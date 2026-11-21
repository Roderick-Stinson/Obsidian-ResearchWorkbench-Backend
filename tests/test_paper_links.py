from pwb.paper_links import links_for


def test_official_source_first_and_preserves_code():
    paper = {"id": "arxiv-2510.08529", "aliases": ["openreview-review-id"],
             "urls": {"source": "https://www.semanticscholar.org/paper/index", "code": "https://github.com/example/repo"}}
    official = "https://proceedings.iclr.cc/paper_files/paper/2026/hash/example-Abstract-Conference.html"
    urls = links_for(paper, official=[official])
    assert urls["source"] == official
    assert urls["pdf"].endswith("/file/example-Paper-Conference.pdf")
    assert urls["code"] == paper["urls"]["code"]
    assert links_for(paper)["source"] == "https://openreview.net/forum?id=review-id"


def test_index_is_not_an_original_and_arxiv_doi_uses_arxiv():
    paper = {"id": "s2-index", "urls": {"source": "https://www.semanticscholar.org/paper/index"}}
    assert links_for(paper)["source"] is None
    urls = links_for(paper, {"externalIds": {"DOI": "10.48550/arXiv.2510.08529"}})
    assert urls["source"] == "https://arxiv.org/abs/2510.08529"
    assert urls["pdf"] == "https://arxiv.org/pdf/2510.08529"
