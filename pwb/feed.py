"""arXiv 上整个智能体方向的论文流（不限于论文库，也不限于已中稿）：按月取标题和摘要。

用途：热点演变（哪些说法什么时候冒出来、什么时候成为主流）要看全领域的最新论文。论文库只收已中稿、
和关键词树相关的论文，会滞后半年到一年，也看不到关键词树以外的新方向。以后每日流水线也从这里取新论文。

检索词 abs:agent（arXiv 会做词干匹配，agents / agentic 也算），只保留计算机类（cs.*）。
每月的结果存在 arxiv_feed 表；取过的月份记在 user_kv 的 feed-months 里，当月和上个月每次都重新取（还在增加）。
"""
import calendar
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

from .pdfs import _get
from .util import TZ, now

API = "https://export.arxiv.org/api/query?"
QUERY = "abs:agent"
PAGE = 1000
NS = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/", "x": "http://arxiv.org/schemas/atom"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS arxiv_feed (
  arxiv_id TEXT PRIMARY KEY, month TEXT NOT NULL, published TEXT NOT NULL,
  title TEXT NOT NULL, abstract TEXT NOT NULL, cats TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS arxiv_feed_month ON arxiv_feed(month);
"""


def init(store):
    with store.conn() as c:
        c.executescript(SCHEMA)


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
                     "abstract": " ".join(e.findtext("a:summary", "", NS).split()), "cats": cats})
    return total, rows


def fetch_month(store, month, log=print):
    y, m = map(int, month.split("-"))
    last = calendar.monthrange(y, m)[1]
    q = "{} AND submittedDate:[{:04d}{:02d}010000 TO {:04d}{:02d}{:02d}2359]".format(QUERY, y, m, y, m, last)
    start, total, kept = 0, None, 0
    while total is None or start < total:
        url = API + urllib.parse.urlencode({"search_query": q, "start": start, "max_results": PAGE,
                                            "sortBy": "submittedDate", "sortOrder": "ascending"})
        body, _ = _get(url)
        page_total, rows = _parse(body)
        total = page_total if total is None else total
        if not rows:  # arXiv 偶尔返回空页，不再往后翻
            break
        with store.conn() as c:
            for r in rows:
                if any(x.startswith("cs.") for x in r["cats"]):
                    c.execute("INSERT OR REPLACE INTO arxiv_feed VALUES (?, ?, ?, ?, ?, ?)",
                              (r["id"], month, r["published"], r["title"], r["abstract"], json.dumps(r["cats"])))
                    kept += 1
        start += len(rows)
    log("{}：arXiv 共 {} 篇，计算机类 {} 篇".format(month, total, kept))
    return total, kept


def fetch(store, since="2023-07", log=print, refresh=False):
    init(store)
    done = store.get_kv("feed-months") or {}
    todo = months(since)
    fresh = set(todo[-2:])  # 当月和上个月还在增加，每次都重新取
    for month in todo:
        if month in done and month not in fresh and not refresh:
            continue
        total, kept = fetch_month(store, month, log)
        done[month] = {"total": total, "kept": kept, "at": now()}
        store.set_kv("feed-months", done)
    return done


def monthly_docs(store):
    """{月份: [标题 + 摘要, …]}。"""
    init(store)
    out = {}
    with store.conn() as c:
        for r in c.execute("SELECT month, title, abstract FROM arxiv_feed"):
            out.setdefault(r["month"], []).append(r["title"] + ". " + r["abstract"])
    return out
