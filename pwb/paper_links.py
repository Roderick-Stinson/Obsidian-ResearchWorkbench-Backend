"""Resolve paper links from bibliographic evidence."""
import re
from urllib.parse import parse_qs, urlsplit


def host(url):
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def rank(url):
    domain = host(url)
    if not domain or not url.startswith(("https://", "http://")) or any(
        domain == site or domain.endswith("." + site) for site in ("semanticscholar.org", "dblp.org", "openalex.org")
    ):
        return 9
    if domain == "openreview.net":
        return 2
    if domain in ("arxiv.org", "export.arxiv.org") or "/10.48550/arxiv." in url.lower():
        return 3
    return 1 if domain == "doi.org" else 0


def repository(abstract):
    links = {url.rstrip(".").removesuffix(".git") for url in
             re.findall(r"https://(?:github\.com|gitlab\.com)/[\w.-]+/[\w.-]+", abstract or "")}
    return next(iter(links)) if len(links) == 1 else None


def links_for(paper, metadata=None, extra_ids=(), official=()):
    metadata = metadata or {}
    external = metadata.get("externalIds") or {}
    urls = paper.get("urls") or {}
    versions = paper.get("versions") or []
    ids = [paper["id"], *paper.get("aliases", []), *extra_ids, *(v["id"] for v in versions)]
    sources = [*official, urls.get("source"), *(v.get("url") for v in versions)]
    acl = external.get("ACL") or next((pid[4:] for pid in ids if pid.startswith("acl-")), None)
    arxiv = external.get("ArXiv") or next((pid[6:] for pid in ids if pid.startswith("arxiv-")), None)
    doi = external.get("DOI")
    if doi and doi.lower().startswith("10.48550/arxiv."):
        arxiv = arxiv or doi[len("10.48550/arxiv."):]
    elif doi:
        sources.append("https://doi.org/" + doi)
    if acl:
        sources.append("https://aclanthology.org/" + acl + "/")
    if arxiv:
        sources.append("https://arxiv.org/abs/" + arxiv)
    sources.extend("https://openreview.net/forum?id=" + pid[11:] for pid in ids if pid.startswith("openreview-"))
    source = min((url for url in sources if rank(url) < 9), key=rank, default=None)
    formal_pdf = None
    if host(source) == "openreview.net" and (review := parse_qs(urlsplit(source).query).get("id")):
        source = "https://openreview.net/forum?id=" + review[0]
        formal_pdf = "https://openreview.net/pdf?id=" + review[0]
    elif host(source) == "proceedings.iclr.cc" and "/hash/" in source and "-Abstract-" in source:
        formal_pdf = source.replace("/hash/", "/file/").replace("-Abstract-", "-Paper-").removesuffix(".html") + ".pdf"
    elif acl:
        formal_pdf = "https://aclanthology.org/" + acl + ".pdf"
    pdf = (metadata.get("openAccessPdf") or {}).get("url")
    return {**urls, "source": source, "pdf": formal_pdf or urls.get("pdf") or pdf or
            ("https://arxiv.org/pdf/" + arxiv if arxiv else None),
            "code": urls.get("code") or repository(metadata.get("abstract") or paper.get("abstract"))}
