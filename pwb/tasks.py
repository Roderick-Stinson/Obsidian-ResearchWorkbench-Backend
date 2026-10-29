"""后台任务（约定第 6 节）。单线程按提交顺序执行；骨架阶段只有 claim-run 真正实现。"""
import logging
import threading

from .util import now

log = logging.getLogger("pwb.tasks")


class TaskFailed(Exception):
    """任务按预期失败（参数指向的东西不存在等），message 原样写进 task.error。"""


def claim_run(store, params, progress):
    inbox = store.get_doc("inbox")
    run = next((r for r in (inbox or {}).get("runs", []) if r["runId"] == params["runId"]), None)
    if run is None:
        raise TaskFailed("待认领列表里没有这次运行：" + params["runId"])
    target = dict(params["target"])
    if target["kind"] == "paper":
        resolved = store.resolve(target["id"])
        if not resolved:
            raise TaskFailed("论文不存在：" + target["id"])
        target["id"] = resolved
    elif target["kind"] == "reproGroup":
        groups = (store.get_doc("experiments") or {}).get("reproGroups", [])
        if not any(g["id"] == target["id"] for g in groups):
            raise TaskFailed("复现配置组不存在：" + target["id"])
    run["claimedBy"] = target
    store.put_doc(inbox)
    return "已认领到 {} {}".format(target["kind"], target["id"]), [{"kind": "data", "path": "/inbox"}]


def rebuild_interests(store, params, progress):
    from . import llm, rebuild
    try:
        return rebuild.rebuild_interests(store, llm.get_config(store), progress)
    except (llm.LLMError, ValueError) as e:
        raise TaskFailed(str(e))


def rebuild_classics(store, params, progress):
    """取论文库的参考文献（只补没取过的），再整理经典与必读。"""
    from . import landscape, llm
    try:
        progress(1, 3, "正在从 Semantic Scholar 取参考文献")
        landscape.fetch_references(store, lambda m: progress(1, 3, m))
        progress(2, 3, "正在整理经典工作和必读论文")
        doc = landscape.build_classics(store, llm.get_config(store), lambda m: progress(2, 3, m))
    except (llm.LLMError, ValueError, RuntimeError) as e:
        raise TaskFailed(str(e))
    kept = sum(len(r["items"]) for r in doc["routes"])
    return "经典工作 {} 篇（{} 条技术路线），必读 {} 篇".format(kept, len(doc["routes"]), len(doc["mustRead"])), [{"kind": "data", "path": "/classics"}]


def rebuild_map(store, params, progress):
    """整理领域发展史；还没有经典与必读时先整理它。"""
    from . import landscape, llm
    try:
        if store.get_doc("classics") is None:
            rebuild_classics(store, {}, progress)
        if store.get_kv("feed-months"):
            # 只重新取最近两个月的 arXiv 论文（之前的月份已经取过），几十秒。取不到就用已有的数据。
            from . import feed
            try:
                feed.fetch(store, since=min(store.get_kv("feed-months")), log=lambda m: progress(3, 3, "更新 arXiv 论文流：" + m))
            except Exception as e:  # noqa: BLE001 — 网络问题不影响用已有数据整理
                progress(3, 3, "arXiv 论文流没更新成功（{}），用已有数据".format(type(e).__name__))
        progress(3, 3, "正在整理领域发展史")
        doc = landscape.build_map(store, llm.get_config(store), lambda m: progress(3, 3, m))
    except (llm.LLMError, ValueError, RuntimeError) as e:
        raise TaskFailed(str(e))
    return "{} 个发展阶段、{} 条研究空白与发现".format(len(doc["stages"]), len(doc["gaps"])), [{"kind": "data", "path": "/map"}]


HANDLERS = {"claim-run": claim_run, "rebuild-interests": rebuild_interests, "rebuild-classics": rebuild_classics, "rebuild-map": rebuild_map}


def run_task(store, task):
    handler = HANDLERS.get(task["type"])
    try:
        if handler is None:
            raise TaskFailed("尚未实现：{}（后端还在骨架阶段）".format(task["type"]))
        def progress(done, total, message):
            store.update_task(task["id"], progress={"done": done, "total": total, "message": message})
        message, outputs = handler(store, task["params"], progress)
        store.update_task(task["id"], status="done", message=message, outputs=outputs, progress=None)
    except TaskFailed as e:
        store.update_task(task["id"], status="failed", error=str(e))
    except Exception as e:  # 任务代码出 bug 也不能拖垮整个服务
        log.exception("任务 %s 出错", task["id"])
        store.update_task(task["id"], status="failed", error="后端内部错误：{}: {}".format(type(e).__name__, e))


class Worker:
    def __init__(self, store, interval=1.0):
        self.store, self.interval = store, interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pwb-worker", daemon=True)

    def start(self):
        interrupted = self.store.fail_interrupted_tasks()
        if interrupted:
            log.warning("%s 个任务在上次关闭时中断，已标为失败（%s）", interrupted, now())
        self._thread.start()

    def wake(self):
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=5)

    def _loop(self):
        while not self._stop.is_set():
            task = self.store.claim_next_task()
            if task is None:
                self._wake.wait(self.interval)
                self._wake.clear()
                continue
            run_task(self.store, task)
