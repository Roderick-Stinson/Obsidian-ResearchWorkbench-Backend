"""后台长任务的进度，给面板展示。这些任务在后端之外单独运行（nohup），这里只读它们留下的状态，不控制它们。

- pdf-download：pwb fetch-pdfs，状态在 user_kv 的 pdf-fetch 里；
- dblp-verify：tools/dblp_update.sh，看 data/dblp 下的整库、索引和日志。
"""
import json
import re
import time
from datetime import datetime

from .util import now

ACTIVE_SECONDS = 300  # 最近 5 分钟有进展就算“进行中”


def _age(ts):
    try:
        return time.time() - datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return None


def pdf_download(store):
    with store.conn() as c:
        row = c.execute("SELECT value, updated_at FROM user_kv WHERE key = 'pdf-fetch'").fetchone()
        missing = c.execute("SELECT COUNT(*) FROM papers WHERE pdf_file IS NULL").fetchone()[0]
        total = c.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    if row is None:
        return {"key": "pdf-download", "name": "下载论文 PDF", "status": "idle", "progress": None,
                "message": "还没运行过；论文库里 {} 篇没有 PDF".format(missing), "updatedAt": None}
    state = json.loads(row["value"])
    counts = {}
    for s in state.values():
        counts[s.get("status")] = counts.get(s.get("status"), 0) + 1
    age = _age(row["updated_at"])
    running = age is not None and age < ACTIVE_SECONDS and missing > (counts.get("none", 0) + counts.get("blocked", 0) + counts.get("error", 0))
    message = "论文库 {} 篇里 {} 篇已有 PDF；没有公开来源 {} 篇，被付费墙挡住 {} 篇".format(
        total, total - missing, counts.get("none", 0), counts.get("blocked", 0))
    return {"key": "pdf-download", "name": "下载论文 PDF", "status": "running" if running else "done",
            "progress": {"done": total - missing, "total": total}, "message": message, "updatedAt": row["updated_at"]}


def dblp_verify(store):
    d = store.settings.data_dir / "dblp"
    log = d / "update.log"
    dump, index = d / "dblp.xml.gz", d / "index.sqlite3"
    last = log.read_text(encoding="utf-8").strip().splitlines()[-1] if log.exists() and log.read_text(encoding="utf-8").strip() else ""
    result = re.search(r"核实完成：.*", log.read_text(encoding="utf-8")) if log.exists() else None
    recent = max((p.stat().st_mtime for p in (log, dump, index) if p.exists()), default=0)
    active = time.time() - recent < ACTIVE_SECONDS
    updated = datetime.fromtimestamp(recent).astimezone().isoformat(timespec="seconds") if recent else None
    base = {"key": "dblp-verify", "name": "用 DBLP 核实“待核实”论文", "updatedAt": updated}
    if result:
        return {**base, "status": "done", "progress": None, "message": result.group(0)}
    if dump.exists() and active:
        size = dump.stat().st_size
        m = re.search(r"已扫描 (\d+) 条", last)
        if m:
            return {**base, "status": "running", "progress": None, "message": "正在建索引：已扫描 {} 条记录".format(m.group(1))}
        return {**base, "status": "running", "progress": {"done": size // 1000000, "total": 1107},
                "message": "正在下载 DBLP 整库：{} MB / 约 1107 MB（对方服务器限速）".format(size // 1000000)}
    if dump.exists() or log.exists():
        return {**base, "status": "stalled", "progress": None, "message": "超过 5 分钟没有进展：" + (last or "看 data/dblp/update.log")}
    return {**base, "status": "idle", "progress": None, "message": "还没运行过：nohup tools/dblp_update.sh > data/dblp/update.log 2>&1 &"}


def all_jobs(store):
    return {"items": [pdf_download(store), dblp_verify(store)], "generatedAt": now()}
