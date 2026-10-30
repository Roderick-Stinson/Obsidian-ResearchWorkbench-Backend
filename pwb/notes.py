"""阅读笔记（约定 5.18）。vault 里的笔记由面板发过来（后端仍然不读 vault），按标题分好的各节存在 notes 表。

推荐怎么用笔记（面板“设置 → 每日推荐 → 阅读笔记”，小节名也在那里改，和模板保持一致）：
- 种子：精读过、评分高的论文当邻近推荐的种子，新论文引用了它们就推出来；
- 要追的论文：这一节里的 arXiv / OpenReview 链接或论文 ID 加进阅读队列。每个只加一次，你从队列里删掉不会再加回来；
- 阅读要点：每天流水线的“整理阅读笔记”一步，把“和我的研究的关系”“想法与疑问”两节交给大模型整理成几条要点
  （interest：关心的问题、想借鉴的思路；doubt：存疑、不看好的思路），笔记没变就沿用上次的。初筛时要点和关键词树
  一起给大模型看：回应了你关心的问题的新论文，没命中关键词也能进核心推荐，推荐理由里写明是哪篇笔记；
  用了你存疑的思路的，精选分打折；
- 候选关键词：整理要点时顺带提出关键词树里还没有的方向，作为候选由你在“我的研究领域”里采纳或忽略。
只有你自己写的内容参与：面板发送前去掉 %% 注释和模板里没填的占位行。
"""
import hashlib
import re
import secrets

from . import interests, llm, survey
from .util import dumps, now

MAX_NOTES = 80        # 整理要点时最多看最近改过的多少篇
MAX_NOTE_CHARS = 2000  # 每篇交给大模型的字数（两节合计）
MAX_POINTS = 15
MAX_CANDIDATES = 5
DOUBT_FACTOR = 0.5    # 用了存疑思路的论文，精选分里“相关度”一项打几折
OPENREVIEW = re.compile(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_-]+)")
ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|arxiv[:\s-]*|(?<![\w.]))(\d{4}\.\d{4,5})(?:v\d+)?(?![\d])", re.I)
PAPER_ID = re.compile(r"(?<![\w-])([a-z][a-z0-9]*-[A-Za-z0-9._-]*[A-Za-z0-9])")
PROFILE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["summary", "points", "candidates"], "properties": {
    "summary": {"type": "string"},
    "points": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["kind", "text", "notes"], "properties": {
        "kind": {"enum": ["interest", "doubt"]}, "text": {"type": "string"}, "notes": {"type": "array", "items": {"type": "integer"}}}}},
    "candidates": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                              "required": ["parentId", "label", "reason", "notes"], "properties": {
        "parentId": {"type": "string"}, "label": {"type": "string"}, "reason": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "integer"}}}}}}}


# ---------- 小节 ----------

def norm_heading(text):
    """“## 6. Idea 实现细节” 和设置里的 “Idea 实现细节” 算同一节：去掉编号、强调符号和空白，不分大小写。"""
    text = re.sub(r"^\s*(\d+[.、)]|[一二三四五六七八九十]+[、.])\s*", "", text or "")
    return re.sub(r"[\s*_`]+", "", text).lower()


def section(note, name):
    """笔记里某一节的文字（含它下面的小标题）；没有这一节返回空串。"""
    key, out, level = norm_heading(name), [], None
    for s in note["sections"]:
        if level is None:
            if norm_heading(s["heading"]) == key:
                level = s["level"]
                out.append(s["text"])
        elif s["level"] > level:
            out.append("#" * s["level"] + " " + s["heading"] + "\n" + s["text"])
        else:
            break
    return "\n".join(t for t in out if t.strip()).strip()


def is_seed(note, ncfg):
    return bool(ncfg["asSeeds"] and (note.get("status") in ncfg["seedStatuses"]
                                     or (ncfg["seedMinRating"] and (note.get("rating") or 0) >= ncfg["seedMinRating"])))


# ---------- 论文引用 ----------

def refs_in(text):
    """一段文字里的论文：[(论文 ID, 链接或 None)]，按出现顺序去重。认 arXiv / OpenReview 链接、arXiv 编号和论文 ID。"""
    found = []
    for m in OPENREVIEW.finditer(text or ""):
        found.append((m.start(), "openreview-" + m.group(1), "https://openreview.net/forum?id=" + m.group(1)))
    for m in ARXIV.finditer(text or ""):
        found.append((m.start(), "arxiv-" + m.group(1), "https://arxiv.org/abs/" + m.group(1)))
    for m in PAPER_ID.finditer(text or ""):
        pid = m.group(1)
        if pid.startswith("openreview-"):
            found.append((m.start(), pid, "https://openreview.net/forum?id=" + pid[len("openreview-"):]))
        elif not pid.startswith("arxiv-"):  # arxiv- 开头的上面按编号认过了
            found.append((m.start(), pid, None))
    out = {}
    for _, pid, url in sorted(found):
        out.setdefault(pid.lower(), (pid, url))
    return list(out.values())


