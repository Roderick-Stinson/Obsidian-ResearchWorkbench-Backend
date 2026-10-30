"""Write v2 example bodies. brief/overview come from the existing dashboard-data.json snapshot;
the rest is trimmed from view.js demo data and the paper-record cards."""
import json, shutil, sys
from pathlib import Path

ROOT = Path(sys.argv[1])          # 论文工作台/
OUT = ROOT / "后端约定" / "examples"
GEN = "2026-10-27T15:39:49+08:00"
RUN = "run-2026-10-27"

def resp(kind, generated=True, at=GEN):
    h = {"schemaVersion": 1, "kind": kind}
    return {**h, "generatedAt": at, "runId": RUN} if generated else h
def ref(pid, title):
    return {"id": pid, "title": title, "url": "https://arxiv.org/abs/" + pid[6:]} if pid and pid.startswith("arxiv-") else {"id": pid, "title": title}
def user(pid, **kw):
    return {"paperId": pid, "rating": 0, "flags": [], "relevance": None, "watched": False, "own": False, "discussionStatus": None,
            "updatedAt": None, **kw}
def write(rel, data):
    p = OUT / rel; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if OUT.exists():
    shutil.rmtree(OUT)
R = "responses/"

write(R + "health.json", {**resp("health", False), "status": "ok", "version": "0.1.0", "time": GEN, "taskTypes": ["claim-run", "rebuild-interests"]})
write(R + "jobs.json", {**resp("jobs"), "items": [
    {"key": "pdf-download", "name": "下载论文 PDF", "status": "running", "progress": {"done": 482, "total": 989},
     "message": "论文库 989 篇里 482 篇已有 PDF；没有公开来源 30 篇，被付费墙挡住 29 篇", "updatedAt": GEN},
    {"key": "dblp-verify", "name": "用 DBLP 核实“待核实”论文", "status": "running", "progress": {"done": 82, "total": 1107},
     "message": "正在下载 DBLP 整库：82 MB / 约 1107 MB（对方服务器限速）", "updatedAt": GEN}]})

old = json.loads((Path(__file__).resolve().parent / "example_source.json").read_text(encoding="utf-8"))
b = old["brief"]
start, end = b["weeklyPeriod"].split(" 至 ")
write(R + "brief.json", {**resp("brief", at="2026-10-27T15:50:48+08:00"),
    "date": b["date"], "updatedAt": b["updatedAt"], "newCount": b["newCount"], "pickedCount": b["pickedCount"],
    "summary": b["summary"],
    "highlights": [{"title": h["title"], "text": h["text"], "sources": [ref(s["id"], s["title"]) for s in h["sources"]]} for h in b["highlights"]],
    "picks": [],
    "todayNew": [{"id": p["id"], "title": p["title"], "summary": p["summary"], "publishedAt": p["published"],
                  "url": "https://arxiv.org/abs/" + p["id"][6:]} for p in old["todayNew"][:3]],
    "weekly": {"period": {"start": start, "end": end}, "summary": b["weeklySummary"], "reportId": "weekly-" + start},
    "accepted": [{"paper": ref("openreview-imcyVlzpXh", "Multi-agent Architecture Search via Agentic Supernet"), "venue": "ICML 2025", "kind": "upgraded"}]})
write(R + "overview.json", {**resp("overview"), "paperTotal": old["overview"]["paperTotal"], "relevantTotal": 1874,
    "newLast7Days": 96, "lastIngestAt": "2026-10-27T01:12:30+08:00"})

STEPS = [("fetch", "抓取新论文"), ("version-check", "检查中稿，升级论文 ID"), ("classify", "按研究兴趣判定相关性"),
         ("skim", "读全文速读"), ("recommend", "分出核心 / 邻近 / 热点三类推荐"), ("pick", "挑选精选（≤10 篇）"), ("new-accepted", "每周：新中稿入库"),
         ("deep-read", "写精读"), ("results", "更新结果表"), ("map", "领域地图重组"), ("must-read", "更新必读"),
         ("classics", "更新经典"), ("interests", "更新候选关键词"), ("research-lines", "推进研究线"),
         ("hotspots", "整理热点"), ("report", "生成报告"), ("push", "推送 Telegram")]
