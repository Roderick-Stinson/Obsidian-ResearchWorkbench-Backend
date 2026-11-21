"""Stateless MCP Streamable HTTP (JSON responses; no SSE or server-initiated messages)."""
import json
import re

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from . import experiment_desk as desk
from .errors import ApiError

VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")


def install(router, store):
    @router.get("/experiment-desk")
    def read_desk():
        return desk.snapshot(store)

    @router.get("/experiment-desk/revision")
    def desk_revision():
        return {"revision": store.get_kv(desk.KEY + ":revision") or 0}

    @router.post("/experiment-desk/changes")
    def edit_desk(body: desk.PanelEdit):
        return desk.save_panel(store, body)

    @router.get("/mcp")
    @router.delete("/mcp")
    def unsupported_stream():
        return Response(status_code=405, headers={"Allow": "POST"})

    @router.post("/mcp")
    async def mcp(request: Request):
        origin = request.headers.get("origin")
        if origin and not re.fullmatch(r"https?://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]+)?", origin):
            return Response(status_code=403)
        if request.headers.get("mcp-protocol-version", VERSIONS[0]) not in VERSIONS:
            return Response(status_code=400)
        try:
            message = await request.json()
        except ValueError:
            return rpc_error(None, -32700, "Invalid JSON", 400)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return rpc_error(None, -32600, "Invalid request", 400)
        method, rid, params = message["method"], message.get("id"), message.get("params", {})
        if "id" not in message:
            return Response(status_code=202)
        if not isinstance(params, dict):
            return rpc_error(rid, -32602, "Expected named parameters")
        if method == "initialize":
            version = params.get("protocolVersion")
            result = {"protocolVersion": version if version in VERSIONS else VERSIONS[0],
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "paper-workbench", "version": "1.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": name, "description": desc, "inputSchema": model.model_json_schema()}
                                for name, (model, desc) in desk.TOOLS.items()]}
        elif method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or name not in desk.TOOLS:
                return rpc_error(rid, -32602, "Unknown tool")
            try:
                args = desk.TOOLS[name][0].model_validate(params.get("arguments", {}))
                data = desk.call(store, name, args)
                result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "structuredContent": data}
            except (ApiError, ValidationError) as error:
                result = {"isError": True, "content": [{"type": "text", "text": str(error)}]}
        else:
            return rpc_error(rid, -32601, "Unknown method")
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})


def rpc_error(rid, code, message, status=200):
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}, status_code=status)
