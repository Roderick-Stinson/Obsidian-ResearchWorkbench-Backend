import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from pwb.api import create_app
from pwb.config import CONTRACT_DIR, Settings
from pwb.contract import ContractError
from pwb.tasks import run_task

from .conftest import API, assert_contract

MAAS = "openreview-imcyVlzpXh"
MAS_GATE = "arxiv-2610.17613"


def get(client, path, status=200, **kw):
    res = client.get(API + path, **kw)
    assert res.status_code == status, res.text
    return assert_contract(res.json())


def run_queued(store):
    while True:
        task = store.claim_next_task()
        if task is None:
            return
        run_task(store, task)


# ---------- 返回体都符合约定 ----------

@pytest.mark.parametrize("path,kind", [
    ("/health", "health"), ("/overview", "overview"), ("/pipeline", "pipeline"), ("/brief", "brief"),
    ("/brief?date=2026-10-27", "brief"), ("/recommendations", "recommendations-index"),
    ("/recommendations/2026-10-27", "recommendations-day"), ("/papers", "paper-list"),
    ("/papers/" + MAAS, "paper"), ("/papers/" + MAS_GATE + "/deep-read", "deep-read"),
    ("/me/queue", "queue"), ("/me/preferences", "preferences"), ("/me/idea-notes", "idea-notes"),
    ("/papers?own=true", "paper-list"), ("/classics", "classics"), ("/map", "map"), ("/results", "results-index"),
    ("/results/gsm8k", "results-benchmark"), ("/ideas", "ideas"), ("/hotspots", "hotspots"),
    ("/reports", "reports-index"), ("/reports/weekly-2026-10-19", "report"), ("/experiments", "experiments"),
    ("/inbox", "inbox"), ("/tasks", "task-list"),
])
def test_get_endpoints_match_contract(client, seeded, path, kind):
    assert get(client, path)["kind"] == kind


def test_not_generated_is_404_not_empty(client):
    body = get(client, "/map", 404)
    assert body["error"]["code"] == "not_found"
    get(client, "/brief", 404)
    get(client, "/me/interests", 404)


def test_errors_use_contract_format(client, seeded):
    assert get(client, "/nope", 404)["error"]["code"] == "not_found"
    assert get(client, "/papers?limit=0", 400)["error"]["code"] == "invalid_params"
    assert get(client, "/papers?cursor=abc", 400)["error"]["code"] == "invalid_params"
    assert get(client, "/brief?date=27-10-2026", 400)["error"]["code"] == "invalid_params"
    res = client.patch(API + "/me/papers/" + MAAS, content="不是 JSON")
    assert res.status_code == 400 and assert_contract(res.json(), "error")


def test_put_doc_rejects_data_that_breaks_contract(seeded):
    bad = seeded.get_doc("map")
    del bad["intro"]
    with pytest.raises(ContractError):
        seeded.put_doc(bad)


def test_hotspots_filter(client, seeded):
    assert len(get(client, "/hotspots?platform=X / Twitter")["items"]) == 1
    assert get(client, "/hotspots?platform=知乎")["items"] == []


# ---------- 论文 ----------

def test_paper_list_search_watched_and_paging(client, seeded):
    assert get(client, "/papers")["total"] == 4
    assert [p["id"] for p in get(client, "/papers?q=supernet")["items"]] == [MAAS]
    assert get(client, "/papers?q=arxiv-2502.04180")["items"][0]["id"] == MAAS, "别名也能搜到"
    first = get(client, "/papers?limit=3")
    assert len(first["items"]) == 3 and first["nextCursor"] == "3"
    rest = get(client, "/papers?limit=3&cursor=3")
    assert len(rest["items"]) == 1 and rest["nextCursor"] is None
    client.patch(API + "/me/papers/" + MAAS, json={"watched": True})
    assert [p["id"] for p in get(client, "/papers?watched=true")["items"]] == [MAAS]


def test_alias_lookup_returns_canonical_id(client, seeded):
    assert get(client, "/papers/arxiv-2502.04180")["id"] == MAAS
    res = client.post(API + "/papers/resolve", json={"ids": ["arxiv-2502.04180", MAAS, "arxiv-2699.00001"]})
    assert assert_contract(res.json(), "resolve-result")["ids"] == {
        "arxiv-2502.04180": MAAS, MAAS: MAAS, "arxiv-2699.00001": None}


