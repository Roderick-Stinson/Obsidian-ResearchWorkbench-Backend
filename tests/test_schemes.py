"""分类方案：大模型用假的。"""
from pwb import llm, schemes
from pwb.tasks import run_task

from .conftest import API, assert_contract
from .test_authors import full

TOPIC = "多智能体架构的编排优化"


def seed(store):
    rows = [("arxiv-2501.00001", "GNN Topology Generator", 2024, [TOPIC]), ("arxiv-2501.00002", "LLM Writes Agent Teams", 2025, [TOPIC]),
            ("arxiv-2501.00003", "Prompting a Meta Agent", 2025, [TOPIC]), ("arxiv-2501.00004", "A Survey of Agents", 2025, [TOPIC]),
            ("arxiv-2501.00005", "Sparse Autoencoders", 2025, ["LLM 可解释性"])]
    for pid, title, year, topics in rows:
        store.upsert_paper(full(id=pid, title=title, authors=[], year=year, publicationStatus="预印本", summary="s", topics=topics,
                                abstract="Abstract of " + title))


def fake_llm(prompts):
    def fake(settings, config, system, prompt, schema):
        prompts.append(prompt)
        if schema is schemes.SUGGEST_SCHEMA:
            return {"items": [{"name": "生成粒度", "rationale": "看逐步和一次性", "multi": False,
                               "categories": [{"name": "逐步", "description": "一步一个节点", "estimate": 2},
                                              {"name": "一次性", "description": "一次给出", "estimate": 2}]},
                              {"name": "只有一类", "rationale": "", "multi": False, "categories": [{"name": "x", "description": "", "estimate": 1}]}]}
        lines = [l for l in prompt.split("<论文列表>")[1].splitlines() if l.startswith("### ")]
        out = []
        for l in lines:
            i, title = l[4:].split(". ", 1)
            cats = ["1"] if "GNN" in title else ["2"] if ("LLM" in title or "Prompting" in title) else []
            out.append({"i": int(i), "categories": cats, "reason": "因为" + title})
        return {"items": out}
    return fake


def test_scheme_lifecycle(client, store, monkeypatch):
    seed(store)
    prompts = []
    monkeypatch.setattr(llm, "complete_json", fake_llm(prompts))
    body = {"name": "MAS 的生成方式", "scope": {"kind": "topic", "value": TOPIC},
            "categories": [{"name": "用神经网络生成 MAS", "description": "训练网络产生架构"}, {"name": "用 LLM 生成 MAS", "description": "LLM 直接写"}]}
    created = assert_contract(client.post(API + "/schemes", json=body).json(), "scheme")
    sid = created["scheme"]["id"]
    assert created["scheme"]["paperCount"] == 4 and created["scheme"]["pendingCount"] == 4, "只算这个方向的论文"
    nn, lm = [c["id"] for c in created["categories"]]

    task = client.post(API + "/tasks", json={"type": "classify-scheme", "params": {"schemeId": sid}}).json()
    run_task(store, store.claim_next_task())
    assert client.get(API + "/tasks/" + task["id"]).json()["status"] == "done"
    assert "1. 用神经网络生成 MAS：训练网络产生架构" in prompts[0] and "只能选一个" in prompts[0] and "Abstract of GNN" in prompts[0]
    d = assert_contract(client.get(API + "/schemes/" + sid).json(), "scheme")
    assert {c["name"]: c["count"] for c in d["categories"]} == {"用神经网络生成 MAS": 1, "用 LLM 生成 MAS": 2} and d["noneCount"] == 1
    by = {p["paper"]["id"]: p for p in d["papers"]}
    assert by["arxiv-2501.00001"]["categories"] == [nn] and by["arxiv-2501.00001"]["reason"] == "因为GNN Topology Generator"
    assert by["arxiv-2501.00004"]["categories"] == [], "都不属于就是“都不是”"
    assert d["byYear"] == [{"year": 2024, "counts": {nn: 1, lm: 0}, "none": 0}, {"year": 2025, "counts": {nn: 0, lm: 2}, "none": 1}]

    # 手动改：保留；改了类别后大模型分的作废，只有你改过的不用重分
    d = client.put(API + "/schemes/" + sid + "/papers/arxiv-2501.00004", json={"categories": [nn]}).json()
    assert {p["paper"]["id"]: p["source"] for p in d["papers"]}["arxiv-2501.00004"] == "user"
    assert client.put(API + "/schemes/" + sid + "/papers/arxiv-2501.00004", json={"categories": [nn, lm]}).status_code == 409, "单选"
    d = client.patch(API + "/schemes/" + sid, json={"categories": [{"id": nn, "name": "神经网络生成", "description": "改了说明"},
                                                                   {"id": lm, "name": "用 LLM 生成 MAS", "description": "LLM 直接写"}]}).json()
    assert d["scheme"]["pendingCount"] == 3 and {p["paper"]["id"]: p["categories"] for p in d["papers"]}["arxiv-2501.00004"] == [nn]
    n = len(prompts)
    client.post(API + "/tasks", json={"type": "classify-scheme", "params": {"schemeId": sid}})
    run_task(store, store.claim_next_task())
    assert len(prompts) == n + 1 and "A Survey of Agents" not in prompts[-1], "你改过的不再交给大模型"
    assert client.get(API + "/schemes/" + sid).json()["scheme"]["pendingCount"] == 0

    lst = assert_contract(client.get(API + "/schemes").json(), "schemes")
    assert [s["name"] for s in lst["items"]] == ["MAS 的生成方式"]
    assert client.delete(API + "/schemes/" + sid).json()["items"] == []
    assert client.get(API + "/schemes/" + sid).status_code == 404


