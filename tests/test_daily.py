"""每日推荐流水线：arXiv、Hugging Face、Semantic Scholar、大模型全部用假的。"""
import json
from datetime import datetime

from pwb import daily, feed, llm, profile
from pwb.tasks import run_task
from pwb.util import TZ

from .conftest import API, assert_contract

DATE = "2026-10-29"
PROFILE = {"direction": ["我的研究方向：多智能体系统的自动设计。"],
           "streams": [{"key": "agent", "name": "智能体", "query": "abs:agent"}, {"key": "interp", "name": "可解释性", "query": "abs:interpretability"}]}
ARXIV = {"agent": [
    {"id": "2610.00001", "published": "2026-10-28", "title": "Core Paper On Architecture Search", "abstract": "We search agent topologies.", "cats": ["cs.AI"], "authors": ["A"]},
    {"id": "2610.00002", "published": "2026-10-28", "title": "Citing Paper", "abstract": "We build a planner.", "cats": ["cs.CL"], "authors": ["B"]},
    {"id": "2610.00003", "published": "2026-10-28", "title": "Unrelated Robot Paper", "abstract": "A robot agent.", "cats": ["cs.RO"], "authors": ["C"]}],
    "interp": [{"id": "2610.00004", "published": "2026-10-28", "title": "Agent Skills Explained", "abstract": "Agent skills are interpretable.", "cats": ["cs.LG"], "authors": ["D"]}]}
HF = [{"id": "2610.00005", "title": "Viral Paper", "abstract": "Everyone likes it.", "authors": ["E"], "published": "2026-10-27",
       "hf": {"upvotes": 120, "github": "https://github.com/x/viral", "date": "2026-10-28"}},
      {"id": "2610.00004", "title": "Agent Skills Explained", "abstract": "Agent skills are interpretable.", "authors": ["D"], "published": "2026-10-28",
       "hf": {"upvotes": 5, "github": None, "date": "2026-10-28"}},
      {"id": "2610.00006", "title": "Agent Skills Again", "abstract": "Agent skills, again.", "authors": ["F"], "published": "2026-10-28",
       "hf": {"upvotes": 1, "github": None, "date": "2026-10-28"}}]


