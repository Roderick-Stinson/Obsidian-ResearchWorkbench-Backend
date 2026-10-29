"""把约定里的示例数据写进后端，方便前端调试。示例不是真实结果。"""
import json
from pathlib import Path

from .store import DOC_KINDS


def paper_from_recommendation(p):
    """推荐里的论文先按最少信息建档，这样前端能对它们评分、标记。"""
    return {"id": p["id"], "title": p["titleEn"], "titleZh": p["titleZh"], "authors": [], "firstAuthor": "",
            "year": int(p["publishedAt"][:4]), "venue": "arXiv", "publicationStatus": "预印本",
            "versions": [{"id": p["id"], "kind": "arxiv", "label": p["id"], "url": p["url"], "date": p["publishedAt"]}],
            "urls": {"source": p["url"], "pdf": None, "code": p["codeUrl"]}, "hasPdf": False, "pdfSha256": None,
            "citations": p["citations"], "metadataStatus": "待核实", "summary": p["summary"],
            "matchedKeywords": p["matchedKeywords"], "hasDeepRead": False,
            "addedAt": p["publishedAt"] + "T00:00:00+08:00"}


def seed_examples(store, examples_dir, log=print):
    bodies = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(examples_dir) / "responses").glob("*.json"))]
    for body in bodies:
        if body["kind"] in DOC_KINDS:
            store.put_doc(body)
            log("示例数据：" + body["kind"])
        elif body["kind"] == "paper":
            store.upsert_paper(body, body["aliases"])
            log("示例论文：" + body["id"])
    for body in bodies:
        if body["kind"] == "recommendations-day":
            for p in body["papers"]:
                if not store.resolve(p["id"]):
                    store.upsert_paper(paper_from_recommendation(p))
                    log("示例论文：" + p["id"])
    for body in bodies:
        if body["kind"] == "deep-read" and store.resolve(body["paperId"]):
            store.put_deep_read(body)
            log("示例精读：" + body["paperId"])
