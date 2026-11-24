"""Shared experiment index, with transactional panel edits and agent handoffs."""
import copy
import hashlib
import json
import re
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .errors import ApiError, invalid, not_found
from .util import now

KEY = "experiment-desk-v1"
COLLECTIONS = ("entities", "ideas", "configs", "runs", "uses")
LEGACY_PROJECT = re.compile(r"paper:[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}", re.I)
STATUS = {"running": "运行中", "completed": "已完成", "failed": "失败", "cancelled": "已取消"}


def empty():
    return {"version": 1, "revision": 0, **{key: [] for key in COLLECTIONS}}


def snapshot(store):
    return store.get_kv(KEY) or empty()


def transaction(store, edit):
    with store.conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        desk = store._kv(conn, KEY) or empty()
        before = copy.deepcopy(desk)
        result = edit(desk, conn)
        if before != desk:
            desk["revision"] = desk.get("revision", 0) + 1
            store._set_kv(conn, KEY, desk)
            store._set_kv(conn, KEY + ":revision", desk["revision"])
        return result


def find(rows, key):
    return next((row for row in rows if row["id"] == key), None)


def joined(row):
    return row.get("kind") == "paper" and (row.get("joined") is True or LEGACY_PROJECT.fullmatch(row["id"]))


def conflict(message):
    raise ApiError(409, message)


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Change(Args):
    collection: Literal["entities", "ideas", "configs", "runs", "uses"]
    before: Optional[Dict[str, Any]] = None
    after: Dict[str, Any]


class PanelEdit(Args):
    request_id: str = Field(min_length=1, max_length=128)
    changes: List[Change]
    import_missing: bool = False


def save_panel(store, form):
    receipt = "experiment-edit:" + form.request_id
    fingerprint = hashlib.sha256(form.model_dump_json().encode()).hexdigest()

    def edit(desk, conn):
        previous = store._kv(conn, receipt)
        if previous:
            if previous != fingerprint:
                conflict("保存请求编号已经使用")
            return {"saved": True}
        for op in form.changes:
            row = op.after
            key = row.get("id")
            if not isinstance(key, str) or not key or (op.before is not None and op.before.get("id") != key):
                raise invalid("实验记录缺少有效 ID")
            if op.collection == "entities" and (row.get("kind") not in ("paper", "baseline") or not isinstance(row.get("title"), str) or not row["title"].strip()):
                raise invalid("论文或 Baseline 的类型、名称无效")
            rows = desk[op.collection]
            current = find(rows, key)
            if (form.import_missing and current is not None) or row == current:
                continue
            if op.collection == "runs" and current and current.get("startRequest"):
                conflict("该运行由 MCP 登记，请通过 finish_run 回填")
            if current != op.before:
                conflict("记录在其他会话中已更新，请刷新后核对：" + key)
            if current is None:
                rows.append(row)
            else:
                rows[rows.index(current)] = row
        store._set_kv(conn, receipt, fingerprint)
        return {"saved": True}
    return transaction(store, edit)


class Projects(Args):
    pass


class Project(Args):
    project_id: str


class Runs(Args):
    project_id: Optional[str] = None
    status: Optional[Literal["running", "completed", "failed", "cancelled"]] = None
    limit: int = Field(50, ge=1, le=200)


class Run(Args):
    run_id: str = Field(min_length=1, max_length=160)


class Start(Run):
    project_id: str
    question: str = Field(min_length=1)
    config_name: str = Field(min_length=1)
    host: str = Field(min_length=1)
    cwd: str = Field(min_length=1)
    command: str = Field(min_length=1)
    output_path: str = Field(min_length=1, description="Run-specific location another session can read.")
    method_id: Optional[str] = Field(None, description="An existing baseline ID. Omit for the paper's own method.")
    job_id: str = ""
    dataset: str = ""
    protocol: str = ""
    metric: str = ""
    params: str = ""
    code_version: str = ""
    seed: str = ""


class Finish(Run):
    status: Literal["completed", "failed", "cancelled"]
    result: str = Field(min_length=1, description="Observed result or failure reason.")
    evidence_path: str = Field(min_length=1, description="Actual output or log supporting the result.")
    score: Optional[float] = None
    metrics: Dict[str, Union[float, str]] = Field(default_factory=dict)
    display: Optional[Dict[str, Any]] = Field(None, description="Optional structured summary/table/matrix blocks.")


class ProjectOverview(Args):
    project_id: str
    display: Dict[str, Any]