def setup(store, monkeypatch, tree=True):
    profile.path(store).write_text(json.dumps(PROFILE), encoding="utf-8")
    if tree:
        store.set_kv("interests", {"generatedAt": "2026-10-01T00:00:00+08:00", "overview": "", "stale": False, "candidates": [],
                                   "basis": {"ownPapers": 0, "watchedPapers": 0, "relevantPapers": 0},
                                   "domains": [{"id": "kw-1", "label": "架构搜索", "summary": "", "origin": "user", "pinned": False,
                                                "evidence": [], "children": []}]})
    store.upsert_paper({"id": "arxiv-2502.04180", "title": "Multi-agent Architecture Search via Agentic Supernet", "authors": [],
                        "publicationStatus": "已发表", "summary": "", "libraryRelevance": "core"})
    store.patch_user("arxiv-2502.04180", {"watched": True})
    store.upsert_paper({"id": "arxiv-2501.11111", "title": "Some Robot Paper I Hate", "authors": [], "publicationStatus": "预印本", "summary": ""})
    store.patch_user("arxiv-2501.11111", {"relevance": "不相关"})
    fetched = []

    def search(stream, date_range):
        fetched.append((stream["key"], date_range))
        return len(ARXIV[stream["key"]]), [dict(r) for r in ARXIV[stream["key"]] if any(c.startswith(tuple(stream["categories"])) for c in r["cats"])]
    monkeypatch.setattr(feed, "_search", search)
    monkeypatch.setattr(daily, "_hf_day", lambda date: [dict(p) for p in HF] if date == "2026-10-28" else [])

    def s2(ids, fields, log, tries=20):
        if "references" in fields:
            return [{"references": [{"title": "Multi-agent Architecture Search via Agentic Supernet", "externalIds": {"ArXiv": "2502.04180"}}]}
                    if i == "ARXIV:2610.00002" else {"references": []} for i in ids]
        return [None for _ in ids]
    monkeypatch.setattr(daily, "_s2_batch", s2)

    def no_pdf(store, arxiv_id):
        raise RuntimeError("离线")
    monkeypatch.setattr(daily, "_cache_pdf", no_pdf)
    # 正在热的热点：只在“可解释性”流里有 Agent Skills（热点按论文流分开算）。
    monkeypatch.setattr(daily, "_current_hotspots", lambda store: {"interp": [("Agent Skills", {"agent skill"})]})
    prompts = []

    def fake(settings, config, system, prompt, schema):
        prompts.append(prompt)
        props = schema["properties"]
        if "items" in props and "relevant" in props["items"]["items"]["properties"]:
            lines = [l for l in prompt.split("<论文列表>")[1].splitlines() if l.startswith("### ")]
            out = []
            for l in lines:
                i, title = l[4:].split(". ", 1)
                core = title.startswith("Core")
                out.append({"i": int(i), "relevant": core, "keywords": ["kw-1", "kw-made-up"] if core else [], "reason": "理由" + i})
            return {"items": out}
        if "items" in props and "domain" in props["items"]["items"]["properties"]:  # 归研究领域：Viral 不属于任何领域
            lines = [l for l in prompt.split("<论文列表>")[1].splitlines() if l.startswith("### ")]
            return {"items": [{"i": int(l[4:].split(". ", 1)[0]), "domain": "none" if "Viral" in l else "kw-1"} for l in lines]}
        if "items" in props:  # 只看摘要的速读
            n = len([l for l in prompt.split("<论文列表>")[1].splitlines() if l.startswith("### ")])
            return {"items": [{"i": i, "titleZh": "中文标题" + str(i), "summary": "速读。", "quality": 7, "rationale": "扎实",
                               "codeUrl": None} for i in range(n)]}
        if "highlights" in props:
            return {"summary": "今天的简报。", "highlights": [{"title": "要点", "text": "说明。", "refs": [0, 99]}]}
        raise AssertionError(prompt[:200])
    monkeypatch.setattr(llm, "complete_json", fake)
    return fetched, prompts


def test_settings_defaults_and_partial_update(client, store):
    body = assert_contract(client.get(API + "/settings/recommend").json(), "recommend-settings")
    assert body["schedule"]["time"] == "01:00" and body["streams"] == [] and "profile.json" in body["profileProblem"]
    profile.path(store).write_text(json.dumps(PROFILE), encoding="utf-8")
    res = client.put(API + "/settings/recommend", json={"schedule": {"time": "02:30"}, "sources": {"streams": {"interp": False}},
                                                       "pick": {"weights": {"hf": 0.5}}})
    body = assert_contract(res.json(), "recommend-settings")
    assert body["schedule"] == {**daily.DEFAULTS["schedule"], "time": "02:30"}, "只改给了的字段"
    assert body["sources"]["streams"] == {"agent": True, "interp": False}
    assert body["pick"]["weights"] == {**daily.DEFAULTS["pick"]["weights"], "hf": 0.5}
    assert [s["key"] for s in body["streams"]] == ["agent", "interp"] and body["profileProblem"] is None
    assert client.put(API + "/settings/recommend", json={"schedule": {"time": "25:00"}}).status_code == 400
    assert client.put(API + "/settings/recommend", json={"pick": {"max": 99}}).status_code == 400
    assert client.put(API + "/settings/recommend", json={}).status_code == 400