def resolve(store, pid, url, ensure, origin=None):
    """论文 ID 或链接 → 论文库里的 ID：已有的直接对上；每日推荐里的写进论文表；arXiv / OpenReview 链接按链接收录。
    对不上返回 None。ensure(url, origin) 是接口层的“按链接收录”。"""
    from . import daily
    found = store.resolve(pid) or daily.materialize(store, pid)
    if not found and url:
        found = ensure(url, origin)
    return found


# ---------- 同步 ----------

def sync(store, items, ensure, ncfg):
    """面板发来的全部笔记（整体替换）。返回 notes 返回体，外加这次同步的 ids（笔记里写的 paper_id → 论文 ID，
    面板据此改笔记）和 queued（从“要追的论文”加进队列的）。"""
    rows, ids, skipped = {}, {}, []
    for it in sorted(items, key=lambda x: -x["mtime"]):
        ref = refs_in(it["paperId"])
        pid = resolve(store, *ref[0], ensure) if ref else None
        if not pid:
            skipped.append({"path": it["path"], "mtime": it["mtime"], "reason": "paper_id 对不上论文：" + it["paperId"]})
            continue
        ids[it["paperId"]] = pid
        if pid in rows:
            skipped.append({"path": it["path"], "mtime": it["mtime"], "reason": "和《{}》是同一篇论文的笔记，只用最近改过的那篇".format(rows[pid]["title"])})
            continue
        rows[pid] = {**it, "paperId": pid}
    store.replace_notes(rows.values())
    store.set_kv("notes-sync", {"skipped": skipped, "syncedAt": now()})
    queued = follow(store, ncfg, ensure) if ncfg["followToQueue"] else []
    return {**body(store, ncfg), "sync": {"ids": ids, "queued": queued}}


def follow(store, ncfg, ensure):
    """“要追的论文”里的论文加进阅读队列；已经写了笔记的（读过了）不加。返回加进去的 PaperRef。"""
    notes = store.get_notes()
    noted = {n["paperId"] for n in notes}
    done = set(store.get_kv("note-follow-done") or [])
    queue = store.get_kv("queue") or {"paperIds": [], "updatedAt": None}
    added = []
    for note in notes:
        for pid, url in refs_in(section(note, ncfg["sections"]["follow"])):
            if pid.lower() in done:
                continue
            found = resolve(store, pid, url, ensure, origin="note-follow")
            if not found and not url:
                continue  # 像论文 ID 但对不上的词（比如 multi-agent），以后也不会对上，不记
            done.add(pid.lower())
            if found and found not in noted and found not in queue["paperIds"]:
                queue["paperIds"].append(found)
                added.append({"id": found, "title": store.get_paper(found)["title"]})
    if added:
        store.set_kv("queue", {"paperIds": queue["paperIds"], "updatedAt": now()})
    store.set_kv("note-follow-done", sorted(done))
    return added


def body(store, ncfg):
    """约定里的 notes 返回体。"""
    prof = store.get_kv("reading-profile")
    st = store.get_kv("notes-sync") or {}
    items = [{"paperId": n["paperId"], "path": n["path"], "title": n["title"], "status": n["status"], "rating": n["rating"],
              "mtime": n["mtime"], "sections": [s["heading"] for s in n["sections"]], "seed": is_seed(n, ncfg)} for n in store.get_notes()]
    return {"items": items, "skipped": st.get("skipped", []), "updatedAt": st.get("syncedAt"),
            "profile": {k: prof[k] for k in ("generatedAt", "summary", "points")} if prof else None}


# ---------- 推荐里用 ----------

def seed_ids(store, ncfg):
    return [n["paperId"] for n in store.get_notes() if is_seed(n, ncfg)]


def points(store, ncfg):
    """初筛用的阅读要点；设置里关了或还没整理过返回 []。"""
    prof = store.get_kv("reading-profile") if ncfg["screening"] else None
    return (prof or {}).get("points") or []


def profile_summary(store, ncfg):
    prof = store.get_kv("reading-profile") if ncfg["screening"] else None
    return (prof or {}).get("summary") or ""


def _sources(store, ncfg):
    """整理要点用的笔记：[(论文 ID, 标题, 状态, 评分, 两节的文字)]，只要两节里至少一节写了的。"""
    out = []
    for n in store.get_notes()[:MAX_NOTES]:
        parts = [(name, section(n, name)) for name in (ncfg["sections"]["relation"], ncfg["sections"]["ideas"])]
        text = "\n".join("【{}】{}".format(name, t) for name, t in parts if t)[:MAX_NOTE_CHARS]
        if text:
            paper = store.get_paper(n["paperId"])
            out.append((n["paperId"], paper["title"] if paper else n["title"], n["status"], n["rating"], text))
    return out


