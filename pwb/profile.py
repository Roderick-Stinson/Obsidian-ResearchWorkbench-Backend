"""研究方向配置（data/profile.json，不进版本库；格式见仓库根目录的 profile.example.json）。

和你研究方向有关的文字都放在这里：
- direction    给大模型看的方向说明（领域检索、每日初筛、经典与发展史都用）；
- queries      领域检索入库用的 Semantic Scholar 检索词；
- extraTopics  关键词树以外、筛选时也可以归入的方向；
- streams      arXiv 论文流：每日推荐从这里取新论文，热点演变也按流分别统计；
- venues / venuesExtra  领域检索的会议 / 期刊范围（不写就用内置的 CCF-A 等清单）。

旧版本的文件叫 survey.json，论文流只有一个 arxiv 字段，都照样能读。
"""
import json
import re

from .config import ROOT

EXAMPLE = ROOT / "profile.example.json"
STREAM_KEY = re.compile(r"^[a-z0-9-]+$")


def path(store):
    return store.settings.data_dir / "profile.json"


def _source(store):
    p = path(store)
    if p.exists():
        return p
    legacy = store.settings.data_dir / "survey.json"
    return legacy if legacy.exists() else None


def load(store, required=False):
    """归一化后的配置：{direction, queries, extraTopics, streams, venues, venuesExtra}。没配置时 required 则报错，否则返回空的。"""
    src = _source(store)
    if src is None:
        if required:
            raise FileNotFoundError("还没有研究方向配置：把 {} 复制成 {}，再按你的方向改".format(EXAMPLE, path(store)))
        raw = {}
    else:
        raw = json.loads(src.read_text(encoding="utf-8"))
    streams = raw.get("streams")
    if streams is None and raw.get("arxiv"):
        old = raw["arxiv"]
        streams = [{"key": "main", "name": old.get("field") or old.get("query", ""), "query": old.get("query", ""),
                    "categories": old.get("categories")}]
    out = []
    for s in streams or []:
        if not s.get("query"):
            continue
        key = s.get("key") or "main"
        if not STREAM_KEY.match(key):
            raise ValueError("论文流的 key 只能用小写字母、数字和 -：" + key)
        out.append({"key": key, "name": s.get("name") or s["query"], "query": s["query"],
                    "categories": s.get("categories") or ["cs."]})
    if len({s["key"] for s in out}) != len(out):
        raise ValueError("profile.json 里有重复的论文流 key")
    return {"direction": list(raw.get("direction") or []), "queries": list(raw.get("queries") or []),
            "extraTopics": list(raw.get("extraTopics") or []), "streams": out,
            "venues": raw.get("venues"), "venuesExtra": dict(raw.get("venuesExtra") or {})}


def streams(store):
    """arXiv 论文流列表；一个都没配置时报错（每日抓取和热点演变都要用）。"""
    s = load(store)["streams"]
    if not s:
        raise FileNotFoundError("还没配置 arXiv 论文流：在 {} 里写上 streams（格式见 {}）".format(path(store), EXAMPLE.name))
    return s
