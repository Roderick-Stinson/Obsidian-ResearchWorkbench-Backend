"""HTTP 接口（约定第 4 节）。路径都在 /api/v1 下。"""
import hashlib
import logging
import os
import re
import secrets
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import __version__, contract, daily, errors, interests, jobs, llm, migrate, pdfmeta, pdfs
from .errors import invalid, not_found
from .store import Store
from .tasks import HANDLERS, Worker
from .util import TZ_NAME, arxiv_id_from_url, new_id, now

PREFIX = "/api/v1"
LINE_ID_RE = re.compile(r"^line-[a-z0-9-]+$")
log = logging.getLogger("pwb.api")
OPENREVIEW_RE = re.compile(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_-]+)")
MAX_PDF_BYTES = 100 * 2**20
CORS_ORIGINS = r"^(app://obsidian\.md|capacitor://localhost|http://(localhost|127\.0\.0\.1)(:\d+)?)$"


def out(kind, body, status=200):
    return JSONResponse({"schemaVersion": 1, "kind": kind, **body}, status_code=status)


async def read_body(request, schema):
    try:
        body = await request.json()
    except ValueError:
        raise invalid("请求体不是合法的 JSON")
    errs = contract.errors(schema, body)
    if errs:
        raise invalid("请求体不符合约定 " + schema, errs)
    return body