steps = [{"key": k, "name": n, "status": "failed" if k == "map" else "ok",
          "startedAt": "2026-10-27T01:00:00+08:00", "finishedAt": "2026-10-27T01:20:00+08:00",
          "attempts": 3 if k == "map" else 1, "error": "RuntimeError: 领域地图重组失败：退出码 1" if k == "map" else None}
         for k, n in STEPS]
write(R + "pipeline.json", {**resp("pipeline"),
    "schedule": [{"when": "01:00", "what": "每日流程（16 步依次执行）", "panel": "首页 → 流程明细"},
                 {"when": "08:30", "what": "Telegram 推送当期", "panel": ""},
                 {"when": "每 30 分钟", "what": "自动补跑暂停/失败的步骤", "panel": "首页 → 流程明细"},
                 {"when": "每周一", "what": "更新候选关键词；出上周周报", "panel": "我的研究领域、报告"}],
    "latestRun": {"runId": RUN, "date": "2026-10-27", "status": "partial", "startedAt": "2026-10-27T01:00:00+08:00",
                  "finishedAt": "2026-10-27T01:20:00+08:00", "steps": steps},
    "attention": [{"id": "pipeline-run-2026-10-27-map", "source": "pipeline", "severity": "error",
                   "title": old["attention"][0]["title"], "summary": "RuntimeError: 领域地图重组失败：退出码 1",
                   "since": "2026-10-27T01:20:00+08:00", "link": None}]})

MAAS = ref("openreview-imcyVlzpXh", "Multi-agent Architecture Search via Agentic Supernet")
write(R + "recommendations-index.json", {**resp("recommendations-index"), "issues": [
    {"date": "2026-10-27", "counts": {"core": 1, "adjacent": 1, "trending": 1}, "pickedCount": 1}]})
write(R + "recommendations-day.json", {**resp("recommendations-day"), "date": "2026-10-27",
    "counts": {"core": 1, "adjacent": 1, "trending": 1}, "domains": [{"id": "kw-mas", "label": "多智能体系统", "count": 1}], "papers": [
    {"id": "arxiv-2610.17613", "titleZh": "MAS-Gate 通过节点级量化统领多智能体全生命周期，将工作流编排平均表现提升至 44.70%",
     "titleEn": "MAS-Gate: Unlocking Multi-Agent System Lifecycle via Node-Centric Quantification",
     "summary": "提出基于节点效用与跨节点传播强度的统一量化方法 MAS-Gate，贯穿设计期编排、运行期干预与事后归因。",
     "publishedAt": "2026-10-13", "url": "https://arxiv.org/abs/2610.17613", "codeUrl": None, "hfUpvotes": 0,
     "citations": {"count": 0, "updatedAt": "2026-10-27T01:10:00+08:00"},
     "quality": {"score": 8.0, "rationale": "设计期、运行期、事后归因三阶段都有实验"},
     "category": "core", "reason": "命中关键词“优化过程的可解释性”“节点级归因”",
     "matchedKeywords": [{"nodeId": "kw-opt-explain", "label": "优化过程的可解释性"}],
     "score": 0.91, "picked": True, "user": user("arxiv-2610.17613"), "sources": ["智能体", "Hugging Face"], "institutions": ["（示例）某大学"],
     "domain": {"id": "kw-mas", "label": "多智能体系统"}},
    {"id": "arxiv-2610.05284", "titleZh": "DAGO 用上下文老虎机引导的多父代工作流融合将六项基准宏平均分提升至 81.7",
     "titleEn": "Fusion is the New Mutation: Bandit-Guided Evolution on Workflow Graphs",
     "summary": "将多父代工作流融合建模为上下文老虎机臂选择问题，在相同验证预算下超越 AFlow。",
     "publishedAt": "2026-10-04", "url": "https://arxiv.org/abs/2610.05284", "codeUrl": None, "hfUpvotes": 0,
     "citations": {"count": 2, "updatedAt": "2026-10-27T01:10:00+08:00"},
     "quality": {"score": 7.3, "rationale": ""}, "category": "adjacent",
     "reason": "引用了你标为相关的 MaAS，但研究的是工作流演化搜索，不在你的关键词里",
     "matchedKeywords": [], "score": 0.64, "picked": False, "user": user("arxiv-2610.05284")},
    {"id": "arxiv-2610.32131", "titleZh": "轻量级代码智能体 Harness 与模型搭配研究：同模型不同 Harness 解决率相差可达 44 个百分点",
     "titleEn": "Harness Matters: Pairing Lightweight Code Agents with Harnesses",
     "summary": "对 3 款轻量级代码模型与 6 款开源 Agent Harness 做 18 组全因子评测。",
     "publishedAt": "2026-10-23", "url": "https://arxiv.org/abs/2610.32131", "codeUrl": None, "hfUpvotes": 21,
     "citations": {"count": None, "updatedAt": None}, "quality": {"score": 7.0, "rationale": ""},
     "category": "trending", "reason": "一级领域“多智能体系统”内近 7 天讨论量最高的论文之一",
     "matchedKeywords": [], "score": 0.58, "picked": False, "user": user("arxiv-2610.32131")}]})

