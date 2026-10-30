"""Generate JSON Schemas for the paper workbench API contract v2 (数据格式约定.md)."""
import json, sys
from pathlib import Path

OUT = Path(sys.argv[1])
BASE = "https://paper-workbench.local/schemas/"
C = "common.schema.json#/$defs/"

def ref(name): return {"$ref": C + name}
def nullable(s): return {"anyOf": [s, {"type": "null"}]}
def arr(items, **kw): return {"type": "array", "items": items, **kw}
def enum(*v): return {"enum": list(v)}
def obj(props, optional=()):
    return {"type": "object", "properties": props, "required": [k for k in props if k not in optional]}

STR = {"type": "string"}
TEXT = ref("Text")
INT = {"type": "integer", "minimum": 0}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}
DATE, MONTH, DT, PERIOD = ref("Date"), ref("Month"), ref("DateTime"), ref("Period")
PID, PREF, PREFS, URL = ref("PaperId"), ref("PaperRef"), arr(ref("PaperRef")), ref("Url")
SCORE10 = {"type": "number", "minimum": 0, "maximum": 10}
SCORE1 = {"type": "number", "minimum": 0, "maximum": 1}
CITATIONS = obj({"count": nullable(INT), "updatedAt": nullable(DT)})
KW_REF = obj({"nodeId": TEXT, "label": TEXT})
FLAGS = arr(enum("已读", "收藏", "复现"), uniqueItems=True)
RELEVANCE = nullable(enum("相关", "不相关"))
TASK_ID = {"type": "string", "pattern": r"^task-\d{8}T\d{6}-[0-9a-f]{6}$"}
LINE_ID = {"type": "string", "pattern": r"^line-[a-z0-9-]+$"}
LLM_ENGINES = ["claude-code", "codex", "deepseek"]
BASIS = obj({"ownPapers": INT, "watchedPapers": INT, "relevantPapers": INT})
CLAIM_TARGET = obj({"kind": enum("paper", "reproGroup", "experimentNote"), "id": TEXT})
STEP_KEYS = ["fetch", "version-check", "notes", "classify", "skim", "recommend", "pick", "new-accepted", "deep-read", "results", "map",
             "must-read", "classics", "interests", "research-lines", "hotspots", "report", "push"]

PAPER_USER = obj({"paperId": PID, "rating": {"type": "integer", "minimum": 0, "maximum": 5}, "flags": FLAGS,
                  "relevance": RELEVANCE, "watched": BOOL, "own": BOOL, "discussionStatus": nullable(STR),
                  "updatedAt": nullable(DT)})

EVIDENCE = obj({"kind": enum("paper"), "ref": TEXT, "title": TEXT, "quote": nullable(TEXT)})


def kw_node(level):
    # 你填写的关键词可以没有证据；整理所得和采纳的候选至少一条。
    node = obj({"id": TEXT, "label": TEXT, "summary": STR, "origin": enum("user", "extracted", "candidate"),
                "pinned": BOOL, "evidence": arr(EVIDENCE)})
    node["if"] = {"properties": {"origin": {"enum": ["extracted", "candidate"]}}}
    node["then"] = {"properties": {"evidence": {"minItems": 1}}}
    if level < 3:
        node["properties"]["children"] = arr(kw_node(level + 1))
        node["required"].append("children")
    else:
        node["properties"]["children"] = {"type": "array", "maxItems": 0}
    return node


def extraction_node(level):
    """一次整理结果里的节点：还没有 id、来源和固定标记，每个节点都要有证据。"""
    node = obj({"label": TEXT, "summary": TEXT, "evidence": arr(EVIDENCE, minItems=1)})
    if level < 3:
        node["properties"]["children"] = arr(extraction_node(level + 1))
    else:
        node["properties"]["children"] = {"type": "array", "maxItems": 0}
        node["required"] = [k for k in node["required"] if k != "children"]
    return node

common = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": BASE + "common.schema.json",
    "title": "公共定义",
    "$defs": {
        "Text": {"type": "string", "minLength": 1},
        "Date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "Month": {"type": "string", "pattern": r"^\d{4}(-\d{2})?$"},  # 年-月；查不到月份时只写年
        "DateTime": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$"},
        "Url": {"type": "string", "pattern": r"^https?://"},
        "PaperId": {"type": "string", "pattern": r"^[a-z][a-z0-9]*-[A-Za-z0-9._-]+$"},
        "PaperRef": obj({"id": nullable(PID), "title": TEXT, "url": URL}, optional=("url",)),
        "Period": obj({"start": DATE, "end": DATE}),
        "PaperUser": PAPER_USER,
        "Response": obj({"schemaVersion": {"const": 1}, "kind": STR}),
        "Generated": obj({"generatedAt": DT, "runId": nullable(STR)}, optional=("runId",)),
    },
}
USER = ref("PaperUser")
TASK_TYPES = {
    "deep-read": obj({"paperId": PID}),
    "generate-report": obj({"type": enum("weekly", "monthly"), "period": PERIOD}),
    "rebuild-map": {"type": "object", "maxProperties": 0},
    "rebuild-classics": {"type": "object", "maxProperties": 0},
    "rebuild-interests": {"type": "object", "maxProperties": 0},
    "run-daily": {"type": "object", "maxProperties": 0},
    "retry-step": obj({"runId": TEXT, "step": enum(*STEP_KEYS)}),
    "ingest-paper": obj({"url": URL, "watch": BOOL}),
    "claim-run": obj({"runId": TEXT, "target": CLAIM_TARGET}),
}
def typed_params(body):
    body["allOf"] = [{"if": {"properties": {"type": {"const": t}}}, "then": {"properties": {"params": s}}}
                     for t, s in TASK_TYPES.items()]
    return body
TASK = typed_params(obj({
    "id": TASK_ID, "type": enum(*TASK_TYPES), "params": {"type": "object"},
    "status": enum("queued", "running", "done", "failed"), "createdAt": DT, "updatedAt": DT,
    "progress": nullable(obj({"done": INT, "total": INT, "message": STR})), "message": STR,
    "outputs": arr(obj({"kind": TEXT, "path": {"type": "string", "pattern": "^/"}})), "error": nullable(STR)}))
PAPER_SUMMARY = obj({"id": PID, "title": TEXT, "titleZh": nullable(STR), "firstAuthor": STR, "year": nullable(INT),
                     "venue": STR, "publicationStatus": enum("已发表", "已录用", "投稿中", "预印本", "待核实"),
                     "hasPdf": BOOL, "citations": CITATIONS, "summary": STR,
                     "urls": obj({"source": nullable(URL), "pdf": nullable(URL), "code": nullable(URL)}), "topics": arr(TEXT),
                     "libraryRelevance": nullable(enum("core", "related")), "user": USER})
BRIEF_PAPER = obj({"id": PID, "title": TEXT, "summary": STR, "publishedAt": nullable(DATE), "url": URL}, optional=("url",))
CLASSIC_EVIDENCE = obj({"libraryCitations": nullable(INT), "citations": nullable(INT), "baselineCount": nullable(INT),
                        "githubStars": nullable(INT), "codeUrl": nullable(URL)})
COUNTS = obj({"core": INT, "adjacent": INT, "trending": INT})

def closed(props, optional=None, **kw):
    s = obj(props, optional=props.keys() if optional is None else optional)
    s["additionalProperties"] = False
    s.update(kw)
    return s

