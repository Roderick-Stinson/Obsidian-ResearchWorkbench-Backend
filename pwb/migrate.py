"""启动时把旧版本存下的数据改成现在的格式（每项都可重复执行）。"""
import json

from . import profile
from .store import ref_title
from .util import dumps


def run(store):
    _map_hotspots_list(store)
    _ref_titles_original(store)


def _map_hotspots_list(store):
    """领域发展史的 hotspots 以前是一个对象（只有一个论文流），现在是按论文流的列表。"""
    doc = store.get_doc("map")
    if not doc or not isinstance(doc.get("hotspots"), dict):
        return
    try:
        first = profile.streams(store)[0]
    except (FileNotFoundError, ValueError, IndexError):
        first = {"key": "main", "name": "arXiv"}
    store.put_doc({**doc, "hotspots": [{"stream": first["key"], "name": first["name"], **doc["hotspots"]}]})


def _ref_titles_original(store):
    """论文引用（简报、经典与必读、发展史、候选关键词、阅读要点里的 PaperRef）以前显示中文结论式标题，现在先显示论文原题：
    凡是 {id, title} 里的 title 等于这篇论文的中文标题的，换成原题；简报里的论文另存中文标题到 titleZh。"""
    if store.get_kv("migrated-ref-titles"):
        return
    titles = {}
    with store.conn() as c:
        for r in c.execute("SELECT id, data FROM papers"):
            d = json.loads(r["data"])
            if d.get("titleZh"):
                titles[r["id"]] = (d["titleZh"], ref_title(d, r["id"]))
        try:
            for r in c.execute("SELECT arxiv_id, data, result FROM daily_candidates"):
                zh = (json.loads(r["result"]).get("skim") or {}).get("titleZh")
                en = json.loads(r["data"]).get("title")
                if zh and en:
                    titles.setdefault("arxiv-" + r["arxiv_id"], (zh, en))
        except Exception:  # noqa: BLE001 — 还没有每日推荐的表
            pass

    def fix(x):
        if isinstance(x, list):
            return [fix(v) for v in x]
        if not isinstance(x, dict):
            return x
        x = {k: fix(v) for k, v in x.items()}
        hit = titles.get(x.get("id")) if isinstance(x.get("id"), str) else None
        if hit and x.get("title") == hit[0] and hit[1] != hit[0]:
            x["title"] = hit[1]
            if "publishedAt" in x and "summary" in x:  # 简报里的论文
                x["titleZh"] = hit[0]
        return x
    with store.conn() as c:
        for r in c.execute("SELECT kind, key, body FROM documents").fetchall():
            body = json.loads(r["body"])
            new = fix(body)
            if new != body:
                c.execute("UPDATE documents SET body = ? WHERE kind = ? AND key = ?", (dumps(new), r["kind"], r["key"]))
    for key in ("interests", "reading-profile"):
        v = store.get_kv(key)
        if v and fix(v) != v:
            store.set_kv(key, fix(v))
    store.set_kv("migrated-ref-titles", True)