class UpsertIdea(Args):
    project_id: str
    id: Optional[str] = Field(None, min_length=1, max_length=160)
    question: str = Field(min_length=1)
    kind: Optional[Literal["rq", "claim", "todo"]] = None
    stage: Optional[str] = None
    summary: Optional[str] = None
    evidence: Optional[str] = None
    next: Optional[str] = None
    observation: Optional[str] = None
    note: Optional[str] = None
    run_ids: Optional[str] = None
    order: Optional[int] = None
    step: Optional[str] = None
    short: Optional[str] = None
    goal: Optional[str] = None
    gap: Optional[str] = None
    needs: Optional[List[Dict[str, Any]]] = None
    exps: Optional[List[str]] = None
    priority: Optional[str] = None
    tone: Optional[str] = None
    phase: Optional[str] = None
    cost: Optional[str] = None
    why: Optional[str] = None
    actions: Optional[List[str]] = None
    claim: Optional[str] = None
    exp: Optional[str] = None


class UpsertConfig(Args):
    project_id: str
    id: Optional[str] = Field(None, min_length=1, max_length=160)
    method_id: Optional[str] = None
    name: str = Field(min_length=1)
    exp_code: Optional[str] = None
    title: Optional[str] = None
    claims: Optional[str] = None
    phase: Optional[str] = None
    phase_label: Optional[str] = None
    dataset: Optional[str] = None
    protocol: Optional[str] = None
    metric: Optional[str] = None
    params: Optional[str] = None
    commit: Optional[str] = None
    original: Optional[Union[float, str]] = None
    parent: Optional[str] = None
    parent_name: Optional[str] = None
    guide: Optional[Dict[str, Any]] = None


class AttachRunDisplay(Run):
    display: Dict[str, Any]


def require(rows, key):
    row = find(rows, key)
    if row is None:
        raise not_found("找不到记录：" + key)
    return row


def project_context(desk, pid):
    paper = require(desk["entities"], pid)
    if not joined(paper):
        raise invalid("请先在面板明确加入这篇论文")
    return {"project": paper, "ideas": [i for i in desk["ideas"] if i["owner"] == pid],
            "configs": [c for c in desk["configs"] if c.get("owner") == pid],
            "baselines": [e for e in desk["entities"] if e.get("kind") == "baseline"]}


def run_detail(desk, run_id):
    r = require(desk["runs"], run_id)
    return {"run": {k: v for k, v in r.items() if k not in ("startRequest", "finishRequest")},
            "config": find(desk["configs"], r.get("config")),
            "idea": find(desk["ideas"], r.get("ideaId", ""))}


def start(desk, args):
    request = args.model_dump()
    prior = find(desk["runs"], args.run_id)
    if prior:
        if prior.get("startRequest") != request:
            conflict("运行编号已被另一组参数使用")
        return run_detail(desk, args.run_id)
    project_context(desk, args.project_id)
    owner = args.method_id or args.project_id
    if args.method_id and require(desk["entities"], owner)["kind"] != "baseline":
        raise invalid("method_id 应指向已登记的 Baseline")
    spec = {"owner": owner, "name": args.config_name, "dataset": args.dataset, "protocol": args.protocol,
            "metric": args.metric, "params": args.params, "commit": args.code_version}
    cfg = next((c for c in desk["configs"] if all(c.get(k, "") == v for k, v in spec.items())), None)
    if cfg is None:
        cfg = {**spec, "id": str(uuid4())}
        desk["configs"].append(cfg)
    idea = next((i for i in desk["ideas"] if i["owner"] == args.project_id and i["question"] == args.question), None)
    if idea is None:
        idea = {"id": str(uuid4()), "owner": args.project_id, "question": args.question, "stage": "验证中"}
        desk["ideas"].append(idea)
    row = {"id": args.run_id, "name": args.run_id, "owner": owner, "projectId": args.project_id,
           "ideaId": idea["id"], "config": cfg["id"], "host": args.host, "cwd": args.cwd, "command": args.command,
           "artifact": args.output_path, "jobId": args.job_id, "status": STATUS["running"], "startedAt": now(),
           "snapshot": args.code_version, "seed": args.seed, "startRequest": request}
    desk["runs"].append(row)
    return run_detail(desk, args.run_id)


