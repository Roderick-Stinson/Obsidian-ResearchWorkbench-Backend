"""经典与必读、领域发展史：Semantic Scholar 和大模型都用假的。"""
import json
from datetime import datetime, timedelta

import pytest

from pwb import landscape, llm
from pwb.tasks import run_task
from pwb.util import TZ

from .conftest import API, assert_contract

TODAY = datetime.now(TZ)


def lib_paper(store, pid, title, topic, days_ago, cites, relevance="core", aliases=()):
    date = (TODAY - timedelta(days=days_ago)).date().isoformat()
    store.upsert_paper({"id": pid, "title": title, "titleZh": title + "（中）", "authors": ["A"], "firstAuthor": "A",
                        "year": int(date[:4]), "venue": "ICML " + date[:4], "publicationStatus": "已发表",
                        "versions": [{"id": pid, "kind": "proceedings", "label": "ICML", "url": None, "date": date}],
                        "urls": {"source": None, "pdf": None, "code": None}, "pdfSha256": None,
                        "citations": {"count": cites, "updatedAt": None}, "metadataStatus": "部分核实", "summary": "做了 " + title,
                        "matchedKeywords": [], "topics": [topic], "libraryRelevance": relevance, "libraryReason": "",
                        "abstract": "We evaluate " + title + " on GSM8K and HumanEval benchmarks."}, list(aliases))


def setup(store, monkeypatch):
    store.set_kv("interests", {"generatedAt": "2026-10-01T00:00:00+08:00", "overview": "", "basis": {"ownPapers": 0, "watchedPapers": 0, "relevantPapers": 0},
                               "stale": False, "candidates": [], "domains": [{"id": "kw-1", "label": "架构搜索", "summary": "", "origin": "user",
                                                                             "pinned": False, "evidence": [], "children": []}]})
    lib_paper(store, "arxiv-2502.00001", "Agent Supernet", "架构搜索", 30, 200, aliases=())
    lib_paper(store, "arxiv-2501.00002", "Topology Search", "架构搜索", 60, 50)
    lib_paper(store, "arxiv-2401.00003", "Old Explainer", "图模型可解释性", 500, 80)
    lib_paper(store, "arxiv-2402.00004", "Side Paper", "架构搜索", 400, 5, relevance="related")
    react = {"paperId": "react", "title": "ReAct: Synergizing Reasoning and Acting", "year": 2022, "venue": "International Conference on Learning Representations",
             "publicationDate": "2022-10-06", "citationCount": 5000, "externalIds": {"ArXiv": "2210.03629"}}
    supernet = {"paperId": "sn", "title": "Agent Supernet", "year": 2025, "venue": "ICML", "publicationDate": "2025-02-01",
                "citationCount": 200, "externalIds": {"ArXiv": "2502.00001"}}
    refs_of = {"ARXIV:2502.00001": [react], "ARXIV:2501.00002": [react, supernet], "ARXIV:2401.00003": [react], "ARXIV:2402.00004": [react, supernet]}
    monkeypatch.setattr(landscape, "_s2_post", lambda ids, log, tries=25: [{"references": refs_of.get(i, [])} for i in ids])
    monkeypatch.setattr(landscape.time, "sleep", lambda s: None)

    def fake(settings, config, system, prompt, schema):
        props = schema["properties"]
        if "startHere" in props:
            return {"items": [{"i": 0, "keep": True, "route": landscape.GENERAL, "role": "奠基", "why": "推理与行动结合的基础范式"},
                              {"i": 1, "keep": True, "route": "架构搜索", "role": "代表", "why": "超网采样"}],
                    "routes": [{"name": "架构搜索", "intro": "自动搜索多智能体架构"}], "startHere": [{"i": 0, "why": "先读它"}, {"i": 99, "why": "越界的丢掉"}]}
        if "benchmarks" in props:
            return {"benchmarks": [{"name": "GSM8K", "aliases": ["GSM-8K"]}, {"name": "HumanEval", "aliases": []}, {"name": "NotThere", "aliases": []}]}
        if "stages" in props:
            return {"intro": "从提示工程走向自动架构搜索。", "stages": [
                {"period": "2022–2023", "title": "推理范式", "description": "提示工程。", "milestones": [{"ref": "C0", "text": "推理行动结合"}, {"ref": "C99", "text": "编造的引用"}]},
                {"period": "2024–2026", "title": "自动搜索", "description": "架构搜索。", "milestones": [{"ref": "P2", "text": "超网"}]}],
                "gaps": [{"title": "失败归因少", "kind": "gap", "metric": "只有 C1 一篇", "note": "很少", "refs": ["P2", "X9"]}]}
        # 必读：每个年份分段调用一次，逐篇判断；每段只留第一篇
        return {"items": [{"i": 0, "keep": True, "why": "【2026】核心方法"}, {"i": 1, "keep": False, "why": ""}, {"i": 9, "keep": True, "why": "越界"}]}
    monkeypatch.setattr(llm, "complete_json", fake)