_day = json.loads((OUT / (R + "recommendations-day.json")).read_text(encoding="utf-8"))
write(R + "recommendations-backlog.json", {**resp("recommendations-backlog", False), "days": 14, "max": 10, "total": 1,
    "papers": [{**_day["papers"][0], "date": "2026-10-27"}]})

write(R + "paper.json", {**resp("paper", False), "id": "openreview-imcyVlzpXh", "aliases": ["arxiv-2502.04180"],
    "title": "Multi-agent Architecture Search via Agentic Supernet", "titleZh": "MaAS：基于智能体超网的多智能体架构搜索",
    "authors": ["Guibin Zhang"], "firstAuthor": "Guibin Zhang", "year": 2025, "venue": "ICML 2025",
    "publicationStatus": "已发表",
    "versions": [{"id": "openreview-imcyVlzpXh", "kind": "openreview", "label": "ICML 2025", "url": "https://openreview.net/forum?id=imcyVlzpXh", "date": None},
                 {"id": "arxiv-2502.04180", "kind": "arxiv", "label": "arXiv:2502.04180v2", "url": "https://arxiv.org/abs/2502.04180", "date": "2025-02-06"}],
    "urls": {"source": "https://openreview.net/pdf?id=imcyVlzpXh", "pdf": "https://arxiv.org/pdf/2502.04180", "code": "https://github.com/bingreeky/MaAS"},
    "hasPdf": True, "pdfSha256": "1f7a580667049d3f7ac879c9ef9b3d206e22ae5f52ee18ab1b52fe0b312bc0b0",
    "citations": {"count": 120, "updatedAt": "2026-10-27T01:10:00+08:00"}, "metadataStatus": "部分核实",
    "summary": "把多智能体架构搜索变成在智能体超网上按查询采样子架构。",
    "matchedKeywords": [{"nodeId": "kw-arch-search", "label": "多智能体架构搜索"}], "hasDeepRead": False,
    "addedAt": "2026-10-27T09:45:56+00:00", "updatedAt": "2026-10-27T09:45:56+00:00",
    "user": user("openreview-imcyVlzpXh", watched=True, relevance="相关", discussionStatus="待讨论", updatedAt=GEN),
    "authorsDetail": [
        {"name": "Guibin Zhang", "personKey": "n-guibin-zhang", "corresponding": False, "affiliations": [
            {"key": "x-exampleuniversity", "name": "Example University", "nameZh": "（示例）某大学", "parent": None, "unit": "Department of Computer Science"}]},
        {"name": "Example Advisor", "personKey": "A0000000001", "corresponding": True, "affiliations": [
            {"key": "I0000000002", "name": "Example Lab of Example University", "nameZh": "（示例）某大学某实验室", "parent": "（示例）某大学", "unit": None}]}],
    "affiliations": {"sources": ["openalex", "pdf"], "updatedAt": GEN}})