def test_pdf_missing_is_404(client, seeded):
    assert get(client, "/papers/" + MAAS + "/pdf", 404)["error"]["code"] == "not_found"


# ---------- 你的数据 ----------

def test_patch_user_shows_up_in_recommendations(client, seeded):
    res = client.patch(API + "/me/papers/" + MAS_GATE, json={"rating": 4, "flags": ["收藏"], "relevance": "相关"})
    user = assert_contract(res.json(), "paper-user")
    assert user["rating"] == 4 and user["updatedAt"]
    rec = get(client, "/recommendations/2026-10-27")["papers"][0]
    assert rec["id"] == MAS_GATE and rec["user"]["flags"] == ["收藏"] and rec["user"]["relevance"] == "相关"


def test_patch_user_rejects_bad_input(client, seeded):
    assert client.patch(API + "/me/papers/arxiv-2699.00001", json={"rating": 1}).status_code == 404
    for body in ({}, {"flags": ["不相关"]}, {"rating": 9}, {"foo": 1}):
        res = client.patch(API + "/me/papers/" + MAAS, json=body)
        assert res.status_code == 400, body
        assert_contract(res.json(), "error")


def test_queue_resolves_aliases_and_rejects_duplicates(client, seeded):
    res = client.put(API + "/me/queue", json={"paperIds": ["arxiv-2502.04180", MAS_GATE]})
    assert assert_contract(res.json(), "queue")["paperIds"] == [MAAS, MAS_GATE]
    assert client.put(API + "/me/queue", json={"paperIds": ["arxiv-2502.04180", MAAS]}).status_code == 400
    assert client.put(API + "/me/queue", json={"paperIds": ["arxiv-2699.00001"]}).status_code == 400


def test_preferences_and_idea_notes(client, seeded):
    assert assert_contract(client.put(API + "/me/preferences", json={"needCode": True}).json())["needCode"] is True
    notes = client.put(API + "/me/idea-notes/line-conservecut", json={"text": "先确认口径"}).json()
    assert assert_contract(notes)["notes"] == {"line-conservecut": "先确认口径"}
    assert client.put(API + "/me/idea-notes/line-conservecut", json={"text": ""}).json()["notes"] == {}
    assert client.put(API + "/me/idea-notes/第一条", json={"text": "x"}).status_code == 400


def test_rename_paper_moves_user_data(client, seeded):
    client.patch(API + "/me/papers/" + MAS_GATE, json={"rating": 5, "watched": True})
    client.put(API + "/me/queue", json={"paperIds": [MAS_GATE]})
    seeded.rename_paper(MAS_GATE, "aaai-50001")
    paper = get(client, "/papers/" + MAS_GATE)
    assert paper["id"] == "aaai-50001" and MAS_GATE in paper["aliases"]
    assert paper["user"]["rating"] == 5 and paper["user"]["watched"] and paper["hasDeepRead"]
    assert get(client, "/me/queue")["paperIds"] == ["aaai-50001"]
    assert client.post(API + "/papers/resolve", json={"ids": [MAS_GATE]}).json()["ids"] == {MAS_GATE: "aaai-50001"}
    rec = get(client, "/recommendations/2026-10-27")["papers"][0]
    assert rec["user"]["paperId"] == "aaai-50001" and rec["user"]["rating"] == 5


# ---------- 研究兴趣 ----------

def add_kw(client, parent, label, summary=None):
    body = {"label": label} if summary is None else {"label": label, "summary": summary}
    return client.post(API + "/me/interests/nodes/{}/children".format(parent), json=body)


def test_user_keywords_at_every_level(client, seeded):
    get(client, "/me/interests", 404)
    tree = assert_contract(add_kw(client, "root", "多智能体系统", "我关注的大领域").json(), "interests")
    domain = tree["domains"][0]
    assert domain["origin"] == "user" and domain["summary"] == "我关注的大领域" and domain["evidence"] == []
    two = add_kw(client, domain["id"], "架构生成的可解释性").json()["domains"][0]["children"][0]
    three = add_kw(client, two["id"], "生成过程可解释").json()["domains"][0]["children"][0]["children"][0]
    assert two["origin"] == three["origin"] == "user"
    assert add_kw(client, three["id"], "第四级").status_code == 409
    assert add_kw(client, "root", "多智能体系统 ").status_code == 409, "同级重名"
    assert add_kw(client, "kw-nope", "x").status_code == 404
    assert add_kw(client, "root", "").status_code == 400
    tree = assert_contract(client.patch(API + "/me/interests/nodes/" + two["id"], json={"label": "架构优化的可解释性", "summary": "剪枝、效率"}).json())
    assert tree["domains"][0]["children"][0]["label"] == "架构优化的可解释性"
    assert client.patch(API + "/me/interests/nodes/" + two["id"], json={}).status_code == 400
    tree = client.post(API + "/me/interests/nodes/{}/remove".format(domain["id"])).json()
    assert assert_contract(tree)["domains"] == [], "一级也能删，连同下级"


