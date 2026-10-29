"""用 DBLP 官方整库（https://dblp.org/xml/dblp.xml.gz）核实论文是否中稿。

DBLP 的在线接口前面有人机验证，后端不绕过它；整库下载是 DBLP 给程序批量使用的公开数据。
build_index 把整库里我们关心的会议 / 期刊、近几年的论文抽成一个小索引（data/dblp/index.sqlite3），
之后 verify_library 按标题在索引里找“待核实”论文的正式发表记录。
"""
import gzip
import shutil
import json
import re
import sqlite3

from .survey import VENUES, _norm_title
from .util import now

# DBLP 编号前缀 → 我们的会议 / 期刊简称（与 survey.VENUES 一致）。
KEY_PREFIX = {
    "conf/aaai/": "AAAI", "conf/nips/": "NeurIPS", "conf/acl/": "ACL", "conf/cvpr/": "CVPR", "conf/iccv/": "ICCV",
    "conf/icml/": "ICML", "conf/ijcai/": "IJCAI", "conf/iclr/": "ICLR", "conf/emnlp/": "EMNLP", "conf/naacl/": "NAACL",
    "conf/kdd/": "KDD", "conf/www/": "WWW", "conf/sigir/": "SIGIR", "conf/sigmod/": "SIGMOD", "journals/pacmmod/": "SIGMOD",
    "journals/pvldb/": "VLDB", "conf/icde/": "ICDE", "conf/icse/": "ICSE", "conf/sigsoft/": "FSE", "journals/pacmse/": "FSE",
    "conf/kbse/": "ASE", "conf/issta/": "ISSTA", "conf/chi/": "CHI", "journals/pacmhci/": "CSCW", "conf/uist/": "UIST",
    "conf/ccs/": "CCS", "conf/sp/": "S&P", "conf/uss/": "USENIX Security", "conf/ndss/": "NDSS", "conf/osdi/": "OSDI",
    "conf/sosp/": "SOSP", "conf/nsdi/": "NSDI", "conf/sigcomm/": "SIGCOMM", "conf/mobicom/": "MobiCom",
    "conf/infocom/": "INFOCOM", "conf/asplos/": "ASPLOS", "conf/isca/": "ISCA", "conf/micro/": "MICRO", "conf/hpca/": "HPCA",
    "conf/eurosys/": "EuroSys", "journals/pacmpl/": "PLDI", "conf/mm/": "ACM MM",
    "journals/pami/": "TPAMI", "journals/ijcv/": "IJCV", "journals/jmlr/": "JMLR", "journals/ai/": "AIJ",
    "journals/tkde/": "TKDE", "journals/tois/": "TOIS", "journals/tse/": "TSE", "journals/tosem/": "TOSEM",
    "journals/tifs/": "TIFS", "journals/tpds/": "TPDS", "journals/nature/": "Nature", "journals/natmi/": "Nat. Mach. Intell.",
    "journals/ncomms/": "Nat. Commun.", "journals/natcs/": "Nat. Comput. Sci.",
}
assert set(KEY_PREFIX.values()) <= set(VENUES), set(KEY_PREFIX.values()) - set(VENUES)
MIN_YEAR = 2022
MIN_INDEX = 10000  # 正常的索引有十几万条；少于这个数说明建索引出错了
# 同一个会议编号下不算主会中稿的分卷：Findings、Workshop、学生研讨会等。
NOT_MAIN = re.compile(r"findings|workshop|@|\bsrw\b|student research|tiny ?papers|blogpost", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pubs (
  key TEXT PRIMARY KEY, norm_title TEXT NOT NULL, title TEXT NOT NULL, venue TEXT NOT NULL,
  booktitle TEXT NOT NULL, year INTEGER NOT NULL, main INTEGER NOT NULL, ee TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pubs_title ON pubs(norm_title);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def venue_for_key(key):
    return next((v for prefix, v in KEY_PREFIX.items() if key.startswith(prefix)), None)


def _ensure_dtd(xml_gz):
    """整库开头写的是带日期的 DTD 名（如 dblp-2023-06-28.dtd），不是 dblp.dtd；找不到就用同目录的 dblp.dtd 顶上（字符实体是一样的）。"""
    from pathlib import Path
    with gzip.open(xml_gz, "rb") as f:
        m = re.search(rb'<!DOCTYPE\s+dblp\s+SYSTEM\s+"([^"]+)"', f.read(500))
    if not m:
        return
    folder = Path(xml_gz).parent
    want = folder / m.group(1).decode()
    if not want.exists() and (folder / "dblp.dtd").exists():
        shutil.copy2(folder / "dblp.dtd", want)


def build_index(xml_gz, out_db, log=print):
    """流式解析整库（几 GB 的 XML，不会整个读进内存），只保留目标会议 / 期刊 MIN_YEAR 年以后的论文。"""
    from lxml import etree

    _ensure_dtd(xml_gz)
    out = sqlite3.connect(out_db)
    out.executescript("DROP TABLE IF EXISTS pubs;" + SCHEMA)
    kept = seen = 0
    batch = []
    # 整库用 DTD 定义了大量字符实体（如 &uuml;），dblp.dtd 要和整库放在同一目录；lxml 按文件对象的 name 找它。
    with gzip.open(xml_gz, "rb") as f:
        for _, el in etree.iterparse(f, events=("end",), tag=("inproceedings", "article"), load_dtd=True,
                                     resolve_entities=True, huge_tree=True):
            seen += 1
            key = el.get("key") or ""
            venue = venue_for_key(key)
            year = el.findtext("year")
            if venue and year and year.isdigit() and int(year) >= MIN_YEAR:
                title = "".join(el.find("title").itertext()).strip() if el.find("title") is not None else ""
                book = el.findtext("booktitle") or el.findtext("journal") or ""
                ee = [e.text for e in el.findall("ee") if e.text]
                if title:
                    batch.append((key, _norm_title(title), title.rstrip("."), venue, book, int(year), 0 if NOT_MAIN.search(book) else 1, json.dumps(ee)))
                    kept += 1
            el.clear()
            while el.getprevious() is not None:
                del el.getparent()[0]
            if len(batch) >= 5000:
                out.executemany("INSERT OR REPLACE INTO pubs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", batch)
                batch = []
                log("已扫描 {} 条记录，保留 {} 条".format(seen, kept))
    out.executemany("INSERT OR REPLACE INTO pubs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", batch)
    out.execute("INSERT OR REPLACE INTO meta VALUES ('builtAt', ?)", (now(),))
    out.commit()
    out.close()
    log("索引建好：扫描 {} 条，保留 {} 条".format(seen, kept))
    return kept


# 之前核实时写在 libraryReason 开头的说明；重新核实时先去掉，不会越叠越长。
STALE_NOTE = re.compile(r"^(?:（(?:会议信息未核实|DBLP [^）]*没找到|实际发表在 )[^）]*）)+")


def _formal_id(key):
    return "dblp-" + re.sub(r"[^A-Za-z0-9._-]", "_", key)


def find(index, title):
    """按标题在索引里找正式记录；先精确匹配，再试冒号前的短标题（camera-ready 常改副标题）。返回行列表。"""
    norm = _norm_title(title)
    rows = index.execute("SELECT * FROM pubs WHERE norm_title = ?", (norm,)).fetchall()
    if not rows and ":" in title:
        head = _norm_title(title.split(":", 1)[0])
        if len(head) >= 12:
            rows = [r for r in index.execute("SELECT * FROM pubs WHERE norm_title LIKE ?", (head + "%",)).fetchall()
                    if r["norm_title"].startswith(head)]
            rows = rows if len(rows) == 1 else []
    return rows


def verify_library(store, index_db, log=print, min_index=MIN_INDEX):
    """核实论文库里所有“待核实”的检索入库论文。返回统计。

    - 在目标会议 / 期刊的主会里找到：改为“已发表”，会议和年份以 DBLP 为准，主 ID 换成 DBLP 正式编号（旧 ID 留作别名）；
    - 只在 Findings / Workshop 等分卷里找到：改为“已发表”并写明实际发表在哪，标注“不属于检索范围”；
    - 没找到：保持“待核实”，写明在哪个版本的 DBLP 里没找到；主 ID 从 DBLP 的 arXiv 编号换成 arxiv-…。
    """
    index = sqlite3.connect(index_db)
    index.row_factory = sqlite3.Row
    size = index.execute("SELECT COUNT(*) FROM pubs").fetchone()[0]
    if size < min_index:
        raise ValueError("DBLP 索引只有 {} 条记录，多半是建索引时出错了；不核实，以免把论文都误标成“没找到”".format(size))
    built = (index.execute("SELECT v FROM meta WHERE k = 'builtAt'").fetchone() or ["?"])[0][:10]
    with store.conn() as c:
        rows = [(r["id"], json.loads(r["data"])) for r in c.execute("SELECT id, data FROM papers")]
    # 发表状态是你手动设的，就不再自动核实。
    todo = [(pid, d) for pid, d in rows if d.get("libraryRelevance") and d.get("publicationStatus") == "待核实"
            and "publicationStatus" not in (d.get("userEdited") or [])]
    stats = {"verified": 0, "elsewhere": 0, "notFound": 0}
    for pid, d in todo:
        hits = find(index, d["title"])
        main = [h for h in hits if h["main"]]
        hit = sorted(main or hits, key=lambda h: -h["year"])[0] if hits else None
        base_reason = STALE_NOTE.sub("", d.get("libraryReason") or "")
        paper = store.get_paper(pid)
        if hit:
            venue = "{} {}".format(hit["venue"] if hit["main"] else hit["booktitle"], hit["year"])
            ee = json.loads(hit["ee"])
            fixes = {"venue": venue, "year": hit["year"], "publicationStatus": "已发表", "metadataStatus": "部分核实",
                     "libraryReason": base_reason if hit["main"] else "（实际发表在 {}，不是主会，不属于检索范围）{}".format(venue, base_reason)}
            new_id = _formal_id(hit["key"])
            version = {"id": new_id, "kind": "journal" if hit["key"].startswith("journals/") else "proceedings",
                       "label": venue, "url": next((u for u in ee if u.startswith("http")), None), "date": None}
            fixes["versions"] = [version] + [v for v in paper["versions"] if v["id"] != new_id and v["kind"] == "arxiv"]
            if not paper["urls"].get("source") or "semanticscholar" in (paper["urls"].get("source") or ""):
                fixes["urls"] = {**paper["urls"], "source": version["url"] or paper["urls"].get("source")}
            stats["verified" if hit["main"] else "elsewhere"] += 1
        else:
            fixes = {"libraryReason": "（DBLP {} 版里没找到正式发表记录，会议信息仍是 Semantic Scholar 推断的）{}".format(built, base_reason)}
            new_id = next((a for a in paper["aliases"] if a.startswith("arxiv-")), None) if pid.startswith("dblp-journals_corr") else None
            stats["notFound"] += 1
        fixes = {k: v for k, v in fixes.items() if k not in (paper.get("userEdited") or [])}
        store.upsert_paper({**paper, **fixes}, paper["aliases"])
        if new_id and new_id != pid:
            owner = store.resolve(new_id)
            if owner in (None, pid):
                store.rename_paper(pid, new_id)
            else:
                log("跳过换 ID：{} 已被另一篇论文 {} 占用".format(new_id, owner))
    log("核实完成：主会中稿 {verified} 篇，只在 Findings / Workshop 等找到 {elsewhere} 篇，DBLP 里没找到 {notFound} 篇".format(**stats))
    return stats