write(R + "paper-list.json", {**resp("paper-list", False), "items": [
    {"id": "openreview-imcyVlzpXh", "title": "Multi-agent Architecture Search via Agentic Supernet",
     "titleZh": "MaAS：基于智能体超网的多智能体架构搜索", "firstAuthor": "Guibin Zhang", "year": 2025, "venue": "ICML 2025",
     "publicationStatus": "已发表", "hasPdf": True, "citations": {"count": 120, "updatedAt": "2026-10-27T01:10:00+08:00"},
     "summary": "把多智能体架构搜索变成在智能体超网上按查询采样子架构。",
     "urls": {"source": "https://openreview.net/pdf?id=imcyVlzpXh", "pdf": "https://arxiv.org/pdf/2502.04180", "code": "https://github.com/bingreeky/MaAS"},
     "topics": ["查询自适应DAG架构搜索"], "libraryRelevance": "core",
     "user": user("openreview-imcyVlzpXh", watched=True, relevance="相关", discussionStatus="待讨论", updatedAt=GEN)}],
    "nextCursor": None, "total": 1,
    "facets": {"topic": [{"label": "查询自适应DAG架构搜索", "count": 1}], "venue": [{"label": "ICML", "count": 1}],
               "year": [{"label": "2025", "count": 1}], "status": [{"label": "已发表", "count": 1}], "relevance": [{"label": "core", "count": 1}]}})
write(R + "paper-topics.json", {**resp("paper-topics", False), "items": [{"label": "查询自适应DAG架构搜索", "count": 42}, {"label": "LLM 可解释性", "count": 17}]})
write(R + "deep-read.json", {**resp("deep-read", at="2026-10-27T15:42:05+08:00"), "paperId": "arxiv-2610.17613",
    "markdown": "## 研究动机\n\n（示例）……\n\n## 方法\n\n（示例）……", "sources": [ref("arxiv-2610.17613", "MAS-Gate")]})
write(R + "resolve-result.json", {**resp("resolve-result", False),
    "ids": {"arxiv-2502.04180": "openreview-imcyVlzpXh", "arxiv-2610.17613": "arxiv-2610.17613", "arxiv-2699.00001": None}})
write(R + "paper-user.json", {**resp("paper-user", False), **user("arxiv-2610.17613", rating=4, flags=["收藏"], relevance="相关", updatedAt=GEN)})
write(R + "queue.json", {**resp("queue", False), "paperIds": ["arxiv-2610.17613"], "updatedAt": GEN})
write(R + "preferences.json", {**resp("preferences", False), "needCode": False, "updatedAt": None})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pwb.daily import DEFAULTS as REC_DEFAULTS  # noqa: E402
write(R + "recommend-settings.json", {**resp("recommend-settings", False), **REC_DEFAULTS,
    "sources": {"huggingface": True, "streams": {"agent": True}}, "updatedAt": None,
    "streams": [{"key": "agent", "name": "智能体", "query": "abs:agent"}], "profileProblem": None})
write(R + "idea-notes.json", {**resp("idea-notes", False), "notes": {"line-conservecut": "先确认 replay gap 的口径"}, "updatedAt": GEN})

ev_own = {"kind": "paper", "ref": "openreview-AbCdEf1234", "title": "（示例）我的投稿", "quote": "（示例：论文中的原文摘录）"}
ev_maas = {"kind": "paper", "ref": "openreview-imcyVlzpXh", "title": "MaAS", "quote": None}
write(R + "interests.json", {**resp("interests"), "overview": "（示例）研究概述。",
    "basis": {"ownPapers": 1, "watchedPapers": 1, "relevantPapers": 1}, "stale": False,
    "domains": [{"id": "kw-mas", "label": "多智能体系统", "summary": "你设定的一级领域", "origin": "user", "pinned": False,
                 "evidence": [], "children": [
        {"id": "kw-arch-search", "label": "多智能体架构搜索", "summary": "示例二级关键词", "origin": "extracted", "pinned": False,
         "evidence": [ev_own, ev_maas], "children": [
            {"id": "kw-opt-explain", "label": "优化过程的可解释性", "summary": "示例三级关键词", "origin": "extracted",
             "pinned": True, "evidence": [ev_own]}]}]}],
    "candidates": [{"id": "cand-harness", "parentId": "kw-mas", "level": 2, "label": "Agent Harness 设计与评测",
                    "reason": "近 30 天该方向论文数增长明显，你的关键词树里还没有",
                    "growth": {"paperCount": 23, "windowDays": 30, "changePct": 120},
                    "examples": [ref("arxiv-2610.32131", "轻量级代码智能体 Harness 与模型搭配研究")], "source": "trend"},
                   {"id": "cand-skill-reuse", "parentId": "kw-mas", "level": 2, "label": "技能复用与组合",
                    "reason": "你在两篇笔记里都想追技能能否跨任务复用",
                    "growth": {"paperCount": 2, "windowDays": 0, "changePct": 0},
                    "examples": [ref("arxiv-2502.04180", "Multi-agent Architecture Search via Agentic Supernet")], "source": "notes"}]})
