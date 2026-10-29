"""一次性导入：把 vault 里 论文库/条目/*.md 和对应 PDF 搬进后端（约定第 7 节）。只读 vault，不改它。"""
import hashlib
import re
import shutil
from pathlib import Path

import yaml

from . import contract
from .util import PAPER_ID_RE, arxiv_id_from_url, now

PUBLICATION = {"已发表", "已录用", "投稿中", "预印本", "待核实"}
METADATA = {"已核实", "部分核实", "待核实"}
VERSION_KIND = {"arxiv": "arxiv", "openreview": "openreview", "doi": "journal"}


def read_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (yaml.safe_load(m.group(1)) or {}) if m else {}


def wikilink_target(value):
    m = re.match(r"^\[\[([^\]|]+)", str(value or "").strip())
    return m.group(1) if m else (str(value).strip() if value else "")


def to_paper(fm):
    """把 paper-record 的 frontmatter 转成 (paper 返回体字段, 别名, 用户标记)。"""
    pid = fm["paper_id"]
    first_author = (fm.get("first_author") or "").strip()
    authors = [a for a in (fm.get("authors") or ([first_author] if first_author else [])) if a]
    source_url, pdf_url = fm.get("source_url") or None, fm.get("pdf_source_url") or None
    arxiv_alias = arxiv_id_from_url(pdf_url) or arxiv_id_from_url(source_url)
    aliases = [arxiv_alias] if arxiv_alias and arxiv_alias != pid else []
    prefix = pid.split("-", 1)[0]
    version_label = str(fm.get("paper_version") or "")
    versions = [{"id": pid, "kind": VERSION_KIND.get(prefix, "proceedings"),
                 "label": fm.get("venue") or version_label or pid, "url": source_url, "date": None}]
    for alias in aliases:
        label = re.match(r"^(arXiv:\S+?)(；|$)", version_label)
        versions.append({"id": alias, "kind": "arxiv", "label": label.group(1) if label else alias,
                         "url": "https://arxiv.org/abs/" + alias[6:], "date": None})
    title = str(fm.get("title") or pid)
    paper = {
        "id": pid, "title": title, "titleZh": None, "authors": authors, "firstAuthor": first_author,
        "year": fm.get("year") if isinstance(fm.get("year"), int) else None,
        "venue": str(fm.get("venue") or ""),
        "publicationStatus": fm.get("publication_status") if fm.get("publication_status") in PUBLICATION else "待核实",
        "versions": versions,
        "urls": {"source": source_url, "pdf": pdf_url, "code": fm.get("code_url") or None},
        "hasPdf": False, "pdfSha256": None,
        "citations": {"count": None, "updatedAt": None},
        "metadataStatus": fm.get("metadata_status") if fm.get("metadata_status") in METADATA else "待核实",
        "summary": str(fm.get("summary") or ""), "matchedKeywords": [], "hasDeepRead": False,
        "addedAt": str(fm.get("updated_at") or now()), "updatedAt": now(),
    }
    roles = fm.get("paper_roles") or []
    user = {"watched": "重点关注" in roles, "own": "本人投稿" in roles,
            "discussionStatus": fm.get("discussion_status") or None}
    return paper, aliases, user


def import_vault(store, vault, log=print):
    """返回 (导入篇数, 警告列表)。可重复执行：元数据覆盖更新，已有的你的标记不覆盖。"""
    vault = Path(vault)
    records = sorted((vault / "论文库" / "条目").glob("*.md"))
    count, warnings = 0, []
    for path in records:
        fm = read_frontmatter(path)
        if fm.get("type") != "paper-record":
            continue
        pid = str(fm.get("paper_id") or "")
        if not PAPER_ID_RE.match(pid):
            warnings.append("{}：paper_id 不合法（{}），跳过".format(path.name, pid))
            continue
        paper, aliases, user = to_paper(fm)
        pdf_file = None
        pdf_rel = wikilink_target(fm.get("paper_pdf"))
        if pdf_rel:
            src = vault / pdf_rel
            if src.exists():
                digest = hashlib.sha256(src.read_bytes()).hexdigest()
                if fm.get("pdf_sha256") and fm["pdf_sha256"] != digest:
                    warnings.append("{}：PDF 的 SHA-256 与条目记录不一致，按实际文件导入".format(path.name))
                pdf_file = pid + ".pdf"
                shutil.copy2(src, store.settings.pdf_dir / pdf_file)
                paper["hasPdf"], paper["pdfSha256"] = True, digest
            else:
                warnings.append("{}：找不到 PDF {}".format(path.name, pdf_rel))
        if paper["title"] == pid:
            warnings.append("{}：没有标题，暂用 ID 代替".format(path.name))
        contract.check("paper", {"schemaVersion": 1, "kind": "paper", "aliases": aliases,
                                 "user": store.get_user(pid), **paper})
        store.upsert_paper(paper, aliases, pdf_file)
        if store.get_user(pid)["updatedAt"] is None:
            store.patch_user(pid, {k: v for k, v in user.items() if v not in (None, False)} or {"watched": False})
        count += 1
        log("导入 {}{}".format(pid, "（含 PDF）" if pdf_file else ""))
    return count, warnings