def test_classics_and_map(client, store, monkeypatch):
    setup(store, monkeypatch)
    task = client.post(API + "/tasks", json={"type": "rebuild-map", "params": {}}).json()
    run_task(store, store.claim_next_task())
    done = assert_contract(client.get(API + "/tasks/" + task["id"]).json(), "task")
    assert done["status"] == "done", done

    classics = assert_contract(client.get(API + "/classics").json(), "classics")
    assert classics["rules"]["libraryPapers"] == 4
    names = [r["name"] for r in classics["routes"]]
    assert names == ["架构搜索", landscape.GENERAL], "路线按关键词树顺序，通用基础放最后"
    react = classics["routes"][1]["items"][0]
    assert react["paper"]["id"] is None and react["paper"]["url"] == "https://arxiv.org/abs/2210.03629"
    assert react["evidence"]["libraryCitations"] == 4 and react["date"] == "2022-10" and react["venue"] == "ICLR"
    sn = classics["routes"][0]["items"][0]
    assert sn["paper"]["id"] == "arxiv-2502.00001", "被引的论文就在论文库里时，指向论文库里的那篇"
    assert len(classics["startHere"]) == 1, "越界编号丢掉"
    assert [m["paper"]["id"] for m in classics["mustRead"]] == ["arxiv-2502.00001", "arxiv-2401.00003"], "每段逐篇判断，keep=false 和越界编号都不要"
    assert classics["mustRead"][0]["why"] == "核心方法", "理由开头抄来的分段标签去掉"
    assert [m["period"] for m in classics["mustRead"]] == [landscape.must_read_bands(TODAY.year)[0][0], landscape.must_read_bands(TODAY.year)[1][0]] \
        or TODAY.month < 3, "必读按发表年份分段，每段单独挑，新作不会被老论文挤掉"
    assert classics["mustRead"][0]["venueTier"] == "CCF-A 会议" and classics["mustRead"][0]["evidence"]["libraryCitations"] == 2

    m = assert_contract(client.get(API + "/map").json(), "map")
    assert [len(s["milestones"]) for s in m["stages"]] == [1, 1], "编造的编号对不上就丢掉"
    assert m["gaps"][0]["sources"] and len(m["gaps"][0]["sources"]) == 1
    assert "《ReAct" in m["gaps"][0]["metric"] and "C1" not in m["gaps"][0]["metric"], "文字里的内部编号换成论文标题"
    benches = {b["name"]: b for b in m["benchmarksAndBaselines"] if b["kind"] == "benchmark"}
    assert benches["GSM8K"]["usedByCount"] == 4 and "NotThere" not in benches, "评测集按摘要实际计数，提不到的不列"
    trend = {t["direction"]: t for t in m["trends"]}
    assert trend["架构搜索"]["paperCount"] == 2 and trend["架构搜索"]["trend"] == "up"
    assert {c["name"]: c["paperCount"] for c in m["categories"]}["架构搜索"] == 3


def test_map_needs_classics_and_refs(client, store, monkeypatch):
    task = client.post(API + "/tasks", json={"type": "rebuild-map", "params": {}}).json()
    monkeypatch.setattr(landscape, "_s2_post", lambda ids, log, tries=25: [])
    run_task(store, store.claim_next_task())
    failed = client.get(API + "/tasks/" + task["id"]).json()
    assert failed["status"] == "failed" and "参考文献" in failed["error"]


