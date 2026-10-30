# 论文工作台 · 后端

> A local research-paper workbench backend (FastAPI + SQLite) for an Obsidian dashboard: daily recommendations from arXiv and Hugging Face Daily Papers (screened against your interest keyword tree, skimmed by an LLM), a paper library for your research directions, and summaries of classics, must-reads and field-wide hotspot trends. LLM: local Claude Code / Codex CLI / DeepSeek. Everything about your research directions lives in a config file, not in code. Docs are in Chinese.

Obsidian 论文面板的后端服务。面板（前端）在另一个仓库：[Obsidian-ResearchWorkbench-Frontend](https://github.com/Roderick-Stinson/Obsidian-ResearchWorkbench-Frontend)。接口按 `contract/数据格式约定.md` 实现；约定的源头在面板仓库的 `论文工作台/后端约定/`，这里的 `contract/` 是同步过来的副本。

已经能用的：每日推荐（arXiv + Hugging Face 每日论文 → 初筛 → 核心 / 邻近 / 热点三类 → 速读 → 精选和简报，每周新中稿入库）、论文库（领域检索入库、DBLP 核实中稿、批量下载公开 PDF）、我的标记与阅读队列、阅读笔记（面板同步过来，推荐时参考）、作者机构与团队（OpenAlex + PDF 第一页认通讯作者，按机构聚合、关注的人的新论文进推荐）、我的研究领域（关键词树的填写与大模型整理）、经典与必读、领域发展史（含按论文流分开的热点演变）。还没生成的数据接口返回 404，还没实现的后台任务（精读、周报等）会以“尚未实现”失败。

**不写死**：和你研究方向有关的文字（检索词、方向说明、arXiv 论文流、会议清单）都在 `data/profile.json`；每日推荐怎么跑（时间、来源开关、阈值、上限、权重）在面板“设置 → 每日推荐”；时区用 `PWB_TZ`。

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
  profile.py    读 data/profile.json（研究方向配置）
  daily.py      每日推荐流水线、推荐设置、定时器
  notes.py      阅读笔记：同步、要追的论文进队列、阅读要点和候选关键词
  authors.py    作者机构：OpenAlex、PDF 第一页、中文机构名；团队聚合；关注的人
  survey.py     领域检索入库：Semantic Scholar 抓候选 → 大模型按标题、摘要筛选 → 写进论文库
  pdfmeta.py    从 PDF 第一页读标题
  dblp.py       用 DBLP 整库核实“待核实”论文是否中稿
  pdfs.py       批量下载论文库的 PDF（只用公开来源）
  landscape.py  经典与必读、领域发展史（参考文献统计 + 大模型整理）
  feed.py       arXiv 论文流（按 profile.json 的 streams，每个流各取各的）
  hotspots.py   热点演变（每个论文流的词组占比统计 + 大模型归类）
  service.py    开机自启（macOS launchd / Linux systemd）
  migrate.py    启动时把旧格式的数据改成新格式
  jobs.py       后台长任务（PDF 下载、DBLP 核实）的进度
  importer.py   从 vault 一次性导入论文条目和 PDF
  seed.py       写入约定里的示例数据
contract/       约定副本：schemas、examples、validate.py
tools/          生成 schemas 和 examples 的脚本（改约定时用）
tests/          契约测试：每个接口的返回体都按 schema 校验
data/           数据（不进版本库）：workbench.sqlite3、pdfs/、profile.json（你的研究方向）、cache/（每日速读下的 PDF）
profile.example.json  研究方向配置的示例
```

迁移到服务器时，把整个文件夹拷过去即可（`data/` 里就是全部数据）。**不要把 `data/` 放进 Dropbox 等同步盘**，SQLite 数据库被同步工具改动容易损坏。

## 安装

```bash
git clone https://github.com/Roderick-Stinson/Obsidian-ResearchWorkbench-Backend.git ~/PaperWorkbench
cd ~/PaperWorkbench
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 从零开始

新用户装好以后，按下面的顺序配置。只有第 2 步要手写配置文件，其余在面板上点或者运行一条命令。

1. **装后端**：见上面的“安装”。
2. **写研究方向**（唯一必须手写的配置文件）：
   ```bash
   mkdir -p data && cp profile.example.json data/profile.json
   ```
   然后按你的方向改 `data/profile.json`（旧版本的 `data/survey.json` 也照样能读）：

   | 字段 | 用在哪里 | 写什么 |
   |---|---|---|
   | `queries` | 领域检索入库 | Semantic Scholar 检索词（`+` 与，`\|` 或，`""` 短语，`*` 前缀） |
   | `direction` | 筛选论文、整理经典与发展史 | 给大模型看的方向说明，写清楚什么算相关、什么不算 |
   | `extraTopics` | 筛选论文 | 关键词树以外、筛选时也可以归入的方向（可以为空） |
   | `direction` | 也用在每日初筛 | 同上 |
   | `streams` | 每日推荐、热点演变 | arXiv 论文流，可以有好几个，每个是 `{key, name, query, categories}`：`key` 是小写英文标识；`name` 是中文名（如“智能体”）；`query` 是 arXiv 检索词，如 `abs:agent`（`abs:` 摘要、`ti:` 标题，可以用 `AND` / `OR`）；`categories` 是保留的分类前缀，默认 `["cs."]`（计算机类） |
   | `venues` | 领域检索、核实中稿 | 只用内置会议 / 期刊清单（CCF-A 等，见 `pwb/survey.py` 的 `VENUES`）里的哪些，写简称列表；`null` 或不写就全用 |
   | `venuesExtra` | 同上 | 清单以外的会议 / 期刊：`{"简称": {"names": ["Semantic Scholar 里的全称"], "kind": "proceedings" 或 "journal"}}`（DBLP 核实只支持内置清单） |

3. **准备大模型**：默认用本机 Claude Code，装好后运行一次 `claude` 登录即可，不需要配置文件。用 DeepSeek 的话，写 `data/secrets.json`：`{"deepseekApiKey": "sk-..."}`，再在面板“设置 → 大模型引擎”里切换。
4. **启动后端**：`.venv/bin/python -m pwb serve`。
5. **装面板**：见[面板仓库](https://github.com/Roderick-Stinson/Obsidian-ResearchWorkbench-Frontend)的 README。后端不在默认地址时，在面板“设置 → 后端连接”里改。
6. **写下研究领域**：面板“我的研究领域” → “添加一级领域”，写你的大方向（说明里写清楚包括什么、不包括什么）。
7. **加几篇代表论文**：
   - 面板“关注论文” → “添加关注论文”，贴 arXiv 链接即可，后端会在后台补上标题、作者、摘要并下载 PDF；
   - 自己的论文在“我的研究领域” → “添加本人论文”，没公开的稿子填标题、上传 PDF。
8. **整理关键词**：“我的研究领域” → “重新整理关键词”。大模型读这些论文的 PDF，整理出二、三级关键词；没有 PDF 的论文只能用标题和摘要，结果里会提示。
9. **建论文库**（在干净的终端里运行，不要在 Claude Code 会话里）：
   ```bash
   .venv/bin/python -m pwb survey        # 按 survey.json 检索、大模型筛选、入库
   .venv/bin/python -m pwb fetch-pdfs    # 下载论文库的公开 PDF
   ```
10. **热点、经典与发展史**：
    ```bash
    .venv/bin/python -m pwb feed          # 取 arXiv 论文流
    .venv/bin/python -m pwb landscape     # 经典与必读、领域发展史（含热点演变）
    ```
11. **每日推荐**：后端开着就会按“设置 → 每日推荐”里的时间（默认 01:00）自动运行；想马上看到，在面板“论文推荐”点“立刻刷新今天的推荐”。让后端开机自启：
    ```bash
    .venv/bin/python -m pwb service install
    ```

之后在论文库里给论文标“相关” / “重点关注”，再“重新整理关键词”，关键词树会跟着你的标记变化。

可选的配置：环境变量见下面的“配置”；`S2_API_KEY`（Semantic Scholar 的免费 key）能让检索快很多。

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
| `PWB_TZ` | 系统时区 | 时区（IANA 名字，如 `Asia/Shanghai`、`America/New_York`）：每日推荐的运行时间、“今天”是哪天都按它算，面板也跟着它显示时间 |
| `S2_API_KEY` | 空 | Semantic Scholar 的 API key（免费申请），有了检索、取引用关系快很多 |
| `OPENALEX_API_KEY` | 空 | OpenAlex 的 API key（免费申请：https://help.openalex.org/api/authentication/ ），也可以写进 `data/secrets.json` 的 `openalexApiKey`。没有 key 时同一个 IP 每天只有很少的共享免费额度，整理作者机构会很慢 |
| `OPENALEX_MAILTO` | 空 | 你的邮箱；查 OpenAlex 时带上 |
| `PWB_CLAUDE_BIN` | 自动查找 | `claude` 命令的路径（PATH 和 `~/.local/bin` 里找不到时设置） |
| `PWB_CODEX_BIN` | 自动查找 | `codex` 命令的路径 |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek API key；也可以写进 `data/secrets.json`：`{"deepseekApiKey": "sk-..."}` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek 接口地址 |

## 大模型引擎

整理关键词、每日初筛和速读等需要大模型的工作，用哪个引擎在面板“设置 → 大模型引擎”里切换（默认本机 Claude Code）。

- **Claude Code**：调用本机 `claude -p`，用你已登录的账号（不需要 API key）。在临时空目录里运行，不给任何工具，只读给它的材料、输出 JSON。
- **Codex CLI**：调用本机 `codex exec`（只读沙箱）。
- **DeepSeek**：调用 DeepSeek 的 chat completions 接口。

不管用哪个引擎，结果都会先按约定校验，再逐条核对原文摘录确实出自论文；对不上的让模型重写一次，仍对不上就去掉摘录、只保留论文出处。

手动整理的结果（JSON）也可以直接并入：`.venv/bin/python -m pwb apply-interests 文件.json`。

## 领域检索入库

在 CCF-A 会议 / 期刊（各领域）、ICLR、EMNLP / NAACL、Nature 及子刊里，按检索词找 2023–2026 年和你方向相关的论文，写进论文库。

先写好 `data/profile.json` 里的检索词（`queries`）、方向说明（`direction`）、关键词树以外的方向（`extraTopics`），要换会议范围就改 `venues` / `venuesExtra`（见“从零开始”）。

```bash
.venv/bin/python -m pwb survey                         # 全流程：fetch → titles → abstracts → ingest
.venv/bin/python -m pwb survey --stages titles --limit 300   # 只跑某一步、先试一小批
.venv/bin/python -m pwb survey --stages reingest       # 已入库的重新写一遍（不调用大模型）
```

- 内置的会议清单在 `pwb/survey.py` 的 `VENUES`；用哪些、加哪些在 `data/profile.json` 里配。
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

热点演变要先取 arXiv 论文流：按 `data/profile.json` 里每个 `streams` 的检索词，按月取标题和摘要（默认只保留计算机类），每个流各算各的热点。第一次每个流约 40 个请求、几分钟，之后只重取最近两个月；改了某个流的检索词，这个流会全部重取：

```bash
.venv/bin/python -m pwb feed                      # 默认从 2023-07 起
```

然后整理领域发展史时，会统计哪些词组的占比涨得最多、由大模型归成热点，按起势时间排出先后。经典 = 被论文库很多篇引用的工作（按方向分别取候选，避免论文多的方向把别的方向挤掉）；趋势、分类、评测集计数直接按数据算；发展阶段、里程碑、研究空白由大模型写，只能引用真实论文。

## 每日推荐

后端开着就会按面板“设置 → 每日推荐”里的时间（默认每天 01:00，按 `PWB_TZ`）自动跑一次；错过了（电脑睡眠、后端没开）会在下次开着时补跑，补抓的天数有上限。也可以手动跑：

```bash
.venv/bin/python -m pwb daily                  # 跑一次（和面板“立刻刷新今天的推荐”一样）
.venv/bin/python -m pwb daily --step skim      # 只跑某一步：fetch / version-check / affiliations / notes / classify / recommend / skim / pick / new-accepted / map / classics
```

流程（每篇候选论文做到哪一步记在 `daily_candidates` 表里，中断后重跑会接着做）：

1. **抓取**：`profile.json` 里每个论文流，取上次运行以来新提交的 arXiv 论文；Hugging Face 每日论文全部取来，带点赞数和 GitHub 链接。
2. **核实中稿**：论文库里的预印本，Semantic Scholar 有了正式出版记录（在你的会议范围里）就改为已发表、换正式 ID，在简报里提一句。
3. **整理阅读笔记**：笔记有变化时，大模型把“和我的研究的关系”“想法与疑问”两节整理成阅读要点（你关心的问题、你存疑的思路），顺带提候选关键词（见下面“阅读笔记”）。
4. **初筛**：大模型分批看标题和摘要，依据方向说明、关键词树、阅读要点、你标过“不相关”的论文（反例）。相关且命中关键词、或回应了笔记里关心的问题的是**核心**。
5. **邻近 / 热点**：引用了种子论文（精读过的、本人、关注、相关、论文库核心论文，可配）的是**邻近**；Hugging Face 点赞够多、或命中正在热的热点且有一些点赞的是**热点**（每天有上限）。都不看关键词。
6. **速读**：选了的类别读全文（arXiv PDF，每天有上限），其余只看摘要；中文结论式标题、速读、质量分。
7. **精选和简报**：按相关程度、质量分、点赞、有没有代码加权打分，取前几篇；用了笔记里存疑思路的，相关程度一项打五折。
8. **每周**（设置里的那天）：按领域检索的检索词和会议清单查新中稿，筛选通过的直接入库；更新领域发展史与热点。

推荐出来的论文不进论文库，你标了“相关”才进（设置里可以让某几类直接进）。全文速读用本机 Claude Code 时每篇约 1 分钟，几十篇要半小时以上，所以放在凌晨。

### 作者机构与团队

每篇论文记下每位作者的机构：先按标题查 OpenAlex（免费，带机构编号和上级机构），再让大模型读 PDF 第一页，认出通讯作者、补上 OpenAlex 没有的机构和院系 / 实验室（读了 PDF 的以 PDF 为准：OpenAlex 有时把全部机构挂到每位作者头上）。机构的中文名由大模型翻译一次存下来。每日流水线的“补作者机构”一步每天补新进论文库的（默认 60 篇）；全库一次补完用面板“团队”页的“整理作者机构”（任务 `rebuild-affiliations`，`readPdf: false` 只查 OpenAlex，几分钟；读 PDF 的部分以后每天补）。OpenAlex 现在要 API key（免费申请，见上面“配置”里的 `OPENALEX_API_KEY`）：没有 key 时整个 IP 每天只有很少的共享额度，用完了这次就不再往下查，没查到的论文留到下次（每天 UTC 0 点重置）。

“团队”页按上级机构聚合论文库，越相关的论文权重越高；每个机构里按当通讯作者 / 末位作者的次数列出人，你挑要关注的。关注的人的新论文进每日推荐（邻近类）：按 OpenAlex 作者编号取他们的新论文，每天的新论文里同名作者也算（可能撞名）。

### 阅读笔记

后端不读 vault：面板打开时（以及笔记改动后）把阅读笔记按小节发过来（`PUT /me/notes`，整体替换），存在 `notes` 表。`我的论文/` 下的、frontmatter 写了 `sync: false` 的不发；模板里没填的占位行发送前就去掉了。笔记的 `paper_id` 可以写 arXiv / OpenReview 链接，后端换成论文 ID、面板改回笔记里。写了笔记的论文都算进论文库。

推荐怎么用笔记（面板“设置 → 每日推荐 → 阅读笔记”，三个小节名也在那里改，要和模板标题一致）：

- 状态是精读 / 已读、或评分 ≥4 的论文当邻近推荐的种子；
- “要追的论文”里的链接、arXiv 编号、论文 ID 加进阅读队列，每篇只加一次；
- “和我的研究的关系”“想法与疑问”整理成阅读要点，初筛和简报时参考，在面板“我的研究领域”里能看到；
- 笔记里反复提到、关键词树里还没有的方向作为候选关键词。

### 开机自启

定时在后端里，后端得一直开着：

```bash
.venv/bin/python -m pwb service install     # macOS：launchd（登录后启动、退出自动拉起）；Linux：systemd 用户服务
.venv/bin/python -m pwb service status
.venv/bin/python -m pwb service uninstall
```

日志在 `data/serve.log`。安装时会把当前终端里的 `PWB_*`、`S2_API_KEY`、`DEEPSEEK_*` 环境变量写进服务配置，改了要重新安装。

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

- 流水线里还没做的：AI 精读草稿、结果表、按论文增长提候选关键词（笔记提的已经有了）、研究线、社区热点、报告、推送（Telegram 等）。DBLP 核实还要定期手动用新的月度存档跑 dblp-index / dblp-verify。
- 任务：`deep-read`、`generate-report`、`ingest-paper` 还没实现（面板上显示“即将上线”）。
- GPU 服务器上的实验守护进程（向 `inbox` 上报运行记录）。

## 许可证

[MIT](LICENSE)