def create_app(settings):
    store = Store(settings)
    migrate.run(store)
    worker = Worker(store)
    scheduler = daily.Scheduler(store, worker.wake)

    @asynccontextmanager
    async def lifespan(app):
        if settings.start_worker:
            worker.start()
            scheduler.start()
        yield
        if settings.start_worker:
            scheduler.stop()
            worker.stop()

    app = FastAPI(title="论文工作台后端", version=__version__, lifespan=lifespan)
    app.state.store, app.state.worker = store, worker
    errors.install(app)

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if settings.token and request.method != "OPTIONS" and request.url.path != PREFIX + "/health":
            if request.headers.get("authorization") != "Bearer " + settings.token:
                return errors.error_response(401, "token 缺失或错误")
        return await call_next(request)

    # 面板拿不到 Obsidian 的 requestUrl 时会用 fetch，需要放行 Obsidian 桌面端、移动端和本机调试页的来源。
    # 后加的中间件在最外层，这样 401 等错误响应也带 CORS 头。
    app.add_middleware(CORSMiddleware, allow_origin_regex=CORS_ORIGINS, allow_methods=["*"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Source-Path", "X-Source-Mtime"])

    r = APIRouter(prefix=PREFIX)

    def doc(kind, key=""):
        body = store.get_doc(kind, key) if key is not None else store.latest_doc(kind)
        if body is None:
            raise not_found("还没生成：{}{}".format(kind, " " + key if key else ""))
        return body

    def require_paper(paper_id):
        # 每日推荐里的论文还不在论文表里：你打开、标记它时才写进去（不显示在论文库，除非标了相关等）。
        pid = store.resolve(paper_id) or daily.materialize(store, paper_id)
        if not pid:
            raise not_found("论文不存在：" + paper_id)
        return pid

    # ---------- 状态与流水线数据 ----------

    @r.get("/health")
    def health():
        # taskTypes：后端目前能执行的任务；面板据此把还没实现的按钮显示成“即将上线”。
        return out("health", {"status": "ok", "version": __version__, "time": now(), "taskTypes": sorted(HANDLERS),
                              **({"timezone": TZ_NAME} if TZ_NAME else {})})

    @r.get("/overview")
    def overview():
        """论文库统计，每次直接从论文库算，不等流水线。"""
        return out("overview", {"generatedAt": now(), **store.library_overview()})

    @r.get("/jobs")
    def background_jobs():
        return out("jobs", jobs.all_jobs(store))

    for path, kind in [("/pipeline", "pipeline"), ("/recommendations", "recommendations-index"),
                       ("/classics", "classics"), ("/map", "map"), ("/results", "results-index"), ("/ideas", "ideas"),
                       ("/reports", "reports-index"), ("/experiments", "experiments"), ("/inbox", "inbox")]:
        r.add_api_route(path, (lambda kind: lambda: doc(kind))(kind), methods=["GET"], name=kind)

    @r.get("/brief")
    def brief(date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
        return doc("brief", date)

    @r.get("/recommendations/{date}")
    def recommendations_day(date: str):
        body = doc("recommendations-day", date)
        for paper in body["papers"]:
            paper["user"] = store.get_user(paper["id"])
        return body

    @r.get("/results/{key}")
    def results_benchmark(key: str):
        return doc("results-benchmark", key)

    @r.get("/reports/{report_id}")
    def report(report_id: str):
        return doc("report", report_id)

    @r.get("/hotspots")
    def hotspots(platform: Optional[str] = None):
        body = doc("hotspots")
        if platform:
            body["items"] = [i for i in body["items"] if i["platform"] == platform]
        return body

    # ---------- 论文 ----------

    @r.get("/papers")
    def papers(q: str = "", watched: Optional[bool] = None, own: Optional[bool] = None, topic: Optional[str] = None,
               venue: Optional[str] = None, year: Optional[int] = None, status: Optional[str] = None,
               relevance: Optional[str] = Query(None, pattern="^(core|related|none)$"),
               rating: Optional[str] = Query(None, pattern="^(unrated|[1-5])$"),
               sort: str = Query("added", pattern="^(relevance|newest|citations|rating|added)$"), facets: bool = False,
               cursor: Optional[str] = None, limit: int = Query(50, ge=1, le=200)):
        if cursor is not None and not cursor.isdigit():
            raise invalid("cursor 不合法")
        offset = int(cursor or 0)
        items, total, facet_counts = store.list_papers(q.strip(), watched, offset, limit, own, topic, venue, year, status,
                                                        relevance, rating, sort, facets)
        next_cursor = str(offset + len(items)) if offset + len(items) < total else None
        body = {"items": items, "nextCursor": next_cursor, "total": total}
        if facet_counts is not None:
            body["facets"] = facet_counts
        return out("paper-list", body)

    @r.get("/papers/topics")
    def paper_topics():
        return out("paper-topics", {"items": store.topic_counts()})

    def complete_later(pid):
        """后台从 arXiv 补信息、下载 PDF（要访问网络，不在请求里等）。测试里不起后台线程。"""
        def run():
            try:
                pdfs.complete_paper(store, pid, log=log.info)
            except Exception:
                log.exception("补全论文信息失败：%s", pid)
        if settings.start_worker:
            threading.Thread(target=run, name="pwb-complete-" + pid, daemon=True).start()

    @r.post("/papers")
    async def create_paper(request: Request):
        """手动添加论文（本人论文、关注论文）。已在论文库里的不重复建，只更新标记。
        给了 arXiv / OpenReview 链接的，后台会补上标题、作者等信息并下载 PDF；标题可以不填。"""
        body = await read_body(request, "paper-create")
        url = body.get("url")
        title = (body.get("title") or "").strip()
        if not title and not url:
            raise invalid("标题和链接至少填一个")
        m = OPENREVIEW_RE.search(url or "")
        pid = ("openreview-" + m.group(1) if m else arxiv_id_from_url(url)) or "local-" + secrets.token_hex(4)
        flags = {k: body[k] for k in ("own", "watched") if body.get(k)}
        existing = store.resolve(pid)
        if existing:
            paper = store.get_paper(existing)
            if paper["title"] == existing and title:
                # 收录时没拿到标题（例如匿名投稿下载失败）的，用你填的标题补上。
                store.upsert_paper({**paper, "title": title}, paper["aliases"])
            if flags:
                store.patch_user(existing, flags)
            if not paper["hasPdf"]:
                complete_later(existing)
            return out("paper", store.get_paper(existing))
        kind = pid.split("-", 1)[0]
        store.upsert_paper({
            "id": pid, "title": title or pid, "titleZh": None, "authors": [], "firstAuthor": "", "year": None,
            "venue": "", "publicationStatus": "投稿中" if body.get("own") else "待核实",
            "versions": [{"id": pid, "kind": kind, "label": pid, "url": url, "date": None}] if kind in ("openreview", "arxiv") else [],
            "urls": {"source": url, "pdf": None, "code": None}, "pdfSha256": None,
            "citations": {"count": None, "updatedAt": None}, "metadataStatus": "待核实", "summary": "", "matchedKeywords": []})
        if flags:
            store.patch_user(pid, flags)
        if kind in ("openreview", "arxiv"):
            complete_later(pid)
        return out("paper", store.get_paper(pid), status=201)

    @r.patch("/papers/{paper_id}")
    async def patch_paper(paper_id: str, request: Request):
        """你手动修改论文的基本信息（标题、发表状态、会议、年份、第一作者）。
        改过的字段记进 userEdited，领域检索重新入库、DBLP 核实等自动流程都不再覆盖它们。"""
        body = await read_body(request, "paper-patch")
        paper = store.get_paper(require_paper(paper_id))
        fixes = {k: (v.strip() if isinstance(v, str) else v) for k, v in body.items()}
        fixes["userEdited"] = sorted(set(paper.get("userEdited") or []) | set(body))
        store.upsert_paper({**paper, **fixes}, paper["aliases"])
        return out("paper", store.get_paper(paper["id"]))

    @r.put("/papers/{paper_id}/pdf")
    async def upload_pdf(paper_id: str, request: Request):
        pid = require_paper(paper_id)
        content = await request.body()
        if not content.startswith(b"%PDF"):
            raise invalid("上传的不是 PDF 文件")
        if len(content) > MAX_PDF_BYTES:
            raise invalid("PDF 超过 {} MB".format(MAX_PDF_BYTES // 2**20))
        target = settings.pdf_dir / (pid + ".pdf")
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, target)
        store.set_pdf(pid, target.name, hashlib.sha256(content).hexdigest())
        paper = store.get_paper(pid)
        if paper["title"] == pid:
            # 收录时没拿到标题的，从 PDF 第一页读出来补上；读不出来就保持“未知标题”。
            try:
                title = pdfmeta.guess_title(target)
            except Exception:
                title = None
            if title:
                store.upsert_paper({**paper, "title": title}, paper["aliases"])
        return out("paper", store.get_paper(pid))

    @r.post("/papers/resolve")
    async def resolve(request: Request):
        body = await read_body(request, "resolve-request")
        return out("resolve-result", {"ids": {i: store.resolve(i) for i in body["ids"]}})

    @r.get("/papers/{paper_id}")
    def paper(paper_id: str):
        return out("paper", store.get_paper(require_paper(paper_id)))

    @r.get("/papers/{paper_id}/pdf")
    def paper_pdf(paper_id: str):
        pid = require_paper(paper_id)
        path = store.pdf_path(pid)
        if not path or not path.exists():
            raise not_found("后端没有这篇论文的 PDF：" + pid)
        return FileResponse(path, media_type="application/pdf", filename=pid + ".pdf", content_disposition_type="inline")

    @r.get("/papers/{paper_id}/deep-read")
    def deep_read(paper_id: str):
        pid = require_paper(paper_id)
        body = store.get_deep_read(pid)
        if body is None:
            raise not_found("还没有这篇论文的精读：" + pid)
        return body

    # ---------- 你的数据 ----------

    @r.patch("/me/papers/{paper_id}")
    async def patch_paper_user(paper_id: str, request: Request):
        body = await read_body(request, "paper-user-patch")
        user = store.patch_user(require_paper(paper_id), body)
        if user is None:
            raise not_found("论文不存在：" + paper_id)
        return out("paper-user", user)

    @r.get("/me/queue")
    def get_queue():
        return out("queue", store.get_kv("queue") or {"paperIds": [], "updatedAt": None})

    @r.put("/me/queue")
    async def put_queue(request: Request):
        body = await read_body(request, "queue-put")
        resolved = [store.resolve(i) or daily.materialize(store, i) for i in body["paperIds"]]
        missing = [i for i, p in zip(body["paperIds"], resolved) if not p]
        if missing:
            raise invalid("论文不存在：" + "、".join(missing))
        if len(set(resolved)) != len(resolved):
            raise invalid("队列里有重复的论文（可能是同一篇的新旧 ID）")
        return out("queue", store.set_kv("queue", {"paperIds": resolved, "updatedAt": now()}))

    @r.get("/me/preferences")
    def get_preferences():
        return out("preferences", store.get_kv("preferences") or {"needCode": False, "updatedAt": None})

    @r.put("/me/preferences")
    async def put_preferences(request: Request):
        body = await read_body(request, "preferences-put")
        return out("preferences", store.set_kv("preferences", {"needCode": body["needCode"], "updatedAt": now()}))

    @r.get("/me/idea-notes")
    def get_idea_notes():
        return out("idea-notes", store.get_kv("idea-notes") or {"notes": {}, "updatedAt": None})

    @r.put("/me/idea-notes/{line_id}")
    async def put_idea_note(line_id: str, request: Request):
        if not LINE_ID_RE.match(line_id):
            raise invalid("研究线 ID 不合法：" + line_id)
        body = await read_body(request, "idea-note-put")
        notes = (store.get_kv("idea-notes") or {"notes": {}})["notes"]
        if body["text"]:
            notes[line_id] = body["text"]
        else:
            notes.pop(line_id, None)
        return out("idea-notes", store.set_kv("idea-notes", {"notes": notes, "updatedAt": now()}))

    # ---------- 研究兴趣 ----------

    def interests_or_404():
        tree = store.get_kv("interests")
        if tree is None:
            raise not_found("还没设定研究领域")
        return tree

    @r.get("/me/interests")
    def get_interests():
        return out("interests", interests_or_404())

    # 先于下面的通用操作路由注册，否则 children 会被当成 action。
    @r.post("/me/interests/nodes/{parent_id}/children")
    async def add_interest_node(parent_id: str, request: Request):
        body = await read_body(request, "interest-node-create")
        tree = interests.add_node(store.get_kv("interests"), parent_id, body["label"], body.get("summary", ""))
        return out("interests", store.set_kv("interests", tree))

    @r.patch("/me/interests/nodes/{node_id}")
    async def patch_interest_node(node_id: str, request: Request):
        body = await read_body(request, "interest-node-patch")
        return out("interests", store.set_kv("interests", interests.patch_node(interests_or_404(), node_id, body)))

    @r.post("/me/interests/nodes/{node_id}/{action}")
    def interest_node(node_id: str, action: str):
        tree = interests_or_404()
        if action == "remove":
            node = next((n for n, _, _ in interests.walk(tree["domains"]) if n["id"] == node_id), None)
            if node and node["origin"] == "extracted":
                store.set_kv("removed-extracted", (store.get_kv("removed-extracted") or []) + [interests.label_path(tree, node_id)])
        return out("interests", store.set_kv("interests", interests.node_action(tree, node_id, action)))

    @r.post("/me/interests/candidates/{candidate_id}/{action}")
    def interest_candidate(candidate_id: str, action: str):
        tree, rejected = interests.candidate_action(interests_or_404(), candidate_id, action,
                                                    store.get_kv("rejected-candidates") or [])
        store.set_kv("rejected-candidates", rejected)
        return out("interests", store.set_kv("interests", tree))

    # ---------- 大模型引擎设置 ----------

    def llm_settings():
        config, ready = llm.get_config(store), llm.status(settings)
        return out("llm-settings", {"engine": config["engine"], "updatedAt": config["updatedAt"], "engines": [
            {"key": k, "name": v["name"], "ready": ready[k][0], "detail": ready[k][1],
             "model": config["models"].get(k), "defaultModel": v["defaultModel"]} for k, v in llm.ENGINES.items()]})

    @r.get("/settings/recommend")
    def get_recommend_settings():
        return out("recommend-settings", daily.settings_body(store))

    @r.put("/settings/recommend")
    async def put_recommend_settings(request: Request):
        body = await read_body(request, "recommend-settings-put")
        try:
            daily.put_settings(store, body)
        except ValueError as e:
            raise invalid(str(e))
        return out("recommend-settings", daily.settings_body(store))

    @r.get("/settings/llm")
    def get_llm_settings():
        return llm_settings()

    @r.put("/settings/llm")
    async def put_llm_settings(request: Request):
        body = await read_body(request, "llm-settings-put")
        saved = store.get_kv("llm") or {}
        models = dict(saved.get("models") or {})
        if "model" in body:
            models[body["engine"]] = body["model"] or llm.ENGINES[body["engine"]]["defaultModel"]
        store.set_kv("llm", {"engine": body["engine"], "models": models, "updatedAt": now()})
        return llm_settings()

    # ---------- 任务 ----------

    @r.post("/tasks")
    async def create_task(request: Request):
        body = await read_body(request, "task-create")
        params = body["params"]
        if body["type"] == "deep-read":
            pid = store.resolve(params["paperId"])
            if not pid:
                raise invalid("论文不存在：" + params["paperId"])
            params = {**params, "paperId": pid}
        task, created = store.create_task(new_id("task"), body["type"], params, request.headers.get("idempotency-key"))
        if created:
            worker.wake()
        return out("task", task, status=202 if created else 200)

    @r.get("/tasks")
    def list_tasks(active: Optional[bool] = None, limit: int = Query(50, ge=1, le=200)):
        return out("task-list", {"items": store.list_tasks(active, limit)})

    @r.get("/tasks/{task_id}")
    def get_task(task_id: str):
        task = store.get_task(task_id)
        if task is None:
            raise not_found("任务不存在：" + task_id)
        return out("task", task)

    app.include_router(r)
    return app
