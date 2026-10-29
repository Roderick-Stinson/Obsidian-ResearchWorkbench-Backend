"""把一次关键词整理结果（schema interests-extraction）写进关键词树。

整理可以由任何大模型或人来做，产物都走这里：先按约定校验，再核对每条论文证据的原文摘录确实出现在该论文的
PDF 里，全部通过才合并。这样关键词树里不会出现编造的证据。
"""
import re
from functools import lru_cache

from pypdf import PdfReader

from . import contract, interests


def _normalize(text):
    # PDF 抽出来的文字常有断行连字符、多余空白，比较前统一掉。
    text = re.sub(r"-\s*\n\s*", "", text)
    return re.sub(r"\s+", "", text).lower()


@lru_cache(maxsize=32)
def _pdf_text(path, mtime):
    return _normalize("\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages))


def check_quotes(store, extraction):
    """返回问题列表；为空表示所有证据都核对通过。"""
    problems = []
    for node, level, _ in interests.walk(extraction["domains"]):
        for e in node["evidence"]:
            pid = store.resolve(e["ref"])
            if not pid:
                problems.append("“{}”的证据论文不在论文库：{}".format(node["label"], e["ref"]))
                continue
            e["ref"] = pid
            if not e.get("quote"):
                continue
            pdf = store.pdf_path(pid)
            if not pdf or not pdf.exists():
                problems.append("“{}”引用了 {} 的原文，但后端没有这篇的 PDF，无法核对".format(node["label"], pid))
            elif _normalize(e["quote"]) not in _pdf_text(pdf, pdf.stat().st_mtime):
                problems.append("“{}”的摘录在 {} 原文里找不到：{}".format(node["label"], pid, e["quote"][:60]))
    return problems


def apply_extraction(store, extraction):
    contract.check("interests-extraction", extraction)
    problems = check_quotes(store, extraction)
    if problems:
        raise ValueError("整理结果没有通过核对：\n" + "\n".join(problems))
    tree = interests.merge_extracted(store.get_kv("interests"), extraction, store.get_kv("removed-extracted") or [])
    contract.check("interests", {"schemaVersion": 1, "kind": "interests", **tree})
    return store.set_kv("interests", tree)