CATS = enum("core", "adjacent", "trending")
def rng(lo, hi): return {"type": "integer", "minimum": lo, "maximum": hi}
WEIGHT = {"type": "number", "minimum": 0, "maximum": 1}
# 每日推荐设置的各个部分（返回体里每项都有；修改时每项都可省略，只改给了的）。
REC_SETTINGS = {
    "schedule": {"enabled": BOOL, "time": {"type": "string", "pattern": r"^([01]\d|2[0-3]):[0-5]\d$"}, "maxCatchupDays": rng(1, 30),
                 "weeklyDay": rng(1, 7)},
    "sources": {"huggingface": BOOL, "streams": {"type": "object", "additionalProperties": BOOL}},
    "screen": {"batchSize": rng(5, 100), "negativeExamples": rng(0, 200)},
    "adjacent": {"seeds": arr(enum("own", "watched", "relevant", "library"), uniqueItems=True), "lookbackDays": rng(1, 30)},
    "trending": {"minHfUpvotes": rng(0, 10000), "useHotspots": BOOL, "hotspotMinHfUpvotes": rng(0, 10000), "maxPerDay": rng(0, 500)},
    "skim": {"fullText": arr(CATS, uniqueItems=True), "maxFullTextPerDay": rng(0, 500)},
    "pick": {"max": rng(0, 30), "weights": closed({k: WEIGHT for k in ("relevance", "quality", "hf", "code")})},
    "weekly": {"newAccepted": BOOL, "map": BOOL, "classics": BOOL},
    "library": {"autoAdd": arr(CATS, uniqueItems=True)},
    "notes": {"sections": closed({k: TEXT for k in ("relation", "ideas", "follow")}), "screening": BOOL, "asSeeds": BOOL,
              "seedStatuses": arr(TEXT, uniqueItems=True), "seedMinRating": rng(0, 5), "followToQueue": BOOL, "candidates": BOOL},
}