EXTRACTION = {"overview": "关注多智能体架构的自动设计。", "basis": {"ownPapers": 0, "watchedPapers": 1, "relevantPapers": 0},
              "domains": [{"label": "多智能体系统", "summary": "大领域", "evidence": [{"kind": "paper", "ref": "arxiv-2502.04180", "title": "MaAS", "quote": "agentic supernet"}],
                           "children": [{"label": "架构搜索", "summary": "自动搜索", "evidence": [{"kind": "paper", "ref": MAAS, "title": "MaAS", "quote": None}],
                                         "children": [{"label": "超网采样", "summary": "按查询采样", "evidence": [{"kind": "paper", "ref": MAAS, "title": "MaAS", "quote": None}]}]}]}]}


def test_extraction_merges_and_keeps_user_parts(client, seeded, monkeypatch):
    from pwb import extraction
    monkeypatch.setattr(extraction, "check_quotes", lambda store, ex: [])
    d = add_kw(client, "root", "多智能体系统").json()["domains"][0]
    add_kw(client, d["id"], "我的方向")
    tree = assert_contract(extraction.apply_extraction(seeded, json.loads(json.dumps(EXTRACTION))) and get(client, "/me/interests"))
    domain = tree["domains"][0]
    assert domain["origin"] == "user" and domain["id"] == d["id"], "同名一级保留你填写的节点"
    assert [(c["label"], c["origin"]) for c in domain["children"]] == [("我的方向", "user"), ("架构搜索", "extracted")]
    assert tree["basis"]["watchedPapers"] == 1 and tree["stale"] is False
    pinned = domain["children"][1]["children"][0]
    client.post(API + "/me/interests/nodes/{}/pin".format(pinned["id"]))
    second = json.loads(json.dumps(EXTRACTION))
    second["domains"][0]["children"][0]["label"] = "拓扑设计"
    tree = assert_contract(extraction.apply_extraction(seeded, second) and get(client, "/me/interests"))
    labels = [c["label"] for c in tree["domains"][0]["children"]]
    assert labels == ["我的方向", "架构搜索", "拓扑设计"], "固定的节点及其上级保留，其余整理所得被替换"
    assert [c["label"] for c in tree["domains"][0]["children"][1]["children"]] == ["超网采样"]
    target = tree["domains"][0]["children"][2]
    assert target["label"] == "拓扑设计" and target["origin"] == "extracted"
    client.post(API + "/me/interests/nodes/{}/remove".format(target["id"]))
    tree = get(client, "/me/interests") if extraction.apply_extraction(seeded, json.loads(json.dumps(second))) else None
    assert "拓扑设计" not in [c["label"] for c in tree["domains"][0]["children"]], "删掉的整理所得关键词不再加回来"


def test_extraction_rejects_unverifiable_quotes(seeded):
    from pwb import extraction
    bad = json.loads(json.dumps(EXTRACTION))
    with pytest.raises(ValueError, match="没有这篇的 PDF"):
        extraction.apply_extraction(seeded, bad)
    assert seeded.get_kv("interests") is None
    bad["domains"][0]["evidence"][0]["ref"] = "arxiv-2699.00001"
    assert any("不在论文库" in p for p in extraction.check_quotes(seeded, bad))


def test_quote_matching_ignores_pdf_line_breaks(seeded, monkeypatch):
    from pwb import extraction
    monkeypatch.setattr(extraction, "_pdf_text", lambda path, mtime: extraction._normalize("we pro-\npose an agentic\n supernet that samples"))
    monkeypatch.setattr(seeded, "pdf_path", lambda pid: CONTRACT_DIR / "数据格式约定.md")
    ok = json.loads(json.dumps(EXTRACTION))
    ok["domains"][0]["evidence"][0]["quote"] = "We propose an Agentic Supernet"
    assert extraction.check_quotes(seeded, ok) == []
    ok["domains"][0]["evidence"][0]["quote"] = "a supernet we never wrote"
    assert len(extraction.check_quotes(seeded, ok)) == 1


