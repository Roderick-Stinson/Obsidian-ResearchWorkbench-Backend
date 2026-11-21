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


def tool(client, name, **args):
    return rpc(client, "tools/call", name=name, arguments=args)


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
