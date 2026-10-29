"""研究兴趣关键词树（约定 5.7）。

树里的关键词有两种来源：你自己填写的（origin=user），和后端根据你关注的论文、本人论文、标为相关的论文
整理出来的（origin=extracted；采纳的候选为 candidate）。重新整理只替换整理所得的部分，你填写的和固定的都保留。
这里都是对 interests 字典的纯函数，失败时抛 ApiError。
"""
import secrets

from .errors import ApiError, not_found
from .util import now

ROOT = "root"


def new_tree():
    return {"generatedAt": now(), "overview": "", "basis": {"ownPapers": 0, "watchedPapers": 0, "relevantPapers": 0},
            "stale": True, "domains": [], "candidates": []}


def _node(label, origin, level, summary="", evidence=()):
    node = {"id": "kw-" + secrets.token_hex(4), "label": label, "summary": summary, "origin": origin,
            "pinned": False, "evidence": list(evidence)}
    if level < 3:
        node["children"] = []
    return node


def walk(nodes, level=1, parent=None):
    for node in nodes:
        yield node, level, parent
        yield from walk(node.get("children", []), level + 1, node)


def _find(tree, node_id):
    for node, level, parent in walk(tree["domains"]):
        if node["id"] == node_id:
            return node, level, parent
    raise not_found("关键词不存在：" + node_id)


def _check_unique(siblings, label, except_id=None):
    key = label.strip().lower()
    if any(n["label"].strip().lower() == key and n["id"] != except_id for n in siblings):
        raise ApiError(409, "同一级已有同名关键词：" + label.strip())


def add_node(tree, parent_id, label, summary=""):
    """你手动添加关键词。parent_id 为 root 时添加一级领域。"""
    tree = tree or new_tree()
    label = label.strip()
    if parent_id == ROOT:
        siblings, level = tree["domains"], 1
    else:
        parent, parent_level, _ = _find(tree, parent_id)
        if parent_level >= 3:
            raise ApiError(409, "关键词最多三级，三级关键词下不能再添加")
        siblings, level = parent["children"], parent_level + 1
    _check_unique(siblings, label)
    siblings.append(_node(label, "user", level, summary.strip()))
    return tree


def patch_node(tree, node_id, patch):
    """改名或改说明。改过的整理所得关键词归为你填写的，重新整理时不再被替换。"""
    node, level, parent = _find(tree, node_id)
    if "label" in patch:
        _check_unique(parent["children"] if parent else tree["domains"], patch["label"], node_id)
        node["label"] = patch["label"].strip()
    if "summary" in patch:
        node["summary"] = patch["summary"].strip()
    node["origin"] = "user"
    return tree


def label_path(tree, node_id):
    """从一级到该节点的名称路径（小写），用来记住你删掉的整理所得关键词。"""
    def search(nodes, trail):
        for n in nodes:
            here = trail + [n["label"].strip().lower()]
            if n["id"] == node_id:
                return here
            found = search(n.get("children", []), here)
            if found:
                return found
    return search(tree["domains"], [])


def node_action(tree, node_id, action):
    node, _, parent = _find(tree, node_id)
    if action in ("pin", "unpin"):
        node["pinned"] = action == "pin"
    elif action == "remove":
        siblings = parent["children"] if parent else tree["domains"]
        siblings[:] = [n for n in siblings if n["id"] != node_id]
        removed = {n["id"] for n, _, _ in walk([node])}
        tree["candidates"] = [c for c in tree["candidates"] if c["parentId"] not in removed]
    else:
        raise not_found("不支持的操作：" + action)
    return tree


def candidate_action(tree, candidate_id, action, rejected):
    """采纳或忽略候选关键词。忽略的记进 rejected，流水线以后不再提同一个。"""
    candidate = next((c for c in tree["candidates"] if c["id"] == candidate_id), None)
    if candidate is None:
        raise not_found("候选关键词不存在：" + candidate_id)
    if action == "accept":
        try:
            parent, parent_level, _ = _find(tree, candidate["parentId"])
        except ApiError:
            raise ApiError(409, "候选关键词的上级已被删除")
        if parent_level != candidate["level"] - 1:
            raise ApiError(409, "候选关键词的层级与上级不符")
        evidence = [{"kind": "paper", "ref": p["id"], "title": p["title"], "quote": None}
                    for p in candidate["examples"] if p.get("id")]
        if not evidence:
            raise ApiError(409, "候选关键词没有可作为证据的论文")
        _check_unique(parent["children"], candidate["label"])
        parent["children"].append(_node(candidate["label"], "candidate", candidate["level"], candidate["reason"], evidence))
    elif action == "reject":
        rejected.append({"label": candidate["label"], "parentId": candidate["parentId"], "rejectedAt": now()})
    else:
        raise not_found("不支持的操作：" + action)
    tree["candidates"] = [c for c in tree["candidates"] if c["id"] != candidate_id]
    return tree, rejected


def _keep(node):
    """重新整理时要保留的节点：你填写的、采纳的候选、你固定的，以及它们的上级。"""
    if node["origin"] != "extracted" or node["pinned"]:
        return True
    return any(_keep(c) for c in node.get("children", []))


def _prune(nodes):
    kept = []
    for node in nodes:
        if not _keep(node):
            continue
        if "children" in node:
            node["children"] = _prune(node["children"])
        kept.append(node)
    return kept


def _graft(existing, incoming, level, removed, trail=()):
    """把新整理出的节点按名称并入现有同级节点：同名的合并下级（整理所得的同时更新说明和证据），其余新增。
    你删掉过的整理所得关键词（removed 里的名称路径）不再加回来。"""
    for item in incoming:
        path = list(trail) + [item["label"].strip().lower()]
        if path in removed:
            continue
        match = next((n for n in existing if n["label"].strip().lower() == item["label"].strip().lower()), None)
        if match is None:
            match = _node(item["label"].strip(), "extracted", level, item["summary"], item["evidence"])
            existing.append(match)
        elif match["origin"] == "extracted":
            match["summary"], match["evidence"] = item["summary"], item["evidence"]
        if level < 3:
            _graft(match["children"], item.get("children", []), level + 1, removed, path)


def merge_extracted(tree, extraction, removed=()):
    """用一次整理结果（schema interests-extraction）更新关键词树。"""
    tree = tree or new_tree()
    tree["domains"] = _prune(tree["domains"])
    _graft(tree["domains"], extraction["domains"], 1, list(removed))
    ids = {n["id"] for n, _, _ in walk(tree["domains"])}
    tree["candidates"] = [c for c in tree["candidates"] if c["parentId"] in ids]
    tree.update(overview=extraction["overview"], basis=extraction["basis"], generatedAt=now(), stale=False)
    return tree
