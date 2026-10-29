"""用 DBLP 整库核实“待核实”论文：用一个很小的假整库代替 1.1 GB 的真文件。"""
import gzip
import json

from pwb import dblp, survey

from .conftest import API, assert_contract

DTD = '<!ENTITY uuml "&#252;">\n<!ELEMENT dblp ANY>\n'
XML = """<?xml version="1.0" encoding="ISO-8859-1"?>
<!DOCTYPE dblp SYSTEM "dblp.dtd">
<dblp>
<inproceedings key="conf/iclr/RouteM25"><author>Ann M&uuml;ller</author><title>Unverified Agent Routing.</title>
  <year>2025</year><booktitle>ICLR</booktitle><ee>https://openreview.net/forum?id=abc</ee></inproceedings>
<inproceedings key="conf/acl/FindX25"><title>Graph Explanations at Scale: A Study.</title><year>2025</year>
  <booktitle>ACL (Findings)</booktitle><ee>https://doi.org/10.18653/v1/2025.findings-acl.9</ee></inproceedings>
<article key="journals/corr/abs-2605-00001"><title>Unverified Agent Routing.</title><year>2026</year><journal>CoRR</journal></article>
<inproceedings key="conf/icml/Old21"><title>Something Old.</title><year>2021</year><booktitle>ICML</booktitle></inproceedings>
<inproceedings key="conf/foo/Other25"><title>Not Our Venue.</title><year>2025</year><booktitle>FOO</booktitle></inproceedings>
</dblp>
"""


def make_index(tmp_path):
    (tmp_path / "dblp.dtd").write_text(DTD)
    with gzip.open(tmp_path / "dblp.xml.gz", "wt", encoding="iso-8859-1") as f:
        f.write(XML)
    out = tmp_path / "index.sqlite3"
    kept = dblp.build_index(tmp_path / "dblp.xml.gz", out, log=lambda m: None)
    return out, kept


def unverified_paper(store, pid, title, aliases):
    store.upsert_paper({"id": pid, "title": title, "titleZh": "中文", "authors": ["A"], "firstAuthor": "A", "year": 2026,
                        "venue": "NeurIPS 2026", "publicationStatus": "待核实", "metadataStatus": "待核实",
                        "versions": [{"id": aliases[0], "kind": "arxiv", "label": "arXiv", "url": "https://arxiv.org/abs/2605.00001", "date": None}],
                        "urls": {"source": "https://www.semanticscholar.org/paper/x", "pdf": None, "code": None}, "pdfSha256": None,
                        "citations": {"count": 1, "updatedAt": None}, "metadataStatus": "待核实", "summary": "s", "matchedKeywords": [],
                        "topics": ["其他相关方向"], "libraryRelevance": "related",
                        "libraryReason": "（会议信息未核实：只找到 arXiv 版本，NeurIPS 2026 是 Semantic Scholar 推断的）理由"}, aliases)


def test_build_index_keeps_target_venues_recent_years(tmp_path):
    out, kept = make_index(tmp_path)
    assert kept == 2, "只保留目标会议、2022 年以后、非 arXiv 的记录"
    import sqlite3
    rows = sqlite3.connect(out).execute("SELECT key, venue, main, year FROM pubs ORDER BY key").fetchall()
    assert rows == [("conf/acl/FindX25", "ACL", 0, 2025), ("conf/iclr/RouteM25", "ICLR", 1, 2025)]


def test_verify_library(client, store, tmp_path):
    out, _ = make_index(tmp_path)
    unverified_paper(store, "dblp-journals_corr_abs-2605-00001", "Unverified Agent Routing", ["arxiv-2605.00001"])
    unverified_paper(store, "dblp-journals_corr_abs-2605-00002", "Graph Explanations at Scale: A Study of Things", ["arxiv-2605.00002"])
    unverified_paper(store, "dblp-journals_corr_abs-2605-00003", "Nobody Published This", ["arxiv-2605.00003"])
    store.patch_user("dblp-journals_corr_abs-2605-00001", {"watched": True})
    stats = dblp.verify_library(store, out, log=lambda m: None, min_index=0)
    assert stats == {"verified": 1, "elsewhere": 1, "notFound": 1}

    ok = assert_contract(client.get(API + "/papers/arxiv-2605.00001").json(), "paper")
    assert ok["id"] == "dblp-conf_iclr_RouteM25" and "dblp-journals_corr_abs-2605-00001" in ok["aliases"]
    assert (ok["venue"], ok["year"], ok["publicationStatus"]) == ("ICLR 2025", 2025, "已发表")
    assert ok["user"]["watched"] and ok["libraryReason"] == "理由" and ok["versions"][0]["url"] == "https://openreview.net/forum?id=abc"

    findings = assert_contract(client.get(API + "/papers/arxiv-2605.00002").json(), "paper")
    assert findings["publicationStatus"] == "已发表" and "不属于检索范围" in findings["libraryReason"] and "Findings" in findings["venue"]

    missing = assert_contract(client.get(API + "/papers/dblp-journals_corr_abs-2605-00003").json(), "paper")
    assert missing["id"] == "arxiv-2605.00003", "没找到的主 ID 换成 arxiv-…"
    assert missing["publicationStatus"] == "待核实" and "DBLP" in missing["libraryReason"]

    again = dblp.verify_library(store, out, log=lambda m: None, min_index=0)
    assert again == {"verified": 0, "elsewhere": 0, "notFound": 1}, "已核实的不再处理"


def test_survey_ids_skip_corr_as_primary():
    primary, aliases = survey._ids({"paperId": "p", "externalIds": {"DBLP": "journals/corr/abs-1", "ArXiv": "2605.1"}})
    assert primary == "arxiv-2605.1" and aliases == ["dblp-journals_corr_abs-1"]


def test_verify_skips_status_you_set(client, store, tmp_path):
    out, _ = make_index(tmp_path)
    unverified_paper(store, "dblp-journals_corr_abs-2605-00001", "Unverified Agent Routing", ["arxiv-2605.00001"])
    client.patch(API + "/papers/dblp-journals_corr_abs-2605-00001", json={"publicationStatus": "待核实", "venue": "我自己写的"})
    assert dblp.verify_library(store, out, log=lambda m: None, min_index=0) == {"verified": 0, "elsewhere": 0, "notFound": 0}
    assert client.get(API + "/papers/arxiv-2605.00001").json()["venue"] == "我自己写的"