def test_daily_run_end_to_end(client, store, monkeypatch):
    fetched, prompts = setup(store, monkeypatch)
    msg, outputs = daily.run(store, llm.get_config(store), lambda *a: None, date=DATE)
    assert {k for k, _ in fetched} == {"agent", "interp"}
    assert "Some Robot Paper I Hate" in prompts[0], "标过不相关的论文当反例"
    assert "架构搜索 [kw-1]" in prompts[0] and "多智能体系统的自动设计" in prompts[0]

    day = assert_contract(client.get(API + "/recommendations/" + DATE).json(), "recommendations-day")
    cats = {p["id"]: p["category"] for p in day["papers"]}
    assert cats == {"arxiv-2610.00001": "core", "arxiv-2610.00002": "adjacent", "arxiv-2610.00004": "trending",
                    "arxiv-2610.00005": "trending"}, cats
    core = day["papers"][0]
    assert core["matchedKeywords"] == [{"nodeId": "kw-1", "label": "架构搜索"}], "编造的关键词编号丢掉"
    adj = next(p for p in day["papers"] if p["category"] == "adjacent")
    assert "关注的《Multi-agent Architecture Search" in adj["reason"]
    viral = next(p for p in day["papers"] if p["id"] == "arxiv-2610.00005")
    assert viral["hfUpvotes"] == 120 and viral["codeUrl"] == "https://github.com/x/viral" and viral["sources"] == ["Hugging Face"]
    skills = next(p for p in day["papers"] if p["id"] == "arxiv-2610.00004")
    assert skills["sources"] == ["可解释性", "Hugging Face"] and "Agent Skills" in skills["reason"], "热点 + 少量点赞"
    assert "arxiv-2610.00006" not in cats, "只属于热点、几乎没人看的不算"
    assert day["counts"] == {"core": 1, "adjacent": 1, "trending": 2}
    assert all(p["picked"] for p in day["papers"]), "不到 10 篇时全部入选"

    brief = assert_contract(client.get(API + "/brief").json(), "brief")
    assert brief["date"] == DATE and brief["pickedCount"] == 4 and brief["summary"] == "今天的简报。"
    assert [s["id"] for s in brief["highlights"][0]["sources"]] == [day["papers"][0]["id"]], "引用了不存在的编号就去掉"
    index = assert_contract(client.get(API + "/recommendations").json(), "recommendations-index")
    assert index["issues"] == [{"date": DATE, "counts": day["counts"], "pickedCount": 4}]
    pipe = assert_contract(client.get(API + "/pipeline").json(), "pipeline")
    status = {s["key"]: s["status"] for s in pipe["latestRun"]["steps"]}
    assert status["fetch"] == status["classify"] == status["pick"] == "ok" and status["push"] == "skipped"
    assert pipe["latestRun"]["status"] == "ok"

    # 推荐的论文不进论文库；标了“相关”才进。
    assert client.get(API + "/papers").json()["total"] == 2
    assert client.patch(API + "/me/papers/arxiv-2610.00003", json={"rating": 3}).status_code == 404, "没推荐的论文不能标"
    client.patch(API + "/me/papers/arxiv-2610.00001", json={"rating": 4})
    assert client.get(API + "/papers").json()["total"] == 2, "只评了分，还不算进论文库"
    paper = assert_contract(client.get(API + "/papers/arxiv-2610.00001").json(), "paper")
    assert paper["titleZh"] == "中文标题0" and paper["publicationStatus"] == "预印本" and paper["user"]["rating"] == 4
    client.patch(API + "/me/papers/arxiv-2610.00001", json={"relevance": "相关"})
    assert client.get(API + "/papers").json()["total"] == 3
    assert client.put(API + "/me/queue", json={"paperIds": ["arxiv-2610.00005"]}).status_code == 200, "推荐的论文可以加进队列"
    day = client.get(API + "/recommendations/" + DATE).json()
    assert next(p for p in day["papers"] if p["id"] == "arxiv-2610.00001")["user"]["relevance"] == "相关"

    # 再跑一次：已经推荐过的不重复推荐，没有新论文时不再调用大模型。
    prompts.clear()
    daily.run(store, llm.get_config(store), lambda *a: None, date=DATE)
    assert len(client.get(API + "/recommendations/" + DATE).json()["papers"]) == 4
    assert not any("<论文列表>" in p for p in prompts), "没有新论文，不再初筛、速读（只重写简报）"


