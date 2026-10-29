"""批量下载论文库里的 PDF。只用公开、合法的来源，不碰付费墙。

来源顺序（先正式版本，后预印本）：ACL Anthology → 出版方的开放获取链接 → arXiv。
都没有时，按标题到 arXiv 搜一次同名论文。每个网站单独限速（arXiv 要求程序访问至少间隔 3 秒）。
每篇的结果记在 user_kv 的 pdf-fetch 里：下过的跳过；找不到来源的默认不再重试（--retry 可以重试）。
"""
import hashlib
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .survey import _norm_title
from .util import now

UA = "paper-workbench/0.1 (personal research library; polite bulk PDF download)"
MIN_INTERVAL = {"export.arxiv.org": 3.2, "arxiv.org": 3.2}
DEFAULT_INTERVAL = 1.2
MAX_BYTES = 100 * 2**20
_last = {}


def _throttle(url):
    host = urllib.parse.urlparse(url).netloc
    wait = MIN_INTERVAL.get(host, DEFAULT_INTERVAL) - (time.time() - _last.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last[host] = time.time()


def _get(url, tries=4):
    """返回 (内容, 最终地址)；失败抛异常。限流或服务器暂时出错时等一会儿再试。"""
    for attempt in range(tries):
        _throttle(url)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as res:
                body = res.read(MAX_BYTES + 1)
                return body, res.geturl()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
        time.sleep(10 * (attempt + 1))


def _arxiv_pdf(arxiv_id):
    return "https://export.arxiv.org/pdf/" + arxiv_id


def candidates(paper):
    """这篇论文可以尝试的 PDF 地址，按优先级排好：[(来源说明, 地址)]。"""
    ids = [paper["id"], *paper.get("aliases", [])]
    out = []
    acl = next((i for i in ids if i.startswith("acl-")), None)
    if acl:
        out.append(("ACL Anthology", "https://aclanthology.org/{}.pdf".format(acl[4:])))
    oa = (paper.get("urls") or {}).get("pdf")
    arxiv_in_oa = re.search(r"arxiv\.org/(?:pdf|abs)/([0-9]{4}\.[0-9]{4,5})", oa or "")
    if oa and not arxiv_in_oa:
        out.append(("开放获取", oa))
    arxiv = next((i[6:] for i in ids if i.startswith("arxiv-")), None) or (arxiv_in_oa.group(1) if arxiv_in_oa else None)
    if arxiv:
        out.append(("arXiv", _arxiv_pdf(arxiv)))
    return out


def arxiv_by_title(title):
    """按标题在 arXiv 搜，标题完全一致（忽略大小写和标点）才算找到。返回 arXiv 编号或 None。"""
    words = re.sub(r"[^\w\s]", " ", title).split()
    if len(words) < 3:
        return None
    query = "ti:" + " AND ti:".join(words[:12])
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"search_query": query, "max_results": 5})
    body, _ = _get(url)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in ET.fromstring(body).findall("a:entry", ns):
        if _norm_title(entry.findtext("a:title", "", ns)) == _norm_title(title):
            m = re.search(r"abs/([0-9]{4}\.[0-9]{4,5})", entry.findtext("a:id", "", ns))
            if m:
                return m.group(1)
    return None


def fetch_one(store, pid):
    """下载一篇论文的 PDF，返回结果记录（status: ok / none / blocked / error）。"""
    paper = store.get_paper(pid)
    tried = []

    def attempt(sources):
        for label, url in sources:
            try:
                body, final = _get(url)
            except Exception as e:
                tried.append("{}：{}".format(label, getattr(e, "code", None) or type(e).__name__))
                continue
            if not body.startswith(b"%PDF"):
                tried.append("{}：不是 PDF（多半是登录页或付费墙）".format(label))
                continue
            if len(body) > MAX_BYTES:
                tried.append("{}：超过 100 MB".format(label))
                continue
            target = store.settings.pdf_dir / (re.sub(r"[^A-Za-z0-9._-]", "_", pid) + ".pdf")
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(body)
            os.replace(tmp, target)
            store.set_pdf(pid, target.name, hashlib.sha256(body).hexdigest())
            return {"status": "ok", "source": label, "url": final, "bytes": len(body)}
        return None

    sources = candidates(paper)
    result = attempt(sources)
    # 登记的来源都不行、其中又没有 arXiv 时，按标题到 arXiv 找同名论文（会议论文多半也挂在 arXiv 上）。
    if result is None and not any(label.startswith("arXiv") for label, _ in sources) and paper["title"] != pid:
        try:
            found = arxiv_by_title(paper["title"])
        except Exception as e:
            found = None
            tried.append("arXiv 标题搜索失败：{}".format(type(e).__name__))
        if found:
            if not store.resolve("arxiv-" + found):
                store.upsert_paper(paper, paper["aliases"] + ["arxiv-" + found])
            sources = sources + [("arXiv（按标题找到）", _arxiv_pdf(found))]
            result = attempt(sources[-1:])
    if result is None:
        status = "none" if not sources else "blocked" if all("不是 PDF" in t or "403" in t or "401" in t for t in tried) else "error"
        result = {"status": status, "tried": tried or ["没有公开来源"]}
    return result