def test_candidate_accept_and_reject(client, seeded):
    example = json.loads((CONTRACT_DIR / "examples/responses/interests.json").read_text(encoding="utf-8"))
    seeded.set_kv("interests", {k: v for k, v in example.items() if k not in ("kind", "schemaVersion", "runId")})
    tree = client.post(API + "/me/interests/candidates/cand-harness/accept").json()
    assert_contract(tree, "interests")
    added = tree["domains"][0]["children"][-1]
    assert added["label"] == "Agent Harness 设计与评测" and added["origin"] == "candidate" and added["evidence"]
    assert tree["candidates"] == []
    assert client.post(API + "/me/interests/candidates/cand-harness/accept").status_code == 404
    leaf = tree["domains"][0]["children"][0]["children"][0]["id"]
    tree = client.post(API + "/me/interests/nodes/{}/remove".format(leaf)).json()
    assert tree["domains"][0]["children"][0]["children"] == []


def test_relevance_marks_interests_stale(client, seeded):
    add_kw(client, "root", "多智能体系统")
    tree = seeded.get_kv("interests")
    seeded.set_kv("interests", {**tree, "stale": False})
    client.patch(API + "/me/papers/" + MAAS, json={"relevance": "相关"})
    assert get(client, "/me/interests")["stale"] is True


def test_add_own_paper_and_upload_pdf(client, seeded, settings):
    res = client.post(API + "/papers", json={"title": "我的会议投稿", "url": "https://openreview.net/forum?id=Xz0wn1Pp3r", "own": True})
    assert res.status_code == 201
    paper = assert_contract(res.json(), "paper")
    assert paper["id"] == "openreview-Xz0wn1Pp3r" and paper["user"]["own"] and not paper["hasPdf"]
    again = client.post(API + "/papers", json={"title": "随便", "url": "https://openreview.net/pdf?id=Xz0wn1Pp3r", "watched": True})
    assert again.status_code == 200 and again.json()["user"]["own"] and again.json()["user"]["watched"], "已有的只加标记"
    assert again.json()["title"] == "我的会议投稿", "已有真实标题的不改"
    seeded.upsert_paper({**seeded.get_paper("openreview-Xz0wn1Pp3r"), "title": "openreview-Xz0wn1Pp3r"})
    fixed = client.post(API + "/papers", json={"title": "My Method", "url": "https://openreview.net/forum?id=Xz0wn1Pp3r"}).json()
    assert fixed["title"] == "My Method", "标题还是 ID 的用你填的补上"
    local = client.post(API + "/papers", json={"title": "还没公开的稿子", "own": True}).json()
    assert local["id"].startswith("local-") and local["versions"] == []
    assert [p["id"] for p in get(client, "/papers?own=true")["items"]] == [local["id"], "openreview-Xz0wn1Pp3r"]
    assert client.post(API + "/papers", json={"title": ""}).status_code == 400

    assert client.put(API + "/papers/openreview-Xz0wn1Pp3r/pdf", content=b"not a pdf").status_code == 400
    assert client.put(API + "/papers/arxiv-2699.00001/pdf", content=b"%PDF-1.4").status_code == 404
    body = assert_contract(client.put(API + "/papers/openreview-Xz0wn1Pp3r/pdf", content=b"%PDF-1.4 mine").json(), "paper")
    assert body["hasPdf"] and body["pdfSha256"] == hashlib.sha256(b"%PDF-1.4 mine").hexdigest()
    assert client.get(API + "/papers/openreview-Xz0wn1Pp3r/pdf").content == b"%PDF-1.4 mine"
    client.put(API + "/papers/openreview-Xz0wn1Pp3r/pdf", content=b"%PDF-1.4 v2")
    assert client.get(API + "/papers/openreview-Xz0wn1Pp3r/pdf").content == b"%PDF-1.4 v2", "再传就替换"


