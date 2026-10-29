import hashlib

from pwb.importer import import_vault

from .conftest import API, assert_contract

RECORD = """---
type: "paper-record"
paper_id: "openreview-imcyVlzpXh"
title: "Multi-agent Architecture Search via Agentic Supernet"
paper_roles: ["重点关注"]
discussion_status: "待讨论"
year: 2025
first_author: "Guibin Zhang"
publication_status: "已发表"
venue: "ICML 2025"
source_url: "https://openreview.net/pdf?id=imcyVlzpXh"
code_url: "https://github.com/bingreeky/MaAS"
paper_pdf: "[[论文库/PDF/openreview-imcyVlzpXh-arxiv.pdf]]"
pdf_sha256: "{sha}"
metadata_status: "部分核实"
updated_at: "2026-10-27T09:45:56.236380+00:00"
pdf_source_url: "https://arxiv.org/pdf/2502.04180"
paper_version: "arXiv:2502.04180v2；未逐字核对 OpenReview 版本"
---

## 收录信息
"""
OWN = """---
type: "paper-record"
paper_id: "openreview-Xz0wn1Pp3r"
title: "openreview-Xz0wn1Pp3r"
paper_roles: ["本人投稿"]
discussion_status: "待讨论"
year: null
first_author: ""
publication_status: "投稿中"
venue: ""
source_url: "https://openreview.net/pdf?id=Xz0wn1Pp3r"
paper_pdf: ""
metadata_status: "待核实"
---
"""


def make_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / "论文库/条目").mkdir(parents=True)
    (vault / "论文库/PDF").mkdir(parents=True)
    pdf = b"%PDF-1.4 fake"
    (vault / "论文库/PDF/openreview-imcyVlzpXh-arxiv.pdf").write_bytes(pdf)
    (vault / "论文库/条目/openreview-imcyVlzpXh.md").write_text(RECORD.replace("{sha}", hashlib.sha256(pdf).hexdigest()), encoding="utf-8")
    (vault / "论文库/条目/openreview-Xz0wn1Pp3r.md").write_text(OWN, encoding="utf-8")
    (vault / "论文库/条目/notes.md").write_text("普通笔记\n", encoding="utf-8")
    return vault, pdf


def test_import_vault(client, store, tmp_path):
    vault, pdf = make_vault(tmp_path)
    count, warnings = import_vault(store, vault, log=lambda _: None)
    assert count == 2
    assert any("没有标题" in w for w in warnings)

    paper = assert_contract(client.get(API + "/papers/arxiv-2502.04180").json(), "paper")
    assert paper["id"] == "openreview-imcyVlzpXh" and paper["aliases"] == ["arxiv-2502.04180"]
    assert paper["hasPdf"] and paper["pdfSha256"] == hashlib.sha256(pdf).hexdigest()
    assert [v["kind"] for v in paper["versions"]] == ["openreview", "arxiv"]
    assert paper["versions"][1]["label"] == "arXiv:2502.04180v2"
    assert paper["user"]["watched"] and paper["user"]["discussionStatus"] == "待讨论"
    res = client.get(API + "/papers/openreview-imcyVlzpXh/pdf")
    assert res.status_code == 200 and res.content == pdf and res.headers["content-type"] == "application/pdf"

    own = assert_contract(client.get(API + "/papers/openreview-Xz0wn1Pp3r").json(), "paper")
    assert own["user"]["own"] and not own["user"]["watched"] and not own["hasPdf"]

    assert (vault / "论文库/PDF/openreview-imcyVlzpXh-arxiv.pdf").exists(), "导入只读 vault"


def test_reimport_keeps_user_changes(client, store, tmp_path):
    vault, _ = make_vault(tmp_path)
    import_vault(store, vault, log=lambda _: None)
    client.patch(API + "/me/papers/openreview-imcyVlzpXh", json={"watched": False, "rating": 3})
    count, _ = import_vault(store, vault, log=lambda _: None)
    assert count == 2
    user = client.get(API + "/papers/openreview-imcyVlzpXh").json()["user"]
    assert user["watched"] is False and user["rating"] == 3