write(R + "teams.json", {**resp("teams", False), "total": 1, "coverage": {"papers": 989, "withAffiliations": 812},
    "items": [{"key": "I0000000003", "name": "Example University", "nameZh": "（示例）某大学", "country": "CN", "paperCount": 12, "score": 21.5,
               "latestYear": 2026, "keywords": [{"label": "多智能体架构搜索", "count": 7}], "units": ["Department of Computer Science"],
               "people": [{"key": "A0000000001", "name": "Example Advisor", "openalexId": "A0000000001", "paperCount": 9,
                           "correspondingCount": 6, "lastAuthorCount": 8, "followed": True}],
               "papers": [ref("openreview-imcyVlzpXh", "MaAS：基于智能体超网的多智能体架构搜索")]}]})
write(R + "follows.json", {**resp("follows", False), "updatedAt": GEN, "people": [
    {"key": "A0000000001", "name": "Example Advisor", "openalexId": "A0000000001", "institution": "I0000000003",
     "institutionName": "（示例）某大学", "followedAt": GEN}]})
write(R + "notes.json", {**resp("notes", False), "updatedAt": GEN,
    "items": [{"paperId": "arxiv-2502.04180", "path": "论文阅读/论文笔记整理/MaAS 用超网搜索多智能体架构.md",
               "title": "MaAS 用超网搜索多智能体架构", "status": "精读", "rating": 5, "mtime": 1761700000000,
               "sections": ["逻辑链", "1. 研究动机", "我的评价", "和我的研究的关系", "想法与疑问", "要追的论文"], "seed": True}],
    "skipped": [{"path": "论文阅读/论文笔记整理/还没填 ID 的笔记.md", "mtime": 1761600000000, "reason": "paper_id 对不上论文：（空）"}],
    "profile": {"generatedAt": GEN, "summary": "（示例）你最关心按查询自适应地生成多智能体架构，并怀疑自演化带来的增益只是选择噪声。",
                "points": [{"id": "pt-0", "kind": "interest", "text": "按查询难度自适应选择架构，而不是一个架构打天下",
                            "papers": [ref("arxiv-2502.04180", "Multi-agent Architecture Search via Agentic Supernet")]},
                           {"id": "pt-1", "kind": "doubt", "text": "只在单一基准上报告的自演化增益",
                            "papers": [ref("arxiv-2502.04180", "Multi-agent Architecture Search via Agentic Supernet")]}]}})
ev = lambda b, c, s, code=None: {"libraryCitations": b, "citations": c, "baselineCount": None, "githubStars": s, "codeUrl": code}
write(R + "classics.json", {**resp("classics"), "rules": {"libraryCitationMin": 8, "libraryPapers": 989, "mustReadCount": 30},
    "factsUpdatedAt": "2026-10-25T21:44:00+08:00",
    "startHere": [{"paper": {"id": None, "title": "MetaGPT"}, "date": "2023-08", "venue": "ICLR",
                   "why": "多智能体架构研究的标准起点，理解 SOP 和消息池机制是看懂后续工作的基础。"}],
    "routes": [{"key": "static-sop", "name": "静态分工与 SOP 工作流", "intro": "早期系统通过预定义角色提示词与 SOP 规范协作。",
                "items": [{"paper": {"id": None, "title": "CAMEL"}, "date": "2023-03", "venue": "NeurIPS", "role": "开创",
                           "evidence": ev(13, 2053, 17880, "https://github.com/camel-ai/camel")},
                          {"paper": {"id": None, "title": "MetaGPT"}, "date": "2023-08", "venue": "ICLR", "role": "奠基",
                           "evidence": ev(37, None, None)}]}],
    "mustRead": [{"paper": {"id": None, "title": "Graph-of-Agents：通过图结构与双向消息传递使 3 个 Agent 超越 6 个 Agent 的 MoA 基线"},
                  "date": "2026-04", "period": "2026 年新作", "venue": "ICLR", "venueTier": "CCF-A 会议", "why": "论文库里 4 篇论文引用了它",
                  "evidence": ev(4, None, 53)}]})