def test_hotspots_order_by_onset(store, monkeypatch):
    """三个说法先后冒出来：harness → skills → memory；热点按起势时间排序。"""
    from pwb import feed, hotspots, profile
    profile.path(store).write_text(json.dumps({"streams": [{"key": "agent", "name": "智能体", "query": "abs:agent"}]}), encoding="utf-8")
    feed.init(store)
    months = feed.months("2025-01", "2025-12")
    filler = "We study planning with tool calls and evaluate reasoning benchmarks for web navigation."
    rows = []
    for k, m in enumerate(months):
        for j in range(100):
            text = filler
            if k >= 3 and j < 20:
                text += " An agent harness wraps the model."
            if k >= 6 and 20 <= j < 45:
                text += " Agent skills are loaded on demand."
            if k >= 9 and 45 <= j < 75:
                text += " Agent memory stores past episodes."
            rows.append(("agent", "{}.{:05d}".format(m.replace("-", "")[2:], j), m, m + "-15", "Paper " + str(j) + " agent", text, '["cs.AI"]'))
    rows.append(("agent", "2601.00001", "2026-01", "2026-01-02", "Current month agent", filler, '["cs.AI"]'))
    rows.append(("other", "2501.99999", "2025-01", "2025-01-15", "Other stream agent harness", filler, '["cs.AI"]'))
    with store.conn() as c:
        c.executemany("INSERT INTO arxiv_feed VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    seen = {}

    def fake(settings, config, system, prompt, schema):
        seen["prompt"] = prompt
        return {"hotspots": [{"name": "记忆（Memory）", "summary": "", "terms": ["agent memory"]},
                             {"name": "Agent Skills", "summary": "", "terms": ["agent skills", "编造的词组"]},
                             {"name": "Harness", "summary": "", "terms": ["agent harness"]}]}
    monkeypatch.setattr(llm, "complete_json", fake)
    hot = hotspots.build(store, {"engine": "claude-code"}, profile.streams(store)[0], log=lambda m: None)
    assert hot["months"] == months, "当月没过完，不算"
    assert "agent harness" in seen["prompt"] and "evaluate reasoning" not in seen["prompt"], "一直都有的词组不算热点"
    assert [h["name"] for h in hot["items"]] == ["Harness", "Agent Skills", "记忆（Memory）"]
    assert [h["onset"] for h in hot["items"]] == ["2025-04", "2025-07", "2025-10"]
    assert hot["items"][1]["terms"] == ["agent skills"], "编造的词组丢掉"
    assert hot["items"][2]["counts"][-1] == 30 and hot["items"][2]["recentSharePct"] == 30.0
    assert "智能体方向（检索词 abs:agent）" in seen["prompt"] and "\nagent |" not in seen["prompt"], "检索词本身不算热点"
    assert "abs:agent" in hot["source"] and (hot["stream"], hot["name"]) == ("agent", "智能体")
    assert hot["totals"][0] == 100, "别的流的论文不算进来"
    assert [name for name, _ in hotspots.current(hot)] == ["Harness", "Agent Skills", "记忆（Memory）"], "三个都还在峰值"
    assert dict(hotspots.current(hot))["Agent Skills"] == {"agent skill"}, "词组归一化，和每日推荐里的匹配方式一致"
    faded = {**hot, "items": [{**hot["items"][0], "counts": [0, 0, 0, 30, 30, 30, 30, 30, 30, 0, 0, 0]}]}
    assert hotspots.current(faded) == [], "退下去的热点不算正在热"


def test_feed_streams_and_query_change(store, monkeypatch):
    from pwb import feed, profile
    with pytest.raises(FileNotFoundError, match="streams"):
        feed.fetch(store, since="2026-01")
    queries = []
    monkeypatch.setattr(feed, "fetch_month", lambda store, stream, month, log=print: queries.append((stream["key"], month, stream["query"])) or (0, 0))
    monkeypatch.setattr(feed, "months", lambda since, until=None: ["2026-01", "2026-02", "2026-03"])
    path = profile.path(store)
    path.write_text(json.dumps({"streams": [{"key": "a", "query": "abs:agent"}, {"key": "b", "query": "abs:rag"}]}), encoding="utf-8")
    feed.fetch(store, since="2026-01")
    feed.fetch(store, since="2026-01", only=["a"])
    assert [(k, m) for k, m, _ in queries] == [("a", "2026-01"), ("a", "2026-02"), ("a", "2026-03"), ("b", "2026-01"), ("b", "2026-02"),
                                               ("b", "2026-03"), ("a", "2026-02"), ("a", "2026-03")], "取过的老月份不重取"
    with store.conn() as c:
        c.execute("INSERT INTO arxiv_feed VALUES ('a', '2601.00001', '2026-01', '2026-01-02', 't', 'a', '[]')")
        c.execute("INSERT INTO arxiv_feed VALUES ('b', '2601.00002', '2026-01', '2026-01-02', 't', 'a', '[]')")
    path.write_text(json.dumps({"streams": [{"key": "a", "query": "abs:agents"}, {"key": "b", "query": "abs:rag"}]}), encoding="utf-8")
    queries.clear()
    feed.fetch(store, since="2026-01")
    assert [(k, m) for k, m, _ in queries] == [("a", "2026-01"), ("a", "2026-02"), ("a", "2026-03"), ("b", "2026-02"), ("b", "2026-03")], "只重取改了检索词的流"
    with store.conn() as c:
        assert [r[0] for r in c.execute("SELECT stream FROM arxiv_feed")] == ["b"], "改了检索词的流，旧数据作废"
    assert feed.fetched_since(store) == "2026-01"


def test_feed_migrates_single_stream_table(store):
    from pwb import feed, profile
    with store.conn() as c:
        c.execute("CREATE TABLE arxiv_feed (arxiv_id TEXT PRIMARY KEY, month TEXT NOT NULL, published TEXT NOT NULL, "
                  "title TEXT NOT NULL, abstract TEXT NOT NULL, cats TEXT NOT NULL)")
        c.execute("INSERT INTO arxiv_feed VALUES ('2601.00001', '2026-01', '2026-01-02', 't', 'a', '[]')")
    store.set_kv("feed-months", {"2026-01": {"total": 1, "kept": 1, "at": "x"}})
    profile.path(store).write_text(json.dumps({"streams": [{"key": "agent", "query": "abs:agent"}]}), encoding="utf-8")
    feed.init(store)
    assert list(feed.monthly_docs(store, "agent")) == ["2026-01"]
    assert store.get_kv("feed-streams")["agent"]["months"] == {"2026-01": {"total": 1, "kept": 1, "at": "x"}}
