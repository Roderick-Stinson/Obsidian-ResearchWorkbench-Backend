"""SQLite 存储。每次操作开一个连接，接口线程和任务线程互不干扰。"""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from . import contract
from .util import TZ, dumps, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  kind TEXT NOT NULL, key TEXT NOT NULL, body TEXT NOT NULL, generated_at TEXT NOT NULL,
  PRIMARY KEY (kind, key));
CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY, data TEXT NOT NULL, search TEXT NOT NULL, pdf_file TEXT,
  added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_aliases (
  alias TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON UPDATE CASCADE ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS paper_user (
  paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON UPDATE CASCADE ON DELETE CASCADE,
  rating INTEGER NOT NULL DEFAULT 0, flags TEXT NOT NULL DEFAULT '[]', relevance TEXT,
  watched INTEGER NOT NULL DEFAULT 0, own INTEGER NOT NULL DEFAULT 0, discussion_status TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS deep_reads (
  paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON UPDATE CASCADE ON DELETE CASCADE,
  body TEXT NOT NULL, generated_at TEXT NOT NULL);
-- 阅读笔记：vault 里的笔记由面板发过来（后端不读 vault），sections 是按标题分好的各节。
CREATE TABLE IF NOT EXISTS notes (
  paper_id TEXT PRIMARY KEY, path TEXT NOT NULL, title TEXT NOT NULL, status TEXT, rating INTEGER,
  mtime INTEGER NOT NULL, sections TEXT NOT NULL, synced_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS user_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
-- 旧版把 vault 里的本人论文文件传到这里；本人论文现在和其他论文一样存在 papers 表。
DROP TABLE IF EXISTS own_papers;
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, params TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, progress TEXT, message TEXT NOT NULL DEFAULT '',
  outputs TEXT NOT NULL DEFAULT '[]', error TEXT, idempotency_key TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, created_at);
"""

# 有多份的流水线数据，按这个字段区分；其余每种只有一份（key 为空串）。
DOC_KEYS = {
    "brief": lambda b: b["date"],
    "recommendations-day": lambda b: b["date"],
    "report": lambda b: b["id"],
    "results-benchmark": lambda b: b["benchmark"]["key"],
}
# 流水线写入的数据种类（带 generatedAt）。
DOC_KINDS = {"overview", "pipeline", "brief", "recommendations-index", "recommendations-day", "classics", "map",
             "results-index", "results-benchmark", "ideas", "hotspots", "reports-index", "report",
             "experiments", "inbox"}
# 这些来源的论文（每日推荐、笔记里要追的论文）不算进论文库，除非你标了相关 / 重点关注 / 本人论文，或者写了阅读笔记。
HIDDEN_ORIGINS = ("recommendation", "note-follow")
# paper 返回体里由存储层拼出来、不存进 data 的字段。
PAPER_COMPUTED = ("kind", "schemaVersion", "id", "aliases", "hasPdf", "hasDeepRead", "user", "addedAt", "updatedAt")


class Store:
    def __init__(self, settings):
        self.settings = settings
        settings.ensure_dirs()
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.settings.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA journal_mode = WAL")
        try:
            with c:
                yield c
        finally:
            c.close()

    # ---------- 流水线数据 ----------

    def put_doc(self, body):
        """写入一份流水线数据。写入前按约定校验，不合格直接拒绝。"""
        kind = body.get("kind")
        if kind not in DOC_KINDS:
            raise ValueError("不是流水线数据：" + str(kind))
        contract.check(kind, body)
        key = DOC_KEYS[kind](body) if kind in DOC_KEYS else ""
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?)", (kind, key, dumps(body), body["generatedAt"]))
        return key

    def get_doc(self, kind, key=""):
        with self.conn() as c:
            row = c.execute("SELECT body FROM documents WHERE kind = ? AND key = ?", (kind, key)).fetchone()
        return json.loads(row["body"]) if row else None

    def latest_doc(self, kind):
        with self.conn() as c:
            row = c.execute("SELECT body FROM documents WHERE kind = ? ORDER BY key DESC LIMIT 1", (kind,)).fetchone()
        return json.loads(row["body"]) if row else None

    # ---------- 论文 ----------

    def resolve(self, paper_id, c=None):
        """把主 ID 或别名解析成当前主 ID；不存在返回 None（约定 3.2）。"""
        def run(c):
            if c.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone():
                return paper_id
            row = c.execute("SELECT paper_id FROM paper_aliases WHERE alias = ?", (paper_id,)).fetchone()
            return row["paper_id"] if row else None
        if c is not None:
            return run(c)
        with self.conn() as c:
            return run(c)

    def upsert_paper(self, paper, aliases=(), pdf_file=None):
        """新增或更新论文元数据。paper 为 paper 返回体中的字段（不含 id 以外的计算字段）。"""
        pid = paper["id"]
        data = {k: v for k, v in paper.items() if k not in PAPER_COMPUTED}
        search = " ".join([pid, *aliases, data.get("title") or "", data.get("titleZh") or "", data.get("venue") or "", *data.get("authors", [])]).lower()
        ts = now()
        with self.conn() as c:
            existing = c.execute("SELECT pdf_file FROM papers WHERE id = ?", (pid,)).fetchone()
            if existing:
                c.execute("UPDATE papers SET data = ?, search = ?, pdf_file = COALESCE(?, pdf_file), updated_at = ? WHERE id = ?",
                          (dumps(data), search, pdf_file, ts, pid))
            else:
                c.execute("INSERT INTO papers VALUES (?, ?, ?, ?, ?, ?)",
                          (pid, dumps(data), search, pdf_file, paper.get("addedAt") or ts, ts))
            for alias in aliases:
                if alias == pid:
                    continue
                owner = c.execute("SELECT paper_id FROM paper_aliases WHERE alias = ?", (alias,)).fetchone()
                if owner and owner["paper_id"] != pid:
                    raise ValueError("别名 {} 已属于 {}".format(alias, owner["paper_id"]))
                if c.execute("SELECT 1 FROM papers WHERE id = ?", (alias,)).fetchone():
                    raise ValueError("别名 {} 本身是另一篇论文的主 ID".format(alias))
                c.execute("INSERT OR IGNORE INTO paper_aliases VALUES (?, ?)", (alias, pid))
        return pid

    def rename_paper(self, old_id, new_id):
        """中稿后换主 ID：旧 ID 进别名，你的标记、精读、队列、关键词证据随之迁移（约定 3.2）。"""
        with self.conn() as c:
            if not c.execute("SELECT 1 FROM papers WHERE id = ?", (old_id,)).fetchone():
                raise KeyError(old_id)
            owner = self.resolve(new_id, c)
            if owner == old_id:
                # 新 ID 本来就是这篇的别名（例如把 arxiv-… 升为主 ID）：先去掉这条别名。
                c.execute("DELETE FROM paper_aliases WHERE alias = ?", (new_id,))
            elif owner:
                raise ValueError("新 ID 已被占用：" + new_id)
            c.execute("UPDATE papers SET id = ?, updated_at = ? WHERE id = ?", (new_id, now(), old_id))
            c.execute("INSERT INTO paper_aliases VALUES (?, ?)", (old_id, new_id))
            c.execute("UPDATE papers SET search = search || ' ' || ? WHERE id = ?", (new_id.lower(), new_id))
            c.execute("UPDATE notes SET paper_id = ? WHERE paper_id = ?", (new_id, old_id))
            queue = self._kv(c, "queue")
            if queue and old_id in queue["paperIds"]:
                queue["paperIds"] = [new_id if p == old_id else p for p in queue["paperIds"]]
                self._set_kv(c, "queue", queue)
            interests = self._kv(c, "interests")
            if interests:
                text = dumps(interests).replace('"ref":"{}"'.format(old_id), '"ref":"{}"'.format(new_id))
                self._set_kv(c, "interests", json.loads(text))

    def _paper_from_row(self, c, row):
        data = json.loads(row["data"])
        aliases = [r["alias"] for r in c.execute("SELECT alias FROM paper_aliases WHERE paper_id = ? ORDER BY alias", (row["id"],))]
        has_deep = c.execute("SELECT 1 FROM deep_reads WHERE paper_id = ?", (row["id"],)).fetchone() is not None
        return {**data, "id": row["id"], "aliases": aliases, "hasPdf": bool(row["pdf_file"]), "hasDeepRead": has_deep,
                "addedAt": row["added_at"], "updatedAt": row["updated_at"], "user": self._user(c, row["id"])}

    def get_paper(self, paper_id):
        with self.conn() as c:
            pid = self.resolve(paper_id, c)
            if not pid:
                return None
            return self._paper_from_row(c, c.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone())

    SORTS = ("relevance", "newest", "citations", "rating", "added")
    FACETS = ("topic", "venue", "year", "status", "relevance")

    @staticmethod
    def _facet_values(d, user):
        """一篇论文在各分面上的取值（topic 可以有多个）。"""
        return {"topic": d.get("topics") or [], "venue": [re.sub(r"\s*\d{4}$", "", d.get("venue") or "") or "未知"],
                "year": [str(d["year"])] if d.get("year") else ["未知"], "status": [d.get("publicationStatus") or "待核实"],
                "relevance": [d.get("libraryRelevance") or "none"]}

    def list_papers(self, q="", watched=None, offset=0, limit=50, own=None, topic=None, venue=None, year=None,
                    status=None, relevance=None, rating=None, sort="added", with_facets=False):
        """论文库查询：筛选、排序、分页，可选返回分面计数（每个分面按“除它自己以外的筛选条件”计数）。
        论文是几千篇的量级，直接在内存里筛，比拼 SQL 简单。返回 (items, total, facets 或 None)。"""
        with self.conn() as c:
            rows = c.execute("SELECT * FROM papers").fetchall()
            users = {r["paper_id"]: r for r in c.execute("SELECT * FROM paper_user")}
            noted = {r["paper_id"] for r in c.execute("SELECT paper_id FROM notes")}
            papers = []
            for row in rows:
                d = json.loads(row["data"])
                u = self._user(c, row["id"]) if row["id"] in users else {"paperId": row["id"], "rating": 0, "flags": [], "relevance": None,
                                                                        "watched": False, "own": False, "discussionStatus": None, "updatedAt": None}
                papers.append((row, d, u, self._facet_values(d, u)))
        terms = [t for t in (q or "").lower().split() if t]
        chosen = {"topic": topic, "venue": venue, "year": str(year) if year else None, "status": status, "relevance": relevance}

        def basic(row, d, u):
            if d.get("origin") in HIDDEN_ORIGINS and not (u["watched"] or u["own"] or u["relevance"] == "相关" or row["id"] in noted):
                # 每日推荐里你标过、但还没标“相关 / 重点关注 / 本人论文”、也没写笔记的论文：存着你的标记，但不算进论文库。
                return False
            if watched is not None and u["watched"] != watched:
                return False
            if own is not None and u["own"] != own:
                return False
            if rating == "unrated" and u["rating"]:
                return False
            if rating and rating != "unrated" and u["rating"] < int(rating):
                return False
            if terms:
                text = " ".join([row["search"], (d.get("summary") or "").lower(), (d.get("abstract") or "").lower()])
                return all(t in text for t in terms)
            return True

        def facets_ok(fv, skip=None):
            return all(v is None or k == skip or v in fv[k] for k, v in chosen.items())

        base = [p for p in papers if basic(*p[:3])]
        hits = [p for p in base if facets_ok(p[3])]
        rank = {"core": 0, "related": 1}
        keys = {
            "relevance": lambda p: (rank.get(p[1].get("libraryRelevance"), 2), -((p[1].get("citations") or {}).get("count") or 0), -(p[1].get("year") or 0)),
            "newest": lambda p: (-(p[1].get("year") or 0), p[0]["added_at"]),
            "citations": lambda p: (-((p[1].get("citations") or {}).get("count") or -1),),
            "rating": lambda p: (-p[2]["rating"], rank.get(p[1].get("libraryRelevance"), 2)),
        }
        if sort in keys:
            hits.sort(key=keys[sort])
        else:  # 最近加入：加入时间倒序，同一时间按 ID 正序
            hits.sort(key=lambda p: p[0]["id"])
            hits.sort(key=lambda p: p[0]["added_at"], reverse=True)
        items = [{"id": row["id"], "title": d["title"], "titleZh": d.get("titleZh"), "firstAuthor": d.get("firstAuthor", ""),
                  "year": d.get("year"), "venue": d.get("venue", ""), "publicationStatus": d["publicationStatus"],
                  "hasPdf": bool(row["pdf_file"]), "citations": d.get("citations", {"count": None, "updatedAt": None}),
                  "summary": d.get("summary") or "", "urls": d.get("urls") or {"source": None, "pdf": None, "code": None},
                  "topics": d.get("topics") or [], "libraryRelevance": d.get("libraryRelevance"), "user": u}
                 for row, d, u, _ in hits[offset:offset + limit]]
        facets = None
        if with_facets:
            facets = {}
            for name in self.FACETS:
                counts = {}
                for p in base:
                    if facets_ok(p[3], skip=name):
                        for v in p[3][name]:
                            counts[v] = counts.get(v, 0) + 1
                # 年份从新到旧、“未知”放最后；其余按篇数从多到少。
                order = (lambda kv: (kv[0] == "未知", "" if kv[0] == "未知" else str(9999 - int(kv[0])))) if name == "year" else (lambda kv: -kv[1])
                facets[name] = [{"label": k, "count": v} for k, v in sorted(counts.items(), key=order)]
        return items, len(hits), facets

    def library_overview(self):
        week_ago = (datetime.now(TZ) - timedelta(days=7)).isoformat(timespec="seconds")
        with self.conn() as c:
            shown = {r["id"]: r["added_at"] for r in c.execute(
                "SELECT p.id, p.added_at, p.data, u.watched, u.own, u.relevance, n.paper_id AS noted FROM papers p "
                "LEFT JOIN paper_user u ON u.paper_id = p.id LEFT JOIN notes n ON n.paper_id = p.id")
                if json.loads(r["data"]).get("origin") not in HIDDEN_ORIGINS or r["watched"] or r["own"] or r["relevance"] == "相关" or r["noted"]}
            total, last = len(shown), max(shown.values(), default=None)
            relevant = sum(1 for (pid, d) in c.execute("SELECT id, data FROM papers")
                           if pid in shown and json.loads(d).get("libraryRelevance") in ("core", "related"))
            recent = sum(1 for at in shown.values() if at >= week_ago)
        return {"paperTotal": total, "relevantTotal": relevant, "newLast7Days": recent, "lastIngestAt": last}

    def topic_counts(self):
        counts = {}
        with self.conn() as c:
            for row in c.execute("SELECT data FROM papers"):
                for t in json.loads(row["data"]).get("topics") or []:
                    counts[t] = counts.get(t, 0) + 1
        return sorted(({"label": k, "count": v} for k, v in counts.items()), key=lambda x: -x["count"])

    def pdf_path(self, paper_id):
        with self.conn() as c:
            pid = self.resolve(paper_id, c)
            row = c.execute("SELECT pdf_file FROM papers WHERE id = ?", (pid,)).fetchone() if pid else None
        return self.settings.pdf_dir / row["pdf_file"] if row and row["pdf_file"] else None

    def get_deep_read(self, paper_id):
        with self.conn() as c:
            pid = self.resolve(paper_id, c)
            row = c.execute("SELECT body FROM deep_reads WHERE paper_id = ?", (pid,)).fetchone() if pid else None
        return json.loads(row["body"]) if row else None

    def put_deep_read(self, body):
        contract.check("deep-read", body)
        with self.conn() as c:
            pid = self.resolve(body["paperId"], c)
            if not pid:
                raise KeyError(body["paperId"])
            c.execute("INSERT OR REPLACE INTO deep_reads VALUES (?, ?, ?)", (pid, dumps({**body, "paperId": pid}), body["generatedAt"]))

    # ---------- 阅读笔记 ----------

    def get_notes(self):
        """全部笔记，paper_id 换成现在的主 ID（论文中稿换了 ID 也对得上）。"""
        with self.conn() as c:
            rows = c.execute("SELECT * FROM notes ORDER BY mtime DESC").fetchall()
            return [{"paperId": self.resolve(r["paper_id"], c) or r["paper_id"], "path": r["path"], "title": r["title"],
                     "status": r["status"], "rating": r["rating"], "mtime": r["mtime"], "sections": json.loads(r["sections"]),
                     "syncedAt": r["synced_at"]} for r in rows]

    def replace_notes(self, notes):
        """面板每次发来全部笔记：整体替换（vault 里删掉的笔记这里也删掉）。"""
        with self.conn() as c:
            c.execute("DELETE FROM notes")
            for n in notes:
                c.execute("INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (n["paperId"], n["path"], n["title"], n.get("status"),
                          n.get("rating"), n["mtime"], dumps(n["sections"]), now()))

    # ---------- 你的数据 ----------

    @staticmethod
    def _user(c, pid):
        row = c.execute("SELECT * FROM paper_user WHERE paper_id = ?", (pid,)).fetchone()
        if not row:
            return {"paperId": pid, "rating": 0, "flags": [], "relevance": None, "watched": False, "own": False,
                    "discussionStatus": None, "updatedAt": None}
        return {"paperId": pid, "rating": row["rating"], "flags": json.loads(row["flags"]), "relevance": row["relevance"],
                "watched": bool(row["watched"]), "own": bool(row["own"]), "discussionStatus": row["discussion_status"],
                "updatedAt": row["updated_at"]}

    def get_user(self, paper_id):
        """paper_id 不在论文库时返回默认标记，id 原样保留（推荐里可能出现还没收录的论文）。"""
        with self.conn() as c:
            return self._user(c, self.resolve(paper_id, c) or paper_id)

    def patch_user(self, paper_id, patch):
        columns = {"rating": "rating", "flags": "flags", "relevance": "relevance", "watched": "watched",
                   "own": "own", "discussionStatus": "discussion_status"}
        with self.conn() as c:
            pid = self.resolve(paper_id, c)
            if not pid:
                return None
            c.execute("INSERT OR IGNORE INTO paper_user (paper_id) VALUES (?)", (pid,))
            for field, value in patch.items():
                if field == "flags":
                    value = dumps(value)
                elif field in ("watched", "own"):
                    value = int(value)
                c.execute("UPDATE paper_user SET {} = ? WHERE paper_id = ?".format(columns[field]), (value, pid))
            c.execute("UPDATE paper_user SET updated_at = ? WHERE paper_id = ?", (now(), pid))
            if {"relevance", "own", "watched"} & set(patch):
                self._mark_interests_stale(c)
            return self._user(c, pid)

    @staticmethod
    def _kv(c, key):
        row = c.execute("SELECT value FROM user_kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    @staticmethod
    def _set_kv(c, key, value):
        c.execute("INSERT OR REPLACE INTO user_kv VALUES (?, ?, ?)", (key, dumps(value), now()))

    def get_kv(self, key):
        with self.conn() as c:
            return self._kv(c, key)

    def set_kv(self, key, value):
        with self.conn() as c:
            self._set_kv(c, key, value)
        return value

    def _mark_interests_stale(self, c):
        interests = self._kv(c, "interests")
        if interests and not interests.get("stale"):
            interests["stale"] = True
            self._set_kv(c, "interests", interests)

    def set_pdf(self, paper_id, pdf_file, sha256):
        with self.conn() as c:
            # 下载期间论文可能被改了 ID（比如核实中稿后换成正式编号），按别名找到现在的 ID。
            paper_id = self.resolve(paper_id, c) or paper_id
            row = c.execute("SELECT data FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if row is None:
                raise KeyError(paper_id)
            data = {**json.loads(row["data"]), "pdfSha256": sha256}
            c.execute("UPDATE papers SET pdf_file = ?, data = ?, updated_at = ? WHERE id = ?", (pdf_file, dumps(data), now(), paper_id))
            user = self._user(c, paper_id)
            if user["own"] or user["watched"] or user["relevance"] == "相关":
                self._mark_interests_stale(c)

    # ---------- 任务 ----------

    @staticmethod
    def _task(row):
        return {"id": row["id"], "type": row["type"], "params": json.loads(row["params"]), "status": row["status"],
                "createdAt": row["created_at"], "updatedAt": row["updated_at"],
                "progress": json.loads(row["progress"]) if row["progress"] else None, "message": row["message"],
                "outputs": json.loads(row["outputs"]), "error": row["error"]}

    def create_task(self, task_id, task_type, params, idempotency_key=None):
        """同一个 Idempotency-Key 只建一个任务；返回 (任务, 是否新建)。"""
        with self.conn() as c:
            if idempotency_key:
                row = c.execute("SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
                if row:
                    return self._task(row), False
            ts = now()
            c.execute("INSERT INTO tasks (id, type, params, status, created_at, updated_at, idempotency_key) VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                      (task_id, task_type, dumps(params), ts, ts, idempotency_key))
            return self._task(c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()), True

    def get_task(self, task_id):
        with self.conn() as c:
            row = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task(row) if row else None

    def list_tasks(self, active=None, limit=50):
        sql, args = "SELECT * FROM tasks", []
        if active is not None:
            sql += " WHERE status IN ('queued', 'running')" if active else " WHERE status IN ('done', 'failed')"
        with self.conn() as c:
            rows = c.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", args + [limit]).fetchall()
        return [self._task(r) for r in rows]

    def claim_next_task(self):
        with self.conn() as c:
            row = c.execute("SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at, id LIMIT 1").fetchone()
            if not row:
                return None
            c.execute("UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ? AND status = 'queued'", (now(), row["id"]))
            return self._task(c.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone())

    def update_task(self, task_id, **fields):
        columns = {"status": "status", "progress": "progress", "message": "message", "outputs": "outputs", "error": "error"}
        sets, args = ["updated_at = ?"], [now()]
        for field, value in fields.items():
            sets.append(columns[field] + " = ?")
            args.append(dumps(value) if field in ("progress", "outputs") and value is not None else value)
        with self.conn() as c:
            c.execute("UPDATE tasks SET " + ", ".join(sets) + " WHERE id = ?", args + [task_id])

    def fail_interrupted_tasks(self):
        """服务重启时，上次没跑完的任务标为失败。"""
        with self.conn() as c:
            return c.execute("UPDATE tasks SET status = 'failed', error = '服务重启，任务中断', updated_at = ? WHERE status = 'running'",
                             (now(),)).rowcount