# kind -> (title, body, generated)
responses = {
    "health": ("服务状态", obj({"status": enum("ok", "degraded"), "version": TEXT, "time": DT, "taskTypes": arr(TEXT), "timezone": TEXT},
                             optional=("taskTypes", "timezone")), False),
    "jobs": ("后台长任务进度", obj({"items": arr(obj({
        "key": TEXT, "name": TEXT, "status": enum("idle", "running", "stalled", "done"),
        "progress": nullable(obj({"done": INT, "total": INT})), "message": STR, "updatedAt": nullable(DT)}))}), True),
    "overview": ("论文库统计", obj({"paperTotal": INT, "relevantTotal": INT, "newLast7Days": INT,
                                 "lastIngestAt": nullable(DT)}), True),
    "pipeline": ("流水线状态", obj({
        "schedule": arr(obj({"when": TEXT, "what": TEXT, "panel": STR})),
        "latestRun": nullable(obj({
            "runId": TEXT, "date": DATE, "status": enum("running", "ok", "partial", "failed"),
            "startedAt": DT, "finishedAt": nullable(DT),
            "steps": arr(obj({"key": enum(*STEP_KEYS), "name": TEXT,
                              "status": enum("pending", "running", "ok", "failed", "skipped"),
                              "startedAt": nullable(DT), "finishedAt": nullable(DT),
                              "attempts": INT, "error": nullable(STR)}))})),
        "attention": arr(obj({"id": TEXT, "source": enum("pipeline", "experiment", "ingest", "task"),
                              "severity": enum("info", "warn", "error"), "title": TEXT, "summary": STR,
                              "since": DT, "link": nullable(STR)}))}), True),
    "brief": ("每日简报", obj({
        "date": DATE, "updatedAt": DT, "newCount": INT, "pickedCount": INT, "summary": STR,
        "highlights": arr(obj({"title": TEXT, "text": TEXT, "sources": arr(PREF, minItems=1)})),
        "picks": arr({"allOf": [BRIEF_PAPER, obj({"reason": STR})]}, maxItems=30),
        "todayNew": arr(BRIEF_PAPER),
        "weekly": nullable(obj({"period": PERIOD, "summary": TEXT, "reportId": nullable(STR)})),
        "accepted": arr(obj({"paper": PREF, "venue": STR, "kind": enum("upgraded", "new")}))}, optional=("accepted",)), True),
    "recommendations-index": ("往期推荐", obj({
        "issues": arr(obj({"date": DATE, "counts": COUNTS, "pickedCount": INT}))}), True),
    "recommendations-day": ("单日推荐", obj({
        "date": DATE, "counts": COUNTS,
        "papers": arr(obj({
            "id": PID, "titleZh": TEXT, "titleEn": TEXT, "summary": TEXT, "publishedAt": DATE, "url": URL,
            "codeUrl": nullable(URL), "hfUpvotes": INT, "citations": CITATIONS,
            "quality": obj({"score": SCORE10, "rationale": STR}),
            "category": enum("core", "adjacent", "trending"), "reason": TEXT,
            "matchedKeywords": arr(KW_REF), "score": SCORE1, "picked": BOOL, "user": USER, "sources": arr(TEXT)},
            optional=("sources",)))}), True),
    "paper-list": ("论文列表", obj({"items": arr(PAPER_SUMMARY), "nextCursor": nullable(STR), "total": INT,
                                 "facets": obj({k: arr(obj({"label": TEXT, "count": INT})) for k in ("topic", "venue", "year", "status", "relevance")})},
                                optional=("facets",)), False),
    "paper": ("论文详情", obj({
        "id": PID, "aliases": arr(PID, uniqueItems=True), "title": TEXT, "titleZh": nullable(STR),
        "authors": arr(TEXT), "firstAuthor": STR, "year": nullable(INT), "venue": STR,
        "publicationStatus": enum("已发表", "已录用", "投稿中", "预印本", "待核实"),
        "versions": arr(obj({"id": PID, "kind": enum("proceedings", "journal", "openreview", "arxiv"),
                             "label": STR, "url": nullable(URL), "date": nullable(DATE)})),
        "urls": obj({"source": nullable(URL), "pdf": nullable(URL), "code": nullable(URL)}),
        "hasPdf": BOOL, "pdfSha256": nullable({"type": "string", "pattern": "^[0-9a-f]{64}$"}),
        "citations": CITATIONS, "metadataStatus": enum("已核实", "部分核实", "待核实"),
        "summary": STR, "matchedKeywords": arr(KW_REF), "hasDeepRead": BOOL,
        "addedAt": DT, "updatedAt": DT, "user": USER,
        "topics": arr(TEXT), "libraryRelevance": nullable(enum("core", "related")), "libraryReason": STR, "abstract": STR,
        "userEdited": arr(TEXT)},
        optional=("topics", "libraryRelevance", "libraryReason", "abstract", "userEdited")), False),
    "paper-topics": ("论文库里的方向及篇数", obj({"items": arr(obj({"label": TEXT, "count": INT}))}), False),
    "deep-read": ("AI 精读", obj({"paperId": PID, "markdown": TEXT, "sources": PREFS}), True),
    "resolve-result": ("论文 ID 解析结果", obj({
        "ids": {"type": "object", "propertyNames": ref("PaperId"), "additionalProperties": nullable(PID)}}), False),
    "paper-user": ("我对一篇论文的标记", PAPER_USER, False),
    "queue": ("阅读队列", obj({"paperIds": arr(PID, uniqueItems=True), "updatedAt": nullable(DT)}), False),
    "preferences": ("推荐偏好", obj({"needCode": BOOL, "updatedAt": nullable(DT)}), False),
    "idea-notes": ("IDEA 批注", obj({"notes": {"type": "object", "propertyNames": LINE_ID,
                                             "additionalProperties": TEXT}, "updatedAt": nullable(DT)}), False),
    "interests": ("研究兴趣关键词树", obj({
        "overview": STR, "basis": BASIS, "stale": BOOL,
        "domains": arr(kw_node(1)),
        "candidates": arr(obj({"id": TEXT, "parentId": TEXT, "level": enum(2, 3), "label": TEXT, "reason": TEXT,
                               "growth": obj({"paperCount": INT, "windowDays": INT, "changePct": NUM}),
                               "examples": PREFS, "source": enum("trend", "notes")}, optional=("source",)))}), True),
    "notes": ("阅读笔记", obj({
        "items": arr(obj({"paperId": PID, "path": TEXT, "title": TEXT, "status": nullable(STR), "rating": nullable(rng(0, 5)),
                          "mtime": INT, "sections": arr(TEXT), "seed": BOOL})),
        "skipped": arr(obj({"path": TEXT, "mtime": INT, "reason": TEXT})), "updatedAt": nullable(DT),
        "profile": nullable(obj({"generatedAt": DT, "summary": STR, "points": arr(obj({
            "id": TEXT, "kind": enum("interest", "doubt"), "text": TEXT, "papers": PREFS}))})),
        "sync": obj({"ids": {"type": "object", "additionalProperties": PID}, "queued": PREFS})}, optional=("sync",)), False),
    "classics": ("经典与必读", obj({
        "rules": obj({"libraryCitationMin": INT, "libraryPapers": INT, "mustReadCount": INT}), "factsUpdatedAt": DT,
        "startHere": arr(obj({"paper": PREF, "date": MONTH, "venue": STR, "why": TEXT})),
        "routes": arr(obj({"key": TEXT, "name": TEXT, "intro": STR, "items": arr(obj({
            "paper": PREF, "date": MONTH, "venue": STR, "role": enum("开创", "奠基", "代表"), "why": STR,
            "evidence": CLASSIC_EVIDENCE}, optional=("why",)))})),
        "mustRead": arr(obj({"paper": PREF, "date": MONTH, "period": TEXT, "venue": STR,
                             "venueTier": enum("CCF-A 会议", "CCF-A 期刊", "其他"), "why": TEXT,
                             "evidence": CLASSIC_EVIDENCE}, optional=("period",)))}), True),
    "map": ("领域发展史", obj({
        "intro": TEXT,
        "stages": arr(obj({"period": TEXT, "title": TEXT, "description": STR, "milestones": arr(obj({
            "date": MONTH, "venue": STR, "paper": PREF, "text": TEXT}))})),
        "trends": arr(obj({"direction": TEXT, "trend": enum("up", "down", "flat"), "changePct": NUM,
                           "windowDays": INT, "paperCount": INT, "note": STR})),
        "benchmarksAndBaselines": arr(obj({"name": TEXT, "kind": enum("benchmark", "baseline"),
                                           "usedByCount": INT, "note": STR})),
        "gaps": arr(obj({"title": TEXT, "kind": enum("gap", "finding"), "metric": STR, "note": STR, "sources": PREFS})),
        "categories": arr(obj({"routeKey": TEXT, "name": TEXT, "paperCount": INT, "note": STR})),
        "hotspots": arr(obj({"stream": TEXT, "name": TEXT, "source": TEXT, "months": arr(MONTH), "totals": arr(INT), "items": arr(obj({
            "name": TEXT, "summary": STR, "terms": arr(TEXT), "counts": arr(INT), "onset": MONTH, "peak": MONTH,
            "recentSharePct": NUM, "examples": PREFS}))}))}, optional=("hotspots",)), True),
    "results-index": ("结果表目录", obj({"categories": arr(obj({
        "name": enum("数学", "代码生成", "问答", "知识与综合推理", "智能体 / 交互环境", "其它"),
        "benchmarks": arr(obj({"key": {"type": "string", "pattern": "^[a-z0-9-]+$"}, "name": TEXT,
                               "entryCount": INT}))}))}), True),
    "results-benchmark": ("单个 benchmark 结果表", obj({
        "benchmark": obj({"key": TEXT, "name": TEXT, "category": TEXT, "split": STR, "fullSize": nullable(INT),
                          "metric": obj({"name": TEXT, "higherIsBetter": BOOL})}),
        "stats": obj({"methodCount": INT, "paperCount": INT}),
        "entries": arr(obj({
            "method": TEXT, "paper": nullable(PREF), "value": NUM,
            "scale": obj({"type": enum("full", "subset", "unknown"), "n": nullable(INT)}),
            "backboneModel": nullable(STR),
            "code": obj({"status": enum("open", "anonymous", "none", "unknown"), "url": nullable(URL), "stars": nullable(INT)}),
            "source": obj({"type": enum("original", "quoted"), "paper": PREF, "quotedFrom": nullable(PREF), "location": nullable(STR)}),
            "isAblation": BOOL,
            "cost": nullable(obj({"tokens": nullable(INT), "usd": nullable(NUM), "llmCalls": nullable(INT)}))}))}), True),
    "ideas": ("IDEA 与研究线", obj({
        "methods": arr(obj({"topic": TEXT, "method": TEXT, "evidence": STR, "gap": STR, "sources": PREFS})),
        "aiIdeas": arr(obj({"id": TEXT, "title": TEXT, "summary": TEXT, "evidence": STR,
                            "sources": arr(PREF, minItems=1), "generatedAt": DT})),
        "discussions": arr(obj({"id": TEXT, "title": TEXT, "summary": STR, "next": STR, "updatedAt": DT})),
        "researchLines": arr(obj({
            "id": LINE_ID, "title": TEXT, "tag": STR, "status": enum("推进中", "暂停", "已放弃", "已转论文"),
            "startedAt": DATE, "stepCount": INT, "reviewCount": INT, "question": TEXT, "draft": STR,
            "review": nullable(obj({"mean": SCORE10, "importance": SCORE10, "novelty": SCORE10,
                                    "depth": SCORE10, "feasibility": SCORE10, "note": STR})),
            "nextSteps": arr(TEXT)}))}), True),
    "hotspots": ("社区热点", obj({"items": arr(obj({
        "id": TEXT, "platform": enum("X / Twitter", "微信公众号", "小红书", "知乎", "其他"),
        "title": TEXT, "summary": STR, "url": URL, "author": nullable(STR), "publishedAt": nullable(DT),
        "collectedAt": DT, "metrics": obj({"likes": nullable(INT), "reposts": nullable(INT), "comments": nullable(INT)}),
        "relatedPapers": PREFS}))}), True),
    "reports-index": ("报告目录", obj({
        "reports": arr(obj({"id": {"type": "string", "pattern": r"^(weekly-\d{4}-\d{2}-\d{2}|monthly-\d{4}-\d{2})$"},
                            "type": enum("weekly", "monthly"), "period": PERIOD, "title": TEXT, "generatedAt": DT})),
        "topPapers": arr(obj({"paper": PREF, "score": SCORE1, "reason": STR}))}), True),
    "report": ("报告正文", obj({"id": TEXT, "type": enum("weekly", "monthly"), "period": PERIOD, "title": TEXT,
                             "markdown": TEXT}), True),
    "experiments": ("复现配置组", obj({"reproGroups": arr(obj({
        "id": TEXT, "name": TEXT, "paper": nullable(PREF), "runCount": INT,
        "status": enum("复现一致", "复现差距", "进行中", "未开始"), "withinTolerance": INT, "total": INT, "missing": INT,
        "worstDiffs": arr(obj({"metric": TEXT, "delta": NUM})), "note": STR, "updatedAt": DT}))}), True),
    "inbox": ("待认领实验运行", obj({
        "runs": arr(obj({
            "runId": TEXT, "project": TEXT, "host": TEXT, "startedAt": DT, "endedAt": nullable(DT),
            "durationSec": nullable(INT), "gpu": arr(STR), "command": TEXT, "cwd": STR,
            "status": enum("running", "finished", "failed"), "exitCode": nullable({"type": "integer"}),
            "logPath": nullable(STR), "claimedBy": nullable(CLAIM_TARGET)})),
        "services": arr(obj({"name": TEXT, "host": TEXT, "command": TEXT, "startedAt": nullable(DT),
                             "port": nullable({"type": "integer"})}))}), True),
    "llm-settings": ("大模型引擎设置", obj({
        "engine": enum(*LLM_ENGINES), "updatedAt": nullable(DT),
        "engines": arr(obj({"key": enum(*LLM_ENGINES), "name": TEXT, "ready": BOOL, "detail": STR,
                            "model": nullable(STR), "defaultModel": nullable(STR)}))}), False),
    "recommend-settings": ("每日推荐设置", obj({**{k: closed(v, optional=()) for k, v in REC_SETTINGS.items()}, "updatedAt": nullable(DT),
        "streams": arr(obj({"key": TEXT, "name": TEXT, "query": TEXT})), "profileProblem": nullable(STR)}), False),
    "task": ("后台任务", TASK, False),
    "task-list": ("后台任务列表", obj({"items": arr(TASK)}), False),
    "error": ("错误", obj({"error": obj({"code": enum("invalid_params", "unauthorized", "not_found", "conflict", "unavailable", "internal"),
                                          "message": TEXT, "details": {}}, optional=("details",))}), False),
}