def step_notes(store, cfg, date, log, config):
    """每日流水线的“整理阅读笔记”：笔记有变化时重新整理阅读要点和候选关键词。"""
    from .daily import SYSTEM, _tree_lines
    ncfg = cfg["notes"]
    if not (ncfg["screening"] or ncfg["candidates"]):
        return "设置里关闭了笔记参与初筛和候选关键词", 0
    src = _sources(store, ncfg)
    tree = store.get_kv("interests")
    digest = hashlib.sha256(dumps([src, ncfg["candidates"], ncfg["sections"]]).encode()).hexdigest()
    prof = store.get_kv("reading-profile")
    if prof and prof.get("sourceHash") == digest:
        return "笔记没有变化，沿用 {} 整理的要点".format(prof["generatedAt"][:10]), 0
    if not src:
        store.set_kv("reading-profile", None)
        _replace_candidates(store, tree, [], src)
        return "还没有写了“{}”或“{}”的笔记".format(ncfg["sections"]["relation"], ncfg["sections"]["ideas"]), 0
    lines = []
    for i, (_, title, status, rating, text) in enumerate(src):
        meta = "，".join(x for x in (status, "评分 {}".format(rating) if rating else "") if x)
        lines.append("### {}. 《{}》{}\n{}".format(i, title, "（{}）".format(meta) if meta else "", text))
    prompt = [survey._direction_text(store), "",
              "下面是我读过的论文的笔记摘录（每篇只取“{}”“{}”两节，都是我自己写的）。请整理成：".format(ncfg["sections"]["relation"], ncfg["sections"]["ideas"]),
              "- summary：3–5 句中文，概括我读论文时最关心什么、在追哪些问题、对什么存疑；",
              "- points：要点，每条一句话，具体到能拿来判断一篇新论文和我有没有关系。kind=interest 是我关心、想解决的问题或想借鉴的思路；"
              "kind=doubt 是我明确怀疑、不看好或认为已经被证伪的思路。notes 填出处笔记的编号。只写笔记里有的，最多 {} 条；".format(MAX_POINTS)]
    if ncfg["candidates"]:
        prompt += ["- candidates：关键词树里还没有、但笔记里反复出现或明确想追的方向，作为候选关键词：parentId 填它应该挂在哪个一级或二级关键词下"
                   "（方括号里的编号），label 简短，reason 一句话，notes 填出处笔记的编号；没有就空着，最多 {} 个。".format(MAX_CANDIDATES),
                   "", "<关键词树>", *(_tree_lines(tree) or ["（还没有关键词）"]), "</关键词树>"]
    else:
        prompt += ["- candidates：留空。"]
    prompt += ["", "<笔记>", *lines, "</笔记>"]
    log("整理 {} 篇笔记的要点".format(len(src)))
    res = llm.complete_json(store.settings, config, SYSTEM, "\n".join(prompt), PROFILE_SCHEMA)

    def papers(idx):
        return [{"id": src[i][0], "title": src[i][1]} for i in dict.fromkeys(idx or []) if isinstance(i, int) and 0 <= i < len(src)]
    pts = [{"id": "pt-{}".format(i), "kind": p["kind"], "text": p["text"].strip(), "papers": papers(p.get("notes"))}
           for i, p in enumerate(x for x in res.get("points", []) if x.get("text", "").strip())][:MAX_POINTS]
    store.set_kv("reading-profile", {"generatedAt": now(), "sourceHash": digest, "summary": res.get("summary", "").strip(), "points": pts})
    n_cand = _replace_candidates(store, tree, res.get("candidates", []) if ncfg["candidates"] else [], src, papers)
    kinds = [p["kind"] for p in pts]
    return "整理了 {} 篇笔记：关心的问题 {} 条、存疑的思路 {} 条，候选关键词 {} 个".format(
        len(src), kinds.count("interest"), kinds.count("doubt"), n_cand), len(pts)


def _replace_candidates(store, tree, proposed, src, papers=None):
    """笔记提出的候选关键词整体替换上一次笔记提出的；忽略过的、树里已有的不再提。返回留下几个。"""
    if not tree:
        return 0
    nodes = {n["id"]: (n, level) for n, level, _ in interests.walk(tree["domains"])}
    rejected = {(r["label"].strip().lower(), r["parentId"]) for r in store.get_kv("rejected-candidates") or []}
    keep = [c for c in tree.get("candidates", []) if c.get("source") != "notes"]
    taken = {(c["label"].strip().lower(), c["parentId"]) for c in keep}
    added = 0
    for x in proposed[:MAX_CANDIDATES]:
        parent, level = nodes.get(x.get("parentId"), (None, 3))
        label = (x.get("label") or "").strip()
        examples = papers(x.get("notes")) if papers else []
        key = (label.lower(), x.get("parentId"))
        if not parent or level >= 3 or not label or not examples or key in rejected or key in taken \
                or any(ch["label"].strip().lower() == label.lower() for ch in parent.get("children", [])):
            continue
        taken.add(key)
        keep.append({"id": "cand-" + secrets.token_hex(4), "parentId": parent["id"], "level": level + 1, "label": label,
                     "reason": (x.get("reason") or "").strip() or "笔记里提到的方向",
                     "growth": {"paperCount": len(examples), "windowDays": 0, "changePct": 0}, "examples": examples, "source": "notes"})
        added += 1
    if keep != tree.get("candidates", []):
        store.set_kv("interests", {**tree, "candidates": keep})
    return added