def test_daily_settings_change_behaviour(client, store, monkeypatch):
    setup(store, monkeypatch, tree=False)
    client.put(API + "/settings/recommend", json={"sources": {"huggingface": False, "streams": {"interp": False}},
                                                 "trending": {"useHotspots": False}, "pick": {"max": 1}, "library": {"autoAdd": ["adjacent"]}})
    daily.run(store, llm.get_config(store), lambda *a: None, date=DATE)
    day = client.get(API + "/recommendations/" + DATE).json()
    assert [p["id"] for p in day["papers"]] == ["arxiv-2610.00002"], "没有关键词树就没有核心；关掉的来源不取"
    assert [p["picked"] for p in day["papers"]] == [True]
    assert client.get(API + "/papers").json()["total"] == 3, "设置里选了自动入库的类别直接进论文库"


def test_trending_daily_cap(client, store, monkeypatch):
    setup(store, monkeypatch)
    client.put(API + "/settings/recommend", json={"trending": {"maxPerDay": 1}})
    msg, _ = daily.run(store, llm.get_config(store), lambda *a: None, date=DATE)
    day = client.get(API + "/recommendations/" + DATE).json()
    assert [p["id"] for p in day["papers"] if p["category"] == "trending"] == ["arxiv-2610.00005"], "超过上限时按点赞取"
    assert "另有 1 篇够热点条件，超过每天上限" in msg


def test_version_check_upgrades_preprint(client, store, monkeypatch):
    setup(store, monkeypatch)
    store.upsert_paper({"id": "arxiv-2501.00042", "title": "Accepted Preprint", "authors": [], "publicationStatus": "待核实", "summary": ""})
    monkeypatch.setattr(daily, "_s2_batch", lambda ids, fields, log, tries=20: [
        {"paperId": "s2x", "title": "Accepted Preprint", "venue": "ICML", "year": 2026,
         "externalIds": {"ArXiv": "2501.00042", "DBLP": "conf/icml/X26"}} if i == "ARXIV:2501.00042" else None for i in ids])
    daily.run(store, llm.get_config(store), lambda *a: None, only="version-check", date=DATE)
    paper = client.get(API + "/papers/arxiv-2501.00042").json()
    assert paper["id"] == "dblp-conf_icml_X26" and paper["publicationStatus"] == "已发表" and paper["venue"] == "ICML 2026"
    daily.run(store, llm.get_config(store), lambda *a: None, only="pick", date=DATE)
    brief = client.get(API + "/brief").json()
    assert brief["accepted"] == [{"paper": {"id": "dblp-conf_icml_X26", "title": "Accepted Preprint"}, "venue": "ICML 2026", "kind": "upgraded"}]


def test_weekly_steps_only_on_weekly_day(store, monkeypatch):
    setup(store, monkeypatch)
    ran = []
    monkeypatch.setattr(daily, "step_new_accepted", lambda store, cfg, date, log, config: ran.append(date) or ("ok", 0))
    monkeypatch.setattr(daily, "step_map", lambda store, cfg, date, log, config: ("ok", 0))
    daily.run(store, llm.get_config(store), lambda *a: None, date="2026-10-29")  # 周四
    assert ran == []
    daily.run(store, llm.get_config(store), lambda *a: None, date="2026-11-02")  # 周一
    assert ran == ["2026-11-02"]
    daily.run(store, llm.get_config(store), lambda *a: None, date="2026-11-02")
    assert ran == ["2026-11-02"], "同一天不重复"
    daily.run(store, llm.get_config(store), lambda *a: None, date="2026-11-10")  # 周二：错过了周一，一周后补做
    assert ran == ["2026-11-02", "2026-11-10"]


def test_scheduler_runs_once_a_day_and_catches_up(client, store):
    s = daily.Scheduler(store, wake=lambda: None)
    at = lambda h, m=0, day=29: datetime(2026, 10, day, h, m, tzinfo=TZ)  # noqa: E731
    assert not s.tick(at(0, 59)), "还没到时间"
    assert s.tick(at(8, 30)), "过了时间（比如 Mac 刚醒）就补跑"
    assert not s.tick(at(9)), "今天已经提交过"
    task = store.claim_next_task()
    assert task["type"] == "run-daily"
    assert not s.tick(at(1, 0, day=30)), "上一次还没跑完就不再提交"
    store.update_task(task["id"], status="done")
    assert s.tick(at(1, 0, day=30))
    store.update_task(store.claim_next_task()["id"], status="done")
    client.put(API + "/settings/recommend", json={"schedule": {"enabled": False}})
    assert not s.tick(at(1, 0, day=31)), "关掉定时就不跑"


