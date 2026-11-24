"""Back up and repair legacy index links. Run with --apply to write changes."""
import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from pwb.config import Settings
from pwb.dblp import _formal_id
from pwb.paper_links import links_for, repository
from pwb.store import Store
from pwb.survey import _norm_title


def code_from_pdf(path):
    try:
        text = PdfReader(path).pages[0].extract_text()
    except Exception:
        return None
    start = text.lower().find("abstract")
    if start < 0:
        return None
    abstract = text[start + len("abstract"):]
    end = abstract.lower().find("introduction")
    return repository(abstract[:end]) if end >= 0 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--comas-pdf", type=Path)
    args = parser.parse_args()
    store = Store(Settings())
    index = store.settings.data_dir / "dblp" / "index.sqlite3"
    with store.conn() as db, sqlite3.connect(index.as_uri() + "?mode=ro", uri=True) as dblp:
        rows = db.execute("SELECT id, data FROM papers WHERE json_extract(data, '$.urls.source') LIKE ?", ("%semanticscholar.org/%",)).fetchall()
        metadata = {row["paper_id"]: json.loads(row["data"]) for row in db.execute(
            "SELECT paper_id, data FROM survey_candidates WHERE paper_id IS NOT NULL ORDER BY updated_at")}
        if args.apply:
            backup = store.settings.data_dir / ("workbench.before-links-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".sqlite3")
            with sqlite3.connect(backup) as snapshot:
                db.backup(snapshot)
            print("Backup:", backup, flush=True)
        report = Counter(scanned=len(rows))
        for row in rows:
            paper = store.get_paper(row["id"])
            if not paper:
                continue
            previous = paper["urls"]
            official = []
            ids = {paper["id"], *paper["aliases"]}
            hits = dblp.execute("SELECT key, ee FROM pubs WHERE norm_title = ?", (_norm_title(paper["title"]),)).fetchall()
            matched = [hit for hit in hits if _formal_id(hit[0]) in ids]
            for hit in matched or (hits if len(hits) == 1 else []):
                official.extend(json.loads(hit[1]))
            urls = links_for(paper, metadata.get(row["id"]), official=official)
            comas = row["id"] == "dblp-conf_iclr_XueZZZLZYTOB26"
            cached = store.pdf_path(row["id"]) if paper["hasPdf"] else None
            if not urls["code"] and cached:
                urls["code"] = code_from_pdf(cached)
            if comas and args.comas_pdf:
                urls["code"] = "https://github.com/xxyQwQ/CoMAS"
            if not urls["source"]:
                report["source_unresolved"] += 1
                urls["source"] = previous["source"]
            for field in ("source", "pdf", "code"):
                if urls.get(field) != previous.get(field):
                    report[field + "_updated"] += 1
                if not urls.get(field):
                    report[field + "_missing"] += 1
            if urls != previous:
                report["records_updated"] += 1
                if args.apply:
                    store.upsert_paper({**paper, "urls": urls}, paper["aliases"])
            if args.apply and comas and args.comas_pdf and not cached:
                body = args.comas_pdf.read_bytes()
                if not body.startswith(b"%PDF-"):
                    raise ValueError("CoMAS input is not a PDF")
                target = store.settings.pdf_dir / (row["id"] + ".pdf")
                target.write_bytes(body)
                store.set_pdf(row["id"], target.name, hashlib.sha256(body).hexdigest())
                report["pdf_cached"] += 1
        print(json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