def test_patch_paper_metadata(client, seeded):
    pid = client.post(API + "/papers", json={"title": "x", "url": "https://openreview.net/forum?id=Xz0wn2Pp3r", "own": True}).json()["id"]
    body = assert_contract(client.patch(API + "/papers/" + pid, json={"title": " 真正的标题 ", "venue": "某会议 2026 投稿", "year": 2026}).json(), "paper")
    assert (body["title"], body["venue"], body["year"]) == ("真正的标题", "某会议 2026 投稿", 2026) and body["user"]["own"]
    assert get(client, "/papers?q=真正的标题")["items"][0]["id"] == pid, "改过的标题能搜到"
    assert client.patch(API + "/papers/" + pid, json={}).status_code == 400
    assert client.patch(API + "/papers/" + pid, json={"title": ""}).status_code == 400
    assert client.patch(API + "/papers/arxiv-2699.00001", json={"title": "x"}).status_code == 404


def test_pdf_upload_fills_unknown_title(client, seeded, monkeypatch):
    from pwb import pdfmeta
    monkeypatch.setattr(pdfmeta, "guess_title", lambda path: "Sparse Feedback Is Enough")
    seeded.upsert_paper({"id": "openreview-Xz0wn2Pp3r", "title": "openreview-Xz0wn2Pp3r", "authors": [], "publicationStatus": "投稿中"})
    body = client.put(API + "/papers/openreview-Xz0wn2Pp3r/pdf", content=b"%PDF-1.4").json()
    assert body["title"] == "Sparse Feedback Is Enough"
    client.patch(API + "/papers/openreview-Xz0wn2Pp3r", json={"title": "我改过的标题"})
    body = client.put(API + "/papers/openreview-Xz0wn2Pp3r/pdf", content=b"%PDF-1.4 v2").json()
    assert body["title"] == "我改过的标题", "已有标题时不覆盖"


def test_guess_title_from_first_page():
    from pwb.pdfmeta import guess_title_from_text
    first = "Shared Plans Are Not Free: A Cost-Aware Study of Tool\nCoordination for Language Model Agent Teams\nAnonymous submission\nAbstract\n..."
    assert guess_title_from_text(first) == "Shared Plans Are Not Free: A Cost-Aware Study of Tool Coordination for Language Model Agent Teams"
    second = "Sparse Feedback Is Enough for Multi-Agent\nWorkflow Tuning and Evaluation\nAnonymous Author(s)\nAfﬁliation"
    assert guess_title_from_text(second) == "Sparse Feedback Is Enough for Multi-Agent Workflow Tuning and Evaluation"
    arxiv = "arXiv:2502.04180v2  [cs.LG]  9 Jun 2025\nMulti-agent Architecture Search via Agentic Supernet\nGuibin Zhang * 1 2 Luyang Niu * 2"
    assert guess_title_from_text(arxiv) == "Multi-agent Architecture Search via Agentic Supernet"
    iclr = "Under review as a conference paper at ICLR 2027\nEVOSTEER: ONLINE SELF-EVOLVING GRAPH\nORCHESTRATION\nMingda Zhang1, Hanwen Zhang2"
    assert guess_title_from_text(iclr) == "EVOSTEER: ONLINE SELF-EVOLVING GRAPH ORCHESTRATION"
    assert guess_title_from_text("Abstract\nshort") is None


def test_pdf_upload_marks_interests_stale(client, seeded):
    add_kw(client, "root", "多智能体系统")
    seeded.set_kv("interests", {**seeded.get_kv("interests"), "stale": False})
    pid = client.post(API + "/papers", json={"title": "我的稿子", "own": True}).json()["id"]
    seeded.set_kv("interests", {**seeded.get_kv("interests"), "stale": False})
    client.put(API + "/papers/" + pid + "/pdf", content=b"%PDF-1.4")
    assert get(client, "/me/interests")["stale"] is True


# ---------- 任务 ----------

def test_task_idempotency_and_not_implemented(client, seeded):
    headers = {"Idempotency-Key": "k1"}
    first = client.post(API + "/tasks", json={"type": "deep-read", "params": {"paperId": "arxiv-2502.04180"}}, headers=headers)
    assert first.status_code == 202
    task = assert_contract(first.json(), "task")
    assert task["params"]["paperId"] == MAAS, "别名换成主 ID"
    second = client.post(API + "/tasks", json={"type": "deep-read", "params": {"paperId": MAAS}}, headers=headers)
    assert second.status_code == 200 and second.json()["id"] == task["id"]
    run_queued(seeded)
    done = get(client, "/tasks/" + task["id"])
    assert done["status"] == "failed" and "尚未实现" in done["error"]
    assert get(client, "/tasks?active=true")["items"] == []