write(R + "map.json", {**resp("map"),
    "intro": "多智能体架构优化从 2023 年基于人工规则与固定角色扮演的 SOP 启发式协作起步，逐步演进为将交互过程形式化为计算图并利用自动化搜索算法寻找最优拓扑。",
    "stages": [{"period": "2024", "title": "计算图形式化与自动化架构搜索", "description": "架构设计转向将多智能体系统抽象为可优化的计算图拓扑。",
                "milestones": [{"date": "2024-02", "venue": "ICML", "paper": {"id": None, "title": "GPTSwarm"}, "text": "GPTSwarm 将智能体建模为可优化计算图"}]}],
    "trends": [{"direction": "多智能体联合 RL 后训练", "trend": "up", "changePct": 64, "windowDays": 90, "paperCount": 27, "note": "增速最快"}],
    "benchmarksAndBaselines": [{"name": "AFlow", "kind": "baseline", "usedByCount": 53, "note": "最强工作流搜索基线之一"}],
    "gaps": [{"title": "失败归因", "kind": "gap", "metric": "仅 4 篇", "note": "很少有工作分析某个架构为什么在某类题上失败", "sources": []}],
    "categories": [{"routeKey": "static-sop", "name": "静态分工与 SOP 工作流", "paperCount": 418, "note": "预定义角色与流程"}],
    "hotspots": [{"stream": "agent", "name": "智能体", "source": "arXiv 上智能体方向的论文（检索词 abs:agent，分类 cs*，不限是否中稿）", "months": ["2026-07", "2026-08", "2026-09"],
                 "totals": [2600, 2800, 3000],
                 "items": [{"name": "Agent Skills", "summary": "把可复用的能力打包成技能，按需加载给智能体。",
                            "terms": ["agent skills", "skill library"], "counts": [40, 66, 95], "onset": "2026-07", "peak": "2026-09",
                            "recentSharePct": 2.4, "examples": [ref("arxiv-2609.01234", "SkillBench")]}]}]})
write(R + "results-index.json", {**resp("results-index"), "categories": [
    {"name": "数学", "benchmarks": [{"key": "gsm8k", "name": "GSM8K", "entryCount": 2}]}]})
write(R + "results-benchmark.json", {**resp("results-benchmark"),
    "benchmark": {"key": "gsm8k", "name": "GSM8K", "category": "数学", "split": "test", "fullSize": 1319,
                  "metric": {"name": "Accuracy", "higherIsBetter": True}},
    "stats": {"methodCount": 455, "paperCount": 237},
    "entries": [
        {"method": "DenoiseFlow", "paper": {"id": None, "title": "DenoiseFlow"}, "value": 99.5, "scale": {"type": "full", "n": 1319},
         "backboneModel": None, "code": {"status": "anonymous", "url": None, "stars": None},
         "source": {"type": "original", "paper": {"id": None, "title": "DenoiseFlow"}, "quotedFrom": None, "location": "Table 1"},
         "isAblation": False, "cost": None},
        {"method": "Fixed-5-round debate", "paper": None, "value": 99, "scale": {"type": "subset", "n": 200},
         "backboneModel": None, "code": {"status": "unknown", "url": None, "stars": None},
         "source": {"type": "quoted", "paper": {"id": None, "title": "Wald-SPRT"}, "quotedFrom": None, "location": None},
         "isAblation": False, "cost": {"tokens": None, "usd": None, "llmCalls": 5}}]})
