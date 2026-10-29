"""arXiv 论文流（不限于论文库，也不限于已中稿）：按 profile.json 里的 streams，每个流各取各的标题和摘要。

用途：
- 热点演变（哪些说法什么时候冒出来、什么时候成为主流）要看全领域的最新论文。论文库只收已中稿、
  和关键词树相关的论文，会滞后半年到一年，也看不到关键词树以外的新方向。热点按流分别统计。
- 每日推荐从这里取前一天的新论文（fetch_range）。

每个流有自己的检索词（例如 abs:agent，arXiv 会做词干匹配，agents / agentic 也算）和保留的分类前缀
（默认 cs.，计算机类）。结果存在 arxiv_feed 表（同一篇可以属于几个流）；每个流取过的月份记在 user_kv 的
feed-streams 里，当月和上个月每次都重新取（还在增加）。改了某个流的检索词或分类，这个流之前取的作废、全部重取。
"""
import calendar
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

from . import profile
from .pdfs import _get
from .util import TZ, now

API = "https://export.arxiv.org/api/query?"
PAGE = 1000
NS = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/", "x": "http://arxiv.org/schemas/atom"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS arxiv_feed (
  stream TEXT NOT NULL, arxiv_id TEXT NOT NULL, month TEXT NOT NULL, published TEXT NOT NULL,
  title TEXT NOT NULL, abstract TEXT NOT NULL, cats TEXT NOT NULL, PRIMARY KEY (stream, arxiv_id));