def test_scheme_validation(client, store):
    seed(store)
    one = {"name": "x", "scope": {"kind": "all"}, "categories": [{"name": "只有一类"}]}
    assert client.post(API + "/schemes", json=one).status_code == 400
    dup = {"name": "x", "scope": {"kind": "all"}, "categories": [{"name": "A"}, {"name": "a"}]}
    assert client.post(API + "/schemes", json=dup).status_code == 400, "重名的去掉后只剩一类"
    assert client.post(API + "/schemes", json={"name": "x", "scope": {"kind": "topic"}, "categories": [{"name": "A"}, {"name": "B"}]}).status_code == 400
    assert client.post(API + "/tasks", json={"type": "classify-scheme", "params": {"schemeId": "sch-nope"}}).status_code == 404
    q = client.post(API + "/schemes", json={"name": "x", "scope": {"kind": "query", "value": "agent"}, "multi": True,
                                            "categories": [{"name": "A"}, {"name": "B"}]}).json()
    assert q["scheme"]["paperCount"] == 3 and q["scheme"]["multi"] is True, "搜索词范围（标题和摘要里有 agent 的）"


def test_suggest(client, store, monkeypatch):
    seed(store)
    prompts = []
    monkeypatch.setattr(llm, "complete_json", fake_llm(prompts))
    assert client.get(API + "/schemes/suggestions").status_code == 404
    task = client.post(API + "/tasks", json={"type": "suggest-schemes", "params": {"scope": {"kind": "all"}}}).json()
    run_task(store, store.claim_next_task())
    assert client.get(API + "/tasks/" + task["id"]).json()["message"] == "提出了 1 种分法", "少于两类的不要"
    s = assert_contract(client.get(API + "/schemes/suggestions").json(), "scheme-suggestions")
    assert s["paperCount"] == 5 and s["items"][0]["categories"][0] == {"name": "逐步", "description": "一步一个节点", "estimate": 2}
    assert "GNN Topology Generator：s" in prompts[0]
    small = client.post(API + "/tasks", json={"type": "suggest-schemes", "params": {"scope": {"kind": "query", "value": "agent"}}}).json()
    run_task(store, store.claim_next_task())
    assert "太少" in client.get(API + "/tasks/" + small["id"]).json()["error"]
