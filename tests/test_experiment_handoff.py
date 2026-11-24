"""Two independent MCP clients and the panel share the same durable run."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient

from pwb.api import create_app

MCP = "/api/v1/mcp"
DESK = "/api/v1/experiment-desk"
PAPER = {"id": "folder:我的论文/P", "kind": "paper", "title": "P", "joined": True}
ARGS = {"run_id": "R-1", "project_id": PAPER["id"], "question": "Q", "config_name": "C",
        "host": "test", "cwd": "/tmp/example", "command": "python run.py", "output_path": "/tmp/example/R-1",
        "metric": "Accuracy (%)", "code_version": "aabbcc", "seed": "0"}
FINISH = {"run_id": "R-1", "status": "completed", "result": "0 of 3", "score": 0,
          "metrics": {"calls": 3}, "evidence_path": "/tmp/example/R-1/results.json"}


def submit(client, body):
    return client.post(DESK + "/changes", json=body)


def panel_change(client, collection, row, before=None):
    return submit(client, {"request_id": str(uuid4()), "changes": [
        {"collection": collection, "after": row, "before": before}]})


def rpc(client, method, **params):
    r = client.post(MCP, json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def tool(client, _name, **args):
    return rpc(client, "tools/call", name=_name, arguments=args)


def test_handoff_after_restart(settings):
    with TestClient(create_app(settings)) as a:
        assert rpc(a, "initialize", protocolVersion="2025-11-25")["capabilities"]["tools"] == {}
        assert tool(a, "list_projects")["structuredContent"]["projects"] == []
        assert tool(a, "start_run", **ARGS)["isError"]
        assert panel_change(a, "entities", PAPER).is_success
        assert not tool(a, "start_run", **ARGS).get("isError")
    with TestClient(create_app(settings)) as b:
        pending = tool(b, "list_runs", status="running")["structuredContent"]["runs"]
        assert [r["id"] for r in pending] == ["R-1"]
        assert tool(b, "get_run", run_id="R-1")["structuredContent"]["config"]["commit"] == "aabbcc"
        assert not tool(b, "finish_run", **FINISH).get("isError")
        assert not tool(b, "finish_run", **FINISH).get("isError")
        assert not tool(b, "start_run", **ARGS).get("isError")
        assert tool(b, "start_run", **{**ARGS, "seed": "2"})["isError"]
        assert tool(b, "finish_run", **{**FINISH, "score": 100})["isError"]
        desk = b.get(DESK).json()
        assert len(desk["runs"]) == len(desk["ideas"]) == len(desk["configs"]) == 1
        run = desk["runs"][0]
        assert run["score"] == 0 and run["ideaId"] == desk["ideas"][0]["id"] and run["status"] == "已完成"
        assert run["evidencePath"] == FINISH["evidence_path"]
        assert b.get(DESK + "/revision").json()["revision"] == 3
        assert b.get(MCP).status_code == 405


def test_parallel_and_offline_replays(client):
    panel_change(client, "entities", PAPER).raise_for_status()
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda n: tool(client, "start_run", **{**ARGS, "run_id": "R-" + str(n)}), range(6)))
    assert all(not r.get("isError") for r in results)
    desk = client.get(DESK).json()
    assert len(desk["runs"]) == 6 and len(desk["ideas"]) == 1 and len(desk["configs"]) == 1
    idea = desk["ideas"][0]
    edit = {"request_id": "offline-1", "changes": [{"collection": "ideas", "before": idea, "after": {**idea, "next": "repeat"}}]}
    submit(client, edit).raise_for_status()
    panel_change(client, "ideas", {**idea, "next": "full"}, {**idea, "next": "repeat"}).raise_for_status()
    submit(client, edit).raise_for_status()
    assert client.get(DESK).json()["ideas"][0]["next"] == "full"
    assert submit(client, {**edit, "request_id": "other"}).status_code == 409
    bad = client.post(MCP, json={}, headers={"origin": "https://other.example"})
    assert bad.status_code == 403


def test_structured_experiment_views(client):
    panel_change(client, "entities", PAPER).raise_for_status()
    assert not tool(client, "update_project_overview", project_id=PAPER["id"],
                    display={"subtitle": "Overview", "scope": "Generic scope"}).get("isError")
    res_idea = tool(client, "upsert_idea", project_id=PAPER["id"], id="claim-1",
                    question="C1 · Does routing help?", kind="claim", step="① Probe",
                    short="Does routing help?", exps=["E1"], needs=[{"exp": "E1", "text": "Run probe"}],
                    order=1)
    assert not res_idea.get("isError")
    assert res_idea["structuredContent"]["idea"]["step"] == "① Probe"
    # Updating existing idea preserves existing fields while merging new ones
    res_idea2 = tool(client, "upsert_idea", project_id=PAPER["id"], id="claim-1",
                     question="C1 · Does routing help?", stage="小样本支持", summary="Not yet")
    assert res_idea2["structuredContent"]["idea"]["step"] == "① Probe"
    assert res_idea2["structuredContent"]["idea"]["stage"] == "小样本支持"

    res_cfg = tool(client, "upsert_config", project_id=PAPER["id"], id="cfg-e1",
                   name="E1 · Probe", exp_code="E1", phase="formal",
                   guide={"purpose": "Test sensitivity", "fixed": "Model", "changed": "Input"})
    assert not res_cfg.get("isError")
    assert res_cfg["structuredContent"]["config"]["expCode"] == "E1"

    assert not tool(client, "start_run", **ARGS).get("isError")
    display_v1 = {"summaryRows": [["Row A", "10%", "baseline", ""]], "audit": "0 missing"}
    assert not tool(client, "finish_run", **{**FINISH, "display": display_v1}).get("isError")
    assert tool(client, "get_run", run_id="R-1")["structuredContent"]["run"]["display"] == display_v1

    display_v2 = {**display_v1, "blocks": [{"kind": "tables", "badge": "Followup", "tabs": [
        {"key": "t1", "label": "Tab 1", "title": "Comparison", "heads": ["A", "B"], "rows": [["1", "2"]]}
    ]}]}
    assert not tool(client, "attach_run_display", run_id="R-1", display=display_v2).get("isError")
    assert tool(client, "get_run", run_id="R-1")["structuredContent"]["run"]["display"] == display_v2

    proj = tool(client, "get_project", project_id=PAPER["id"])["structuredContent"]
    assert proj["project"]["display"]["subtitle"] == "Overview"
    assert len(proj["configs"]) == 2
    assert any(c["id"] == "cfg-e1" for c in proj["configs"])

    # Ownership mismatch is rejected
    other = {"id": "folder:我的论文/Other", "kind": "paper", "title": "Other", "joined": True}
    panel_change(client, "entities", other).raise_for_status()
    assert tool(client, "upsert_idea", project_id=other["id"], id="claim-1", question="X")["isError"]
    assert tool(client, "upsert_config", project_id=other["id"], id="cfg-e1", name="X")["isError"]