CREATE INDEX IF NOT EXISTS arxiv_feed_month ON arxiv_feed(stream, month);
"""


def _first_stream(store):
    try:
        return profile.streams(store)[0]["key"]
    except (FileNotFoundError, ValueError):
        return "main"


def init(store):
    with store.conn() as c:
        cols = [r["name"] for r in c.execute("PRAGMA table_info(arxiv_feed)")]
        if cols and "stream" not in cols:
            # 旧版本只有一个论文流：已取的数据归到第一个流，不重取。
            key = _first_stream(store)
            c.execute("ALTER TABLE arxiv_feed RENAME TO arxiv_feed_v1")
            c.execute("DROP INDEX IF EXISTS arxiv_feed_month")
            c.executescript(SCHEMA)
            c.execute("INSERT INTO arxiv_feed SELECT ?, arxiv_id, month, published, title, abstract, cats FROM arxiv_feed_v1", (key,))
            c.execute("DROP TABLE arxiv_feed_v1")
        else:
            c.executescript(SCHEMA)
    if store.get_kv("feed-streams") is None and store.get_kv("feed-months"):
        old_query = store.get_kv("feed-query")
        store.set_kv("feed-streams", {_first_stream(store): {**(old_query or {}), "months": store.get_kv("feed-months"), "legacy": old_query is None}})


def months(since, until=None):
    """['2024-01', '2024-02', …]，含首尾。"""
    y, m = map(int, since.split("-"))
    end = until or datetime.now(TZ).strftime("%Y-%m")
    out = []
    while "{:04d}-{:02d}".format(y, m) <= end:
        out.append("{:04d}-{:02d}".format(y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _parse(body):
    root = ET.fromstring(body)
    total = int(root.findtext("o:totalResults", "0", NS))
    rows = []
    for e in root.findall("a:entry", NS):
        m = re.search(r"abs/([0-9]{4}\.[0-9]{4,5})", e.findtext("a:id", "", NS))
        if not m:
            continue
        cats = [c.get("term") for c in e.findall("a:category", NS)]
        rows.append({"id": m.group(1), "published": e.findtext("a:published", "", NS)[:10],
                     "title": " ".join(e.findtext("a:title", "", NS).split()),
                     "abstract": " ".join(e.findtext("a:summary", "", NS).split()), "cats": cats,
                     "authors": [" ".join(a.findtext("a:name", "", NS).split()) for a in e.findall("a:author", NS)]})
    return total, rows


def _search(stream, date_range):
    """按检索词和提交时间段取论文，只保留这个流的分类。date_range 是 arXiv 的 YYYYMMDDHHMM 格式。"""
    q = "({}) AND submittedDate:[{} TO {}]".format(stream["query"], *date_range)
    start, total, kept = 0, None, []
    while total is None or start < total:
        url = API + urllib.parse.urlencode({"search_query": q, "start": start, "max_results": PAGE,
                                            "sortBy": "submittedDate", "sortOrder": "ascending"})
        body, _ = _get(url)
        page_total, rows = _parse(body)
        total = page_total if total is None else total
        if not rows:  # arXiv 偶尔返回空页，不再往后翻
            break
        kept += [r for r in rows if any(x.startswith(tuple(stream["categories"])) for x in r["cats"])]
        start += len(rows)
    return total or 0, kept


def _save(store, stream, rows, month=None):
    with store.conn() as c:
        for r in rows:
            c.execute("INSERT OR REPLACE INTO arxiv_feed VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (stream["key"], r["id"], month or r["published"][:7], r["published"], r["title"], r["abstract"], json.dumps(r["cats"])))


def fetch_month(store, stream, month, log=print):
    y, m = map(int, month.split("-"))
    last = calendar.monthrange(y, m)[1]
    total, rows = _search(stream, ("{:04d}{:02d}010000".format(y, m), "{:04d}{:02d}{:02d}2359".format(y, m, last)))
    _save(store, stream, rows, month)
    log("[{}] {}：arXiv 共 {} 篇，保留 {} 篇（分类 {}）".format(stream["name"], month, total, len(rows), "、".join(stream["categories"])))
    return total, len(rows)


def fetch_range(store, stream, start, end, log=print):
    """每日推荐用：取 start–end（datetime，按 UTC 理解成 arXiv 的提交时间）之间的新论文，也顺带写进论文流。"""
    init(store)
    fmt = "%Y%m%d%H%M"
    total, rows = _search(stream, (start.strftime(fmt), end.strftime(fmt)))
    _save(store, stream, rows)
    log("[{}] {} 到 {}：arXiv 共 {} 篇，保留 {} 篇".format(stream["name"], start.strftime("%m-%d %H:%M"), end.strftime("%m-%d %H:%M"), total, len(rows)))
    return rows


def _state(store, stream, log):
    """这个流取过的月份；检索词或分类改了就作废重取。"""
    all_state = store.get_kv("feed-streams") or {}
    key = {"query": stream["query"], "categories": stream["categories"]}
    st = all_state.get(stream["key"])
    if st and st.get("legacy"):
        # 旧版本没记检索词：认为已取的就是按现在的检索词取的，不重取。
        st = {**key, "months": st.get("months", {})}
    elif not st or {"query": st.get("query"), "categories": st.get("categories")} != key:
        if st:
            log("[{}] 检索词或分类改了，之前取的论文流作废，全部重取".format(stream["name"]))
        with store.conn() as c:
            c.execute("DELETE FROM arxiv_feed WHERE stream = ?", (stream["key"],))
        st = {**key, "months": {}}
    all_state[stream["key"]] = st
    store.set_kv("feed-streams", all_state)
    return st


def fetch(store, since="2023-07", log=print, refresh=False, only=None):
    """按月取每个流（only 给了就只取这几个 key），已取过的老月份跳过。"""
    init(store)
    for stream in profile.streams(store):
        if only and stream["key"] not in only:
            continue
        st = _state(store, stream, log)
        todo = months(since)
        fresh = set(todo[-2:])  # 当月和上个月还在增加，每次都重新取
        for month in todo:
            if month in st["months"] and month not in fresh and not refresh:
                continue
            total, kept = fetch_month(store, stream, month, log)
            st["months"][month] = {"total": total, "kept": kept, "at": now()}
            all_state = store.get_kv("feed-streams") or {}
            all_state[stream["key"]] = st
            store.set_kv("feed-streams", all_state)


def fetched_since(store):
    """各个流里最早取过的月份（重新整理发展史时只补最近的月份用）；还没取过返回 None。"""
    init(store)
    done = [m for st in (store.get_kv("feed-streams") or {}).values() for m in st.get("months", {})]
    return min(done) if done else None


def monthly_docs(store, stream_key):
    """{月份: [标题 + 摘要, …]}。"""
    init(store)
    out = {}
    with store.conn() as c:
        for r in c.execute("SELECT month, title, abstract FROM arxiv_feed WHERE stream = ?", (stream_key,)):
            out.setdefault(r["month"], []).append(r["title"] + ". " + r["abstract"])
    return out