def fetch_all(store, log=print, limit=None, retry=False):
    state = store.get_kv("pdf-fetch") or {}
    with store.conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM papers WHERE pdf_file IS NULL ORDER BY added_at")]
    todo = [i for i in ids if retry or state.get(i, {}).get("status") not in ("none", "blocked")]
    if limit:
        todo = todo[:limit]
    log("要下载 {} 篇（论文库里共有 {} 篇没有 PDF）".format(len(todo), len(ids)))
    counts = {"ok": 0, "none": 0, "blocked": 0, "error": 0}
    for n, pid in enumerate(todo, 1):
        paper = store.get_paper(pid)
        if not paper or paper["hasPdf"]:
            continue
        result = fetch_one(store, pid)
        counts[result["status"]] += 1
        state[pid] = {**result, "at": now()}
        if n % 10 == 0 or n == len(todo):
            store.set_kv("pdf-fetch", state)
            log("进度 {}/{}：已下载 {ok}，没有公开来源 {none}，被挡住（付费墙等）{blocked}，出错 {error}".format(n, len(todo), **counts))
    store.set_kv("pdf-fetch", state)
    return counts


def arxiv_meta(arxiv_id):
    """arXiv 上这篇论文的标题、作者、年份、摘要、首发日期；找不到返回 None。"""
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": arxiv_id, "max_results": 1})
    body, _ = _get(url)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = ET.fromstring(body).find("a:entry", ns)
    title = " ".join((entry.findtext("a:title", "", ns) if entry is not None else "").split())
    if not title or title == "Error":
        return None
    authors = [" ".join(a.findtext("a:name", "", ns).split()) for a in entry.findall("a:author", ns)]
    published = entry.findtext("a:published", "", ns)[:10]
    return {"title": title, "authors": authors, "firstAuthor": authors[0] if authors else "",
            "year": int(published[:4]) if published else None, "published": published or None,
            "abstract": " ".join(entry.findtext("a:summary", "", ns).split())}


def complete_paper(store, pid, log=print):
    """用链接新加的论文：从 arXiv 补上标题、作者、年份、摘要（你填过或改过的不动），再下载 PDF。"""
    paper = store.get_paper(pid)
    arxiv = next((i[6:] for i in [paper["id"], *paper["aliases"]] if i.startswith("arxiv-")), None)
    if arxiv:
        meta = arxiv_meta(arxiv)
        if meta:
            edited = set(paper.get("userEdited") or [])
            fill = {k: meta[k] for k in ("authors", "firstAuthor", "year", "abstract") if k not in edited and not paper.get(k)}
            if paper["title"] == pid:
                fill["title"] = meta["title"]
            versions = [{**v, "date": v.get("date") or meta["published"]} if v["id"] == "arxiv-" + arxiv else v for v in paper["versions"]]
            store.upsert_paper({**paper, **fill, "versions": versions}, paper["aliases"])
            log("{}：已从 arXiv 补上 {}".format(pid, "、".join(fill) or "发布日期"))
    paper = store.get_paper(pid)
    if not paper["hasPdf"]:
        result = fetch_one(store, pid)
        state = store.get_kv("pdf-fetch") or {}
        state[pid] = {**result, "at": now()}
        store.set_kv("pdf-fetch", state)
        log("{}：PDF {}".format(pid, "已下载" if result["status"] == "ok" else "没下到（" + "；".join(result.get("tried", [])) + "）"))
    return store.get_paper(pid)


def missing_report(store):
    """没下到 PDF 的论文清单，给你手动上传用。"""
    state = store.get_kv("pdf-fetch") or {}
    rows = []
    for pid, s in state.items():
        if s.get("status") == "ok":
            continue
        p = store.get_paper(pid)
        if p and not p["hasPdf"]:
            rows.append({"id": p["id"], "title": p["title"], "venue": p.get("venue"), "status": s["status"], "tried": s.get("tried", [])})
    return rows