def finish(desk, args):
    r = require(desk["runs"], args.run_id)
    request = args.model_dump()
    if r.get("finishRequest") == request:
        return run_detail(desk, args.run_id)
    if not r.get("startRequest") or r["status"] != STATUS["running"]:
        conflict("只有已登记且仍在运行的 MCP 实验可以回填，重跑请使用新编号")
    patch = dict(status=STATUS[args.status], result=args.result, score=args.score, metrics=args.metrics,
                 evidencePath=args.evidence_path, endedAt=now(), finishRequest=request)
    if args.display is not None:
        patch["display"] = args.display
    r.update(**patch)
    return run_detail(desk, args.run_id)


def update_project_overview(desk, args):
    project = project_context(desk, args.project_id)["project"]
    project["display"] = {**(project.get("display") or {}), **args.display}
    return {"project": project}


def upsert_idea(desk, args):
    project_context(desk, args.project_id)
    key = args.id or str(uuid4())
    existing = find(desk["ideas"], key)
    if existing and existing.get("owner") != args.project_id:
        raise invalid("想法不属于当前论文项目：" + key)
    payload = args.model_dump(exclude_none=True, exclude={"project_id", "id"})
    if "run_ids" in payload:
        payload["runIds"] = payload.pop("run_ids")
    row = {**(existing or {"id": key, "owner": args.project_id}), **payload}
    if existing is None:
        desk["ideas"].append(row)
    else:
        desk["ideas"][desk["ideas"].index(existing)] = row
    return {"idea": row}


def upsert_config(desk, args):
    project_context(desk, args.project_id)
    owner = args.method_id or args.project_id
    if args.method_id and require(desk["entities"], owner)["kind"] != "baseline":
        raise invalid("method_id 应指向已登记的 Baseline")
    key = args.id or str(uuid4())
    existing = find(desk["configs"], key)
    if existing and existing.get("owner") != owner:
        raise invalid("实验配置不属于当前方法或论文：" + key)
    payload = args.model_dump(exclude_none=True, exclude={"project_id", "id", "method_id"})
    for snake, camel in (("exp_code", "expCode"), ("phase_label", "phaseLabel"), ("parent_name", "parentName")):
        if snake in payload:
            payload[camel] = payload.pop(snake)
    row = {**(existing or {"id": key, "owner": owner}), **payload}
    if existing is None:
        desk["configs"].append(row)
    else:
        desk["configs"][desk["configs"].index(existing)] = row
    return {"config": row}


def attach_run_display(desk, args):
    r = require(desk["runs"], args.run_id)
    r["display"] = args.display
    return run_detail(desk, args.run_id)


TOOLS = {
    "list_projects": (Projects, "List papers explicitly added to the experiment desk."),
    "get_project": (Project, "Find an enrolled paper's research questions, configs and available baseline IDs."),
    "list_runs": (Runs, "Find handoffs across sessions by project and status, newest registrations first."),
    "get_run": (Run, "Read the run, question, execution location, version, command and result evidence."),
    "start_run": (Start, "Register a confirmed launch. Use the same unique run_id recorded beside the output. This tool records metadata only."),
    "finish_run": (Finish, "Report an observed terminal result to the same run. Identical retries are safe."),
    "update_project_overview": (ProjectOverview, "Update structured overview metadata (display fields) on an enrolled paper."),
    "upsert_idea": (UpsertIdea, "Create or update a research question, claim, roadmap step, or backlog todo item."),
    "upsert_config": (UpsertConfig, "Create or update an experiment config/plan and its structured setup guide."),
    "attach_run_display": (AttachRunDisplay, "Attach or update structured summary tables, comparison tabs, or matrix blocks on a run."),
}


MUTATION_HANDLERS = {
    "start_run": start,
    "finish_run": finish,
    "update_project_overview": update_project_overview,
    "upsert_idea": upsert_idea,
    "upsert_config": upsert_config,
    "attach_run_display": attach_run_display,
}


def call(store, name, args):
    if name in MUTATION_HANDLERS:
        return transaction(store, lambda desk, conn: MUTATION_HANDLERS[name](desk, args))
    desk = snapshot(store)
    if name == "list_projects":
        return {"projects": [e for e in desk["entities"] if joined(e)]}
    if name == "get_project":
        return project_context(desk, args.project_id)
    if name == "get_run":
        return run_detail(desk, args.run_id)
    rows = [r for r in reversed(desk["runs"]) if r.get("startRequest")
            and (not args.project_id or r.get("projectId") == args.project_id)
            and (not args.status or r.get("status") == STATUS[args.status])]
    keys = ("id", "projectId", "ideaId", "host", "artifact", "jobId", "status", "startedAt", "endedAt")
    return {"total": len(rows), "runs": [{k: r[k] for k in keys if k in r} for r in rows[:args.limit]]}