write(R + "ideas.json", {**resp("ideas"),
    "methods": [{"topic": "多智能体优化过程的可解释性", "method": "节点级效用量化", "evidence": "MAS-Gate 在设计期与事后归因中使用节点效用",
                 "gap": "尚无对搜索轨迹本身的解释", "sources": [ref("arxiv-2610.17613", "MAS-Gate")]}],
    "aiIdeas": [], "discussions": [],
    "researchLines": [{"id": "line-conservecut", "title": "ConserveCut：以语义守恒率搜索多智能体分解边界", "tag": "领域开放问题",
                       "status": "推进中", "startedAt": "2026-10-14", "stepCount": 6, "reviewCount": 1,
                       "question": "多智能体任务分解的交接边界会系统性丢失已发现事实，层级越深越损害最终有效信息",
                       "draft": "CutLedger：带 Replay Certificate 的多智能体边界风险账本。",
                       "review": {"mean": 6.0, "importance": 8, "novelty": 6, "depth": 6, "feasibility": 4, "note": ""},
                       "nextSteps": ["冻结并写入预注册：baseline A0、唯一 candidate boundary move A1"]}]})
write(R + "hotspots.json", {**resp("hotspots"), "items": [
    {"id": "x-1850000000000000000", "platform": "X / Twitter", "title": "示例：关于 Harness 与模型配对的讨论",
     "summary": "示例条目，用于调试前端渲染。", "url": "https://x.com/example/status/1850000000000000000", "author": "example",
     "publishedAt": "2026-10-26T20:00:00+08:00", "collectedAt": "2026-10-27T01:15:00+08:00",
     "metrics": {"likes": 120, "reposts": 30, "comments": None},
     "relatedPapers": [ref("arxiv-2610.32131", "轻量级代码智能体 Harness 与模型搭配研究")]}]})
write(R + "reports-index.json", {**resp("reports-index"), "reports": [
    {"id": "weekly-2026-10-19", "type": "weekly", "period": {"start": "2026-10-19", "end": "2026-10-25"},
     "title": "周报 · 2026-10-19 当周", "generatedAt": "2026-10-26T09:00:00+08:00"}],
    "topPapers": [{"paper": {"id": None, "title": "自进化智能体架构 HarnessOpt 与插件架构在固定模型权重下大幅提升任务表现"}, "score": 0.80, "reason": ""}]})
write(R + "report.json", {**resp("report", at="2026-10-26T09:00:00+08:00"), "id": "weekly-2026-10-19", "type": "weekly",
    "period": {"start": "2026-10-19", "end": "2026-10-25"}, "title": "周报 · 2026-10-19 当周",
    "markdown": "## 本周脉络\n\n" + b["weeklySummary"]})
write(R + "experiments.json", {**resp("experiments"), "reproGroups": [
    {"id": "repro-evosteer-table1", "name": "EvoSteer-9b 发布权重 × Table1（9 个基准）", "paper": ref("arxiv-2609.38661", "EvoSteer"),
     "runCount": 5, "status": "复现差距", "withinTolerance": 1, "total": 13, "missing": 1,
     "worstDiffs": [{"metric": "nq_open_em", "delta": -50.9}, {"metric": "nq_open_f1", "delta": -43.8}],
     "note": "", "updatedAt": "2026-10-20T12:30:00+08:00"}]})
write(R + "inbox.json", {**resp("inbox"), "runs": [
    {"runId": "R20261020-120910-76a", "project": "my-project", "host": "gpu-01", "startedAt": "2026-10-20T12:09:10+08:00",
     "endedAt": "2026-10-20T12:09:21+08:00", "durationSec": 11, "gpu": ["0"],
     "command": ".venv/bin/python run.py smoke --datasets all --run-id _selftest-mock", "cwd": "~/my-project",
     "status": "finished", "exitCode": 0, "logPath": None, "claimedBy": None}],
    "services": [{"name": "vllm", "host": "gpu-01", "command": "vllm serve Qwen3.5-9B", "startedAt": None, "port": 8000}]})
TASK = {**resp("task", False), "id": "task-20261027T153000-a1b2c3", "type": "deep-read", "params": {"paperId": "arxiv-2610.17613"},
        "status": "done", "createdAt": "2026-10-27T15:30:00+08:00", "updatedAt": "2026-10-27T15:42:05+08:00",
        "progress": None, "message": "精读已完成", "outputs": [{"kind": "deep-read", "path": "/papers/arxiv-2610.17613/deep-read"}], "error": None}
