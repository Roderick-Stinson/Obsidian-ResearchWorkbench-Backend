"""批量下载 PDF：网络请求全部用假的。"""
from pwb import pdfs

from .conftest import API, assert_contract

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2601.00042v2</id><title>Found On  arXiv
 By Title</title></entry></feed>"""


def paper(store, pid, title, aliases=(), pdf=None):
    store.upsert_paper({"id": pid, "title": title, "titleZh": None, "authors": [], "firstAuthor": "", "year": 2025, "venue": "ICML 2025",
                        "publicationStatus": "已发表", "versions": [], "urls": {"source": None, "pdf": pdf, "code": None}, "pdfSha256": None,
                        "citations": {"count": None, "updatedAt": None}, "metadataStatus": "部分核实", "summary": "", "matchedKeywords": []},
                       list(aliases))


def test_candidates_order():
    p = {"id": "acl-2025.acl-long.7", "aliases": ["arxiv-2501.00001"], "urls": {"pdf": "https://ojs.aaai.org/x.pdf"}}
    assert [l for l, _ in pdfs.candidates(p)] == ["ACL Anthology", "开放获取", "arXiv"]
    p = {"id": "dblp-x", "aliases": [], "urls": {"pdf": "https://arxiv.org/pdf/2502.04180"}}
    assert pdfs.candidates(p) == [("arXiv", "https://export.arxiv.org/pdf/2502.04180")], "arXiv 链接统一走 export.arxiv.org"


def test_fetch_all(client, store, monkeypatch):
    paper(store, "acl-2025.acl-long.7", "ACL Paper", ["arxiv-2501.00001"])
    paper(store, "doi-10.1145_1", "Paywalled", pdf="https://dl.acm.org/doi/pdf/10.1145/1")
    paper(store, "doi-10.1109_cvpr", "Found on arXiv by title too", pdf="https://doi.org/10.1109/cvpr")
    paper(store, "dblp-conf_nips_X25", "Found on arXiv by title")
    paper(store, "dblp-conf_nips_Y25", "Nowhere To Be Found")
    served = {"https://aclanthology.org/2025.acl-long.7.pdf": b"%PDF-1.7 acl",
              "https://dl.acm.org/doi/pdf/10.1145/1": b"<html>login</html>",
              "https://doi.org/10.1109/cvpr": b"<html>ieee login</html>",
              "https://export.arxiv.org/pdf/2601.00042": b"%PDF-1.5 arxiv", "https://export.arxiv.org/pdf/2601.00043": b"%PDF-1.5 cvpr"}
    calls = []

    def fake_get(url, tries=4):
        calls.append(url)
        if "api/query" in url:
            if "too" in url:
                return ATOM.replace(b"2601.00042", b"2601.00043").replace(b"By Title", b"By Title Too"), url
            return (ATOM if "Found" in url else b'<feed xmlns="http://www.w3.org/2005/Atom"/>'), url
        if url in served:
            return served[url], url
        raise pdfs.urllib.error.HTTPError(url, 404, "nf", {}, None)
    monkeypatch.setattr(pdfs, "_get", fake_get)
    counts = pdfs.fetch_all(store, log=lambda m: None)
    assert counts == {"ok": 3, "none": 1, "blocked": 1, "error": 0}
    assert client.get(API + "/papers/arxiv-2601.00043").json()["id"] == "doi-10.1109_cvpr", "登记的链接被挡住时，按标题到 arXiv 找"
    acl = assert_contract(client.get(API + "/papers/acl-2025.acl-long.7").json(), "paper")
    assert acl["hasPdf"] and client.get(API + "/papers/acl-2025.acl-long.7/pdf").content == b"%PDF-1.7 acl"
    found = client.get(API + "/papers/arxiv-2601.00042").json()
    assert found["id"] == "dblp-conf_nips_X25" and found["hasPdf"], "按标题找到的 arXiv 编号记成别名"
    report = {r["id"]: r["status"] for r in pdfs.missing_report(store)}
    assert report == {"doi-10.1145_1": "blocked", "dblp-conf_nips_Y25": "none"}
    calls.clear()
    assert pdfs.fetch_all(store, log=lambda m: None) == {"ok": 0, "none": 0, "blocked": 0, "error": 0}, "没来源的、被挡的默认不重试"
    assert calls == []


META = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2005.11401v4</id><published>2020-05-22T17:34:00Z</published>
<title>Retrieval-Augmented Generation for
 Knowledge-Intensive NLP Tasks</title><summary> We explore RAG. </summary>
<author><name>Patrick Lewis</name></author><author><name>Ethan Perez</name></author></entry></feed>"""


def test_add_by_link_completes_paper(client, store, monkeypatch):
    started = []
    monkeypatch.setattr(store.settings, "start_worker", True)
    monkeypatch.setattr(pdfs, "complete_paper", lambda store, pid, log=print: started.append(pid))
    assert client.post(API + "/papers", json={}).status_code == 400, "标题和链接至少填一个"
    res = client.post(API + "/papers", json={"url": "https://arxiv.org/abs/2005.11401", "watched": True})
    body = assert_contract(res.json(), "paper")
    assert res.status_code == 201 and body["title"] == "arxiv-2005.11401" and body["user"]["watched"]
    client.post(API + "/papers", json={"title": "没有链接的稿子", "own": True})
    import time
    for _ in range(50):
        if started:
            break
        time.sleep(0.02)
    assert started == ["arxiv-2005.11401"], "只有给了链接的才去补全"

    served = {"https://export.arxiv.org/pdf/2005.11401": b"%PDF-1.5 rag"}
    monkeypatch.undo()  # 换回真的 complete_paper，网络请求仍是假的
    monkeypatch.setattr(pdfs, "_get", lambda url, tries=4: (META, url) if "api/query" in url else (served[url], url))
    client.patch(API + "/papers/arxiv-2005.11401", json={"year": 2021})
    paper = pdfs.complete_paper(store, "arxiv-2005.11401", log=lambda m: None)
    assert paper["title"] == "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
    assert (paper["firstAuthor"], paper["authors"], paper["abstract"]) == ("Patrick Lewis", ["Patrick Lewis", "Ethan Perez"], "We explore RAG.")
    assert paper["year"] == 2021, "你改过的字段不动"
    assert paper["versions"][0]["date"] == "2020-05-22" and paper["hasPdf"]
    assert store.get_kv("pdf-fetch")["arxiv-2005.11401"]["status"] == "ok"
