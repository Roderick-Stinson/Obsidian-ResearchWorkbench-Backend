# 论文工作台 · 后端

> A local research-paper workbench backend (FastAPI + SQLite) for an Obsidian dashboard: builds a paper library for your research directions, keeps a keyword tree of your interests, and summarises classics, must-reads and field-wide hotspot trends with an LLM (local Claude Code / Codex CLI / DeepSeek). Docs are in Chinese.

Obsidian 论文面板的后端服务。面板（前端）在另一个仓库：[paper-workbench-obsidian](https://github.com/Roderick-Stinson/paper-workbench-obsidian)。接口按 `contract/数据格式约定.md` 实现；约定的源头在面板仓库的 `论文工作台/后端约定/`，这里的 `contract/` 是同步过来的副本。

已经能用的：论文库（领域检索入库、DBLP 核实中稿、批量下载公开 PDF）、我的标记与阅读队列、我的研究领域（关键词树的填写与大模型整理）、经典与必读、领域发展史（含 arXiv 全领域热点演变）。每日推荐流水线（抓取、速读、三类推荐、简报）还在做；还没生成的数据接口返回 404，还没实现的后台任务会以“尚未实现”失败。

## 目录

```
pwb/            代码
  api.py        HTTP 接口（/api/v1）
  store.py      SQLite 存储
  tasks.py      后台任务
  interests.py  研究兴趣关键词树的编辑与合并
  llm.py        可切换的大模型引擎（Claude Code / Codex CLI / DeepSeek API）
  rebuild.py    “重新整理关键词”任务：读论文原文 → 调用引擎 → 核对摘录 → 合并
  extraction.py 核对整理结果里的原文摘录并合并进关键词树
  survey.py     领域检索入库：Semantic Scholar 抓候选 → 大模型按标题、摘要筛选 → 写进论文库
  pdfmeta.py    从 PDF 第一页读标题
  dblp.py       用 DBLP 整库核实“待核实”论文是否中稿
  pdfs.py       批量下载论文库的 PDF（只用公开来源）
  landscape.py  经典与必读、领域发展史（参考文献统计 + 大模型整理）
  feed.py       arXiv 智能体方向论文流（按月，不限于论文库）
  hotspots.py   全领域热点演变（词组占比统计 + 大模型归类）
  jobs.py       后台长任务（PDF 下载、DBLP 核实）的进度
  importer.py   从 vault 一次性导入论文条目和 PDF
  seed.py       写入约定里的示例数据
contract/       约定副本：schemas、examples、validate.py
tools/          生成 schemas 和 examples 的脚本（改约定时用）
tests/          契约测试：每个接口的返回体都按 schema 校验
data/           数据（不进版本库）：workbench.sqlite3、pdfs/、survey.json（你的检索方向）
survey.example.json  检索方向配置的示例
```

迁移到服务器时，把整个文件夹拷过去即可（`data/` 里就是全部数据）。**不要把 `data/` 放进 Dropbox 等同步盘**，SQLite 数据库被同步工具改动容易损坏。

## 安装

```bash
git clone https://github.com/Roderick-Stinson/paper-workbench.git ~/PaperWorkbench
cd ~/PaperWorkbench
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 常用命令

```bash
# 启动服务（默认 http://127.0.0.1:8765/api/v1）
.venv/bin/python -m pwb serve

# 从 vault 导入现有论文条目和 PDF（只读 vault，可重复执行，不会覆盖你在面板里改过的标记）
.venv/bin/python -m pwb import-vault <你的 vault>

# 用示例数据起一个调试用实例（示例不是真实结果，放单独的数据目录，别混进真实数据）
.venv/bin/python -m pwb --data-dir data-demo seed-examples
PWB_DATA_DIR=data-demo PWB_PORT=8766 .venv/bin/python -m pwb serve

# 约定改了之后同步过来，再跑测试
.venv/bin/python -m pwb sync-contract <你的 vault>/论文工作台/后端约定
.venv/bin/python -m pytest -q
```

接口文档（FastAPI 自动生成）：服务启动后打开 http://127.0.0.1:8765/docs 。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PWB_DATA_DIR` | `./data` | 数据目录 |
| `PWB_HOST` | `127.0.0.1` | 监听地址；改成非本机地址时必须设置 `PWB_TOKEN` |
| `PWB_PORT` | `8765` | 端口 |
| `PWB_TOKEN` | 空 | 设置后所有接口（`/health` 除外）都要带 `Authorization: Bearer <token>` |
| `PWB_CLAUDE_BIN` | 自动查找 | `claude` 命令的路径（PATH 和 `~/.local/bin` 里找不到时设置） |
| `PWB_CODEX_BIN` | 自动查找 | `codex` 命令的路径 |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek API key；也可以写进 `data/secrets.json`：`{"deepseekApiKey": "sk-..."}` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek 接口地址 |

## 大模型引擎

整理关键词等需要大模型的工作，用哪个引擎在面板“设置 → 大模型引擎”里切换（默认本机 Claude Code）。

- **Claude Code**：调用本机 `claude -p`，用你已登录的账号（不需要 API key）。在临时空目录里运行，不给任何工具，只读给它的材料、输出 JSON。
- **Codex CLI**：调用本机 `codex exec`（只读沙箱）。
- **DeepSeek**：调用 DeepSeek 的 chat completions 接口。

不管用哪个引擎，结果都会先按约定校验，再逐条核对原文摘录确实出自论文；对不上的让模型重写一次，仍对不上就去掉摘录、只保留论文出处。

手动整理的结果（JSON）也可以直接并入：`.venv/bin/python -m pwb apply-interests 文件.json`。

## 领域检索入库

在 CCF-A 会议 / 期刊（各领域）、ICLR、EMNLP / NAACL、Nature 及子刊里，按检索词找 2023–2026 年和你方向相关的论文，写进论文库。

先写好你的检索方向：把 `survey.example.json` 复制成 `data/survey.json`，改里面的检索词（`queries`）、给大模型看的方向说明（`direction`，写清楚什么算相关、什么不算）和关键词树以外的方向（`extraTopics`）。

```bash
.venv/bin/python -m pwb survey                         # 全流程：fetch → titles → abstracts → ingest
.venv/bin/python -m pwb survey --stages titles --limit 300   # 只跑某一步、先试一小批
.venv/bin/python -m pwb survey --stages reingest       # 已入库的重新写一遍（不调用大模型）
```

- 会议清单在 `pwb/survey.py` 的 `VENUES` 里改，检索词在 `data/survey.json` 里改。
- 每一步的结果存在 `survey_candidates` 表里，中断后重跑会接着做。
- 会议能核实的（DBLP 有正式记录或有出版方 DOI）标“已发表”；只有 arXiv 版本、会议是 Semantic Scholar 推断的标“待核实”；ACL / EMNLP / NAACL 的 Findings 不入库。
- Semantic Scholar 对匿名访问限流很严，抓取会自动等待重试；设置环境变量 `S2_API_KEY`（免费申请）会快很多。
- 调用大模型时要在干净的终端里运行（不要在 Claude Code 会话里），用的是“设置 → 大模型引擎”里选的引擎。

## 经典与必读、领域发展史

```bash
.venv/bin/python -m pwb landscape                 # 取参考文献 → 经典与必读 → 领域发展史
.venv/bin/python -m pwb landscape classics map    # 参考文献已取过，只重新整理
```

面板上也可以点“重新整理”（任务 `rebuild-classics` / `rebuild-map`）。必读按发表年份分三段，每段的核心论文逐篇判断、符合标准的都选，不限篇数（只按被引排，新作永远进不来）。

全领域热点演变要先取 arXiv 论文流（标题或摘要含 agent 的计算机类论文，按月；第一次约 40 个请求、几分钟，之后只重取最近两个月）：

```bash
.venv/bin/python -m pwb feed                      # 默认从 2023-07 起
```

然后整理领域发展史时，会统计哪些词组的占比涨得最多、由大模型归成热点，按起势时间排出先后。经典 = 被论文库很多篇引用的工作（按方向分别取候选，避免论文多的方向把别的方向挤掉）；趋势、分类、评测集计数直接按数据算；发展阶段、里程碑、研究空白由大模型写，只能引用真实论文。

## 批量下载 PDF

```bash
nohup .venv/bin/python -m pwb fetch-pdfs > data/pdf-download.log 2>&1 &   # 下载所有还没有 PDF 的论文，可中断后重跑
.venv/bin/python -m pwb fetch-pdfs --missing                              # 列出没下到的（给你手动上传）
```

- 来源顺序：ACL Anthology → 出版方的开放获取链接 → arXiv；都不行时按标题到 arXiv 找同名论文。
- 只用公开、合法的来源，不碰付费墙；被挡住的（IEEE / ACM 等非开放获取）需要你在面板里手动上传。
- 每个网站单独限速：arXiv 要求程序访问间隔至少 3 秒，所以近千篇要一个小时左右。

## 用 DBLP 核实中稿

DBLP 的在线接口前面有人机验证，后端不绕过；改用 DBLP 官方的整库数据（约 1.1 GB）建一个只含目标会议 / 期刊、2022 年以后论文的小索引，再按标题核实：

```bash
# 下载整库：Dagstuhl 的月度存档比 dblp.org 快很多；dblp.dtd 要和整库放在同一目录
mkdir -p data/dblp && cd data/dblp
curl -L -o dblp.dtd https://dblp.org/xml/dblp.dtd
curl -L -o dblp.xml.gz https://drops.dagstuhl.de/storage/artifacts/dblp/xml/2026/dblp-2026-10-03.xml.gz   # 换成最新月份
cd ../..
.venv/bin/python -m pwb dblp-index     # 建索引 data/dblp/index.sqlite3（之后整库可以删掉）
.venv/bin/python -m pwb dblp-verify    # 核实论文库里“待核实”的论文
```

一步到位（断点续传下载 → 建索引 → 核实 → 删整库，可以放着过夜跑）：

```bash
nohup tools/dblp_update.sh > data/dblp/update.log 2>&1 &
```

核实结果：在目标会议 / 期刊的主会里找到的改为“已发表”，会议和年份以 DBLP 为准，主 ID 换成 DBLP 正式编号（旧 ID 留作别名）；只在 Findings / Workshop 里找到的标“不属于检索范围”；没找到的保持“待核实”。

## 修改约定

1. 改 `tools/gen_schemas.py`（schema 的源头）和 `tools/gen_examples.py`，生成到 vault：
   ```bash
   .venv/bin/python tools/gen_schemas.py <你的 vault>/论文工作台/后端约定/schemas
   .venv/bin/python tools/gen_examples.py <你的 vault>/论文工作台
   ```
2. 同步改 `数据格式约定.md`，然后 `sync-contract` 并跑测试。

## 还没做的

- 流水线：抓取、版本检查（中稿换 ID，存储层已有 `Store.rename_paper`；定期用新的 DBLP 月度存档跑 dblp-index / dblp-verify）、相关性判定、速读、三类推荐、精读、结果表、发展史、经典、候选关键词、研究线、热点、报告、Telegram 推送。
- 任务：`deep-read`、`generate-report`、`retry-step`、`ingest-paper` 还没实现（面板上显示“即将上线”）。
- GPU 服务器上的实验守护进程（向 `inbox` 上报运行记录）。
- 开机自启、每日定时运行（launchd）。

## 许可证

[MIT](LICENSE)