def test_task_bad_params_are_400(client, seeded):
    for body in ({"type": "deep-read", "params": {"paperId": "arxiv-2699.00001"}},
                 {"type": "deep-read", "params": {}}, {"type": "launch-rocket", "params": {}},
                 {"type": "rebuild-map", "params": {"x": 1}}):
        res = client.post(API + "/tasks", json=body)
        assert res.status_code == 400, body
    assert get(client, "/tasks")["items"] == []


def test_claim_run_task(client, seeded):
    target = {"kind": "paper", "id": "arxiv-2502.04180"}
    ok = client.post(API + "/tasks", json={"type": "claim-run", "params": {"runId": "R20261020-120910-76a", "target": target}}).json()
    bad = client.post(API + "/tasks", json={"type": "claim-run", "params": {"runId": "R-nope", "target": target}}).json()
    run_queued(seeded)
    assert get(client, "/tasks/" + ok["id"])["status"] == "done"
    assert get(client, "/inbox")["runs"][0]["claimedBy"] == {"kind": "paper", "id": MAAS}
    failed = get(client, "/tasks/" + bad["id"])
    assert failed["status"] == "failed" and "R-nope" in failed["error"]


def test_worker_thread_runs_tasks(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", token=""))
    with TestClient(app) as client:
        app.state.store.put_doc(json.loads((CONTRACT_DIR / "examples/responses/inbox.json").read_text(encoding="utf-8")))
        task = client.post(API + "/tasks", json={"type": "claim-run", "params": {
            "runId": "R20261020-120910-76a", "target": {"kind": "experimentNote", "id": "实验记录/a.md"}}}).json()
        for _ in range(50):
            if client.get(API + "/tasks/" + task["id"]).json()["status"] == "done":
                break
            time.sleep(0.1)
        assert client.get(API + "/tasks/" + task["id"]).json()["status"] == "done"


# ---------- 认证 ----------

def test_token_required_when_configured(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", token="s3cret", start_worker=False)))
    res = client.get(API + "/papers")
    assert res.status_code == 401 and assert_contract(res.json(), "error")["error"]["code"] == "unauthorized"
    assert client.get(API + "/papers", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(API + "/papers", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get(API + "/health").status_code == 200


def test_cors_allows_obsidian_and_wraps_auth_errors(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", token="s3cret", start_worker=False)))
    pre = client.options(API + "/papers", headers={"Origin": "app://obsidian.md", "Access-Control-Request-Method": "GET",
                                                   "Access-Control-Request-Headers": "authorization"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "app://obsidian.md"
    denied = client.get(API + "/papers", headers={"Origin": "app://obsidian.md"})
    assert denied.status_code == 401 and denied.headers["access-control-allow-origin"] == "app://obsidian.md"
    other = client.get(API + "/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_overview_is_live_and_health_lists_task_types(client, seeded):
    body = get(client, "/overview")
    assert body["paperTotal"] == 4 and body["relevantTotal"] == 0 and body["lastIngestAt"]
    assert get(client, "/health")["taskTypes"] == ["claim-run", "rebuild-classics", "rebuild-interests", "rebuild-map"]


def test_jobs_report_pdf_download_and_dblp(client, seeded, settings):
    jobs = {j["key"]: j for j in get(client, "/jobs")["items"]}
    assert jobs["pdf-download"]["status"] == "idle" and jobs["dblp-verify"]["status"] == "idle"
    seeded.set_kv("pdf-fetch", {MAAS: {"status": "none"}, MAS_GATE: {"status": "ok"}})
    jobs = {j["key"]: j for j in get(client, "/jobs")["items"]}
    assert jobs["pdf-download"]["status"] == "running" and jobs["pdf-download"]["progress"] == {"done": 0, "total": 4}
    (settings.data_dir / "dblp").mkdir()
    (settings.data_dir / "dblp" / "update.log").write_text("x\n核实完成：主会中稿 3 篇\n", encoding="utf-8")
    jobs = {j["key"]: j for j in get(client, "/jobs")["items"]}
    assert jobs["dblp-verify"]["status"] == "done" and "主会中稿 3 篇" in jobs["dblp-verify"]["message"]