# 请求体：没有 kind，按文件名选 schema。
bodies = {
    "paper-create": ("添加论文", closed({"title": TEXT, "url": URL, "own": BOOL, "watched": BOOL}, optional=("title", "url", "own", "watched"))),
    "paper-patch": ("修正论文信息", closed({"title": TEXT, "titleZh": nullable(STR), "venue": STR, "firstAuthor": STR,
                                          "publicationStatus": enum("已发表", "已录用", "投稿中", "预印本", "待核实"),
                                          "year": nullable({"type": "integer", "minimum": 1900, "maximum": 2100})}, minProperties=1)),
    "resolve-request": ("论文 ID 解析请求", closed({"ids": arr(PID, minItems=1, maxItems=500, uniqueItems=True)}, optional=())),
    "paper-user-patch": ("修改我对一篇论文的标记", closed({
        "rating": {"type": "integer", "minimum": 0, "maximum": 5}, "flags": FLAGS, "relevance": RELEVANCE,
        "watched": BOOL, "own": BOOL, "discussionStatus": nullable(STR)}, minProperties=1)),
    "queue-put": ("设置阅读队列", closed({"paperIds": arr(PID, uniqueItems=True)}, optional=())),
    "preferences-put": ("设置推荐偏好", closed({"needCode": BOOL}, optional=())),
    "idea-note-put": ("设置 IDEA 批注", closed({"text": STR}, optional=())),
    "interest-node-create": ("添加关键词", closed({"label": TEXT, "summary": STR}, optional=("summary",))),
    "interest-node-patch": ("修改关键词", closed({"label": TEXT, "summary": STR}, minProperties=1)),
    "interests-extraction": ("一次关键词整理结果", obj({"overview": TEXT, "basis": BASIS,
                                                    "domains": arr(extraction_node(1), minItems=1)})),
    "llm-settings-put": ("切换大模型引擎", closed({"engine": enum(*LLM_ENGINES), "model": nullable(STR)}, optional=("model",))),
    "notes-put": ("同步阅读笔记（整体替换）", closed({"notes": arr(closed({
        "paperId": TEXT, "path": TEXT, "title": TEXT, "status": nullable(STR), "rating": nullable(rng(0, 5)), "mtime": INT,
        "sections": arr(closed({"heading": TEXT, "level": rng(1, 6), "text": STR}, optional=()), maxItems=200)},
        optional=("status", "rating")), maxItems=5000)}, optional=())),
    "recommend-settings-put": ("修改每日推荐设置", closed({k: closed(v) for k, v in REC_SETTINGS.items()}, minProperties=1)),
    "task-create": ("创建后台任务", typed_params(closed({"type": enum(*TASK_TYPES), "params": {"type": "object"}}, optional=()))),
}

OUT.mkdir(parents=True, exist_ok=True)
for old in OUT.glob("*.schema.json"):
    old.unlink()
def dump(name, data):
    (OUT / (name + ".schema.json")).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
def header(name, title):
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": BASE + name + ".schema.json", "title": title}

dump("common", common)
for kind, (title, body, generated) in responses.items():
    parts = [ref("Response"), {"properties": {"kind": {"const": kind}}}] + ([ref("Generated")] if generated else []) + [body]
    dump(kind, {**header(kind, title), "allOf": parts})
for name, (title, body) in bodies.items():
    dump(name, {**header(name, title), **body})
print(len(responses) + len(bodies) + 1, "schemas written to", OUT)