write(R + "task.json", TASK)
write(R + "task-list.json", {**resp("task-list", False), "items": [{**TASK, "status": "running", "message": "",
    "progress": {"done": 2, "total": 5, "message": "正在读第 3 节"}, "outputs": []}]})
write(R + "llm-settings.json", {**resp("llm-settings", False), "engine": "claude-code", "updatedAt": None, "engines": [
    {"key": "claude-code", "name": "Claude Code（本机命令行）", "ready": True, "detail": "/Users/me/.local/bin/claude", "model": None, "defaultModel": None},
    {"key": "codex", "name": "Codex CLI（本机命令行）", "ready": False, "detail": "没找到 codex 命令；安装 Codex CLI，或用 PWB_CODEX_BIN 指定路径", "model": None, "defaultModel": None},
    {"key": "deepseek", "name": "DeepSeek API", "ready": False, "detail": "没有 API key；设置环境变量 DEEPSEEK_API_KEY，或写入 data/secrets.json 的 deepseekApiKey", "model": "deepseek-chat", "defaultModel": "deepseek-chat"}]})
write(R + "error.json", {**resp("error", False), "error": {"code": "not_found", "message": "论文不存在：arxiv-2610.99999"}})

B = "bodies/"
write(B + "paper-create.json", {"title": "（示例）我的投稿", "url": "https://openreview.net/forum?id=AbCdEf1234", "own": True})
write(B + "paper-patch.json", {"title": "（示例）补上的标题", "venue": "某会议 2027 投稿", "year": 2026})
write(B + "paper-patch.状态.json", {"publicationStatus": "已录用", "venue": "ICLR 2027"})
write(B + "resolve-request.json", {"ids": ["arxiv-2502.04180", "arxiv-2610.17613"]})
write(B + "paper-user-patch.json", {"relevance": "相关", "rating": 4})
write(B + "paper-user-patch.关注.json", {"watched": True, "discussionStatus": "待讨论"})
write(B + "queue-put.json", {"paperIds": ["arxiv-2610.17613", "openreview-imcyVlzpXh"]})
write(B + "preferences-put.json", {"needCode": False})
write(B + "idea-note-put.json", {"text": "先确认 replay gap 的口径"})
write(B + "interest-node-create.json", {"label": "多智能体系统", "summary": "我关注的大领域"})
write(B + "interest-node-patch.json", {"summary": "只看架构生成与优化，不看具身多智能体"})
write(B + "interests-extraction.json", {"overview": "（示例）关注多智能体架构的自动设计与优化。",
    "basis": {"ownPapers": 0, "watchedPapers": 1, "relevantPapers": 0},
    "domains": [{"label": "多智能体系统", "summary": "示例一级关键词", "evidence": [ev_maas], "children": [
        {"label": "多智能体架构搜索", "summary": "示例二级关键词", "evidence": [ev_maas], "children": [
            {"label": "查询自适应的超网采样", "summary": "示例三级关键词", "evidence": [ev_maas]}]}]}]})
write(B + "llm-settings-put.json", {"engine": "deepseek", "model": "deepseek-reasoner"})
write(B + "follow-put.json", {"name": "Example Advisor", "openalexId": "A0000000001", "institution": "I0000000003"})
write(B + "notes-put.json", {"notes": [{"paperId": "arxiv-2502.04180", "path": "论文阅读/论文笔记整理/MaAS 用超网搜索多智能体架构.md",
    "title": "MaAS 用超网搜索多智能体架构", "status": "精读", "rating": 5, "mtime": 1761700000000,
    "sections": [{"heading": "和我的研究的关系", "level": 2, "text": "它的超网只按任务采样，我想按查询采样。"},
                 {"heading": "要追的论文", "level": 2, "text": "- https://arxiv.org/abs/2410.10762"}]}]})
write(B + "recommend-settings-put.json", {"schedule": {"time": "01:00"}, "skim": {"fullText": ["core", "adjacent", "trending"]}})
write(B + "task-create.json", {"type": "deep-read", "params": {"paperId": "arxiv-2610.17613"}})
write(B + "task-create.周报.json", {"type": "generate-report", "params": {"type": "weekly", "period": {"start": "2026-10-19", "end": "2026-10-25"}}})
print("examples written to", OUT)