def test_run_daily_and_retry_step_tasks(client, store, monkeypatch):
    setup(store, monkeypatch)
    task = client.post(API + "/tasks", json={"type": "run-daily", "params": {}}).json()
    run_task(store, store.claim_next_task())
    done = assert_contract(client.get(API + "/tasks/" + task["id"]).json(), "task")
    assert done["status"] == "done" and "今天推荐 4 篇" in done["message"]
    run_id = client.get(API + "/pipeline").json()["latestRun"]["runId"]
    retry = client.post(API + "/tasks", json={"type": "retry-step", "params": {"runId": run_id, "step": "skim"}}).json()
    run_task(store, store.claim_next_task())
    assert client.get(API + "/tasks/" + retry["id"]).json()["status"] == "done"
    bad = client.post(API + "/tasks", json={"type": "retry-step", "params": {"runId": "daily-1999-01-01", "step": "skim"}}).json()
    run_task(store, store.claim_next_task())
    assert client.get(API + "/tasks/" + bad["id"]).json()["status"] == "failed"


def test_pick_balances_domains_and_caps_outside():
    def cand(i, dom, score):
        return {"id": str(i), "result": {"domain": dom, "score": score}}
    cands = [cand(1, "a", 0.9), cand(2, "a", 0.8), cand(3, "a", 0.7), cand(4, "b", 0.3), cand(5, None, 0.99), cand(6, None, 0.98),
             cand(7, None, 0.97)]
    cfg = {"pick": {"max": 4, "outsideMax": 1}}
    assert daily._pick(cands, cfg) == {"1", "4", "5", "2"}, "每个领域轮流出一篇；领域外的只要 1 篇"


def test_backlog_of_unhandled_picks(client, store, monkeypatch):
    def day(date, papers):
        store.put_doc({"schemaVersion": 1, "kind": "recommendations-day", "generatedAt": DATE + "T01:00:00+08:00", "runId": "daily-" + date,
                       "date": date, "counts": {"core": len(papers), "adjacent": 0, "trending": 0}, "papers": papers})
        daily._update_index(store, date, {"core": len(papers), "adjacent": 0, "trending": 0}, len(papers))

    def rec(i, score, picked=True):
        return {"id": "arxiv-2610.0000" + str(i), "titleZh": "中文" + str(i), "titleEn": "Paper " + str(i), "summary": "s", "publishedAt": "2026-10-01",
                "url": "https://arxiv.org/abs/2610.0000" + str(i), "codeUrl": None, "hfUpvotes": 0, "citations": {"count": None, "updatedAt": None},
                "quality": {"score": 7, "rationale": ""}, "category": "core", "reason": "r", "matchedKeywords": [], "score": score,
                "picked": picked, "user": daily._blank_user("arxiv-2610.0000" + str(i))}
    day("2026-10-10", [rec(9, 0.99)])                      # 太早：超过 14 天
    day("2026-10-27", [rec(1, 0.9), rec(2, 0.5), rec(3, 0.8, picked=False)])
    day("2026-10-28", [rec(4, 0.6), rec(1, 0.9)])
    day("2026-10-29", [rec(5, 0.7)])                       # 最近一期，不算积压
    store.upsert_paper({"id": "arxiv-2610.00002", "title": "Paper 2", "authors": [], "publicationStatus": "预印本", "summary": ""})
    store.patch_user("arxiv-2610.00002", {"flags": ["跳过"]})
    body = assert_contract(client.get(API + "/recommendations/backlog").json(), "recommendations-backlog")
    assert [(p["id"], p["date"]) for p in body["papers"]] == [("arxiv-2610.00001", "2026-10-28"), ("arxiv-2610.00004", "2026-10-28")], \
        "只算精选、没处理过的；同一篇只出现一次；跳过的不算"
    daily.put_settings(store, {"backlog": {"max": 1}})
    body = client.get(API + "/recommendations/backlog").json()
    assert body["total"] == 2 and len(body["papers"]) == 1
