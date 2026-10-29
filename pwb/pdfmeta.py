"""从 PDF 第一页猜论文标题。只用于收录时没拿到标题的论文，猜错了可以在面板里改。"""
import re

from pypdf import PdfReader

# 标题前面常见的页眉，跳过。
HEADER = re.compile(r"^(arXiv:|Under review|Published as|Proceedings of|Preprint|Accepted|Workshop|\d+$)", re.I)
# 遇到这些就说明标题结束了：作者、匿名、摘要、带上标编号或通讯标记的作者行、邮箱。
END = re.compile(r"^(Anonymous|Abstract|ABSTRACT|Authors?\b)|@|[*∗†‡]|[A-Za-z]\s?\d(,|\s|$)")


def guess_title_from_text(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    while lines and HEADER.match(lines[0]):
        lines.pop(0)
    title = []
    for line in lines[:4]:
        if END.search(line):
            break
        title.append(line)
    guess = re.sub(r"\s+", " ", " ".join(title)).strip()
    return guess if 10 <= len(guess) <= 250 else None


def guess_title(path):
    reader = PdfReader(str(path))
    meta = (reader.metadata or {}).get("/Title")
    if meta and len(str(meta).strip()) >= 10 and not re.search(r"\.(tex|dvi|pdf)$|^untitled|^microsoft word", str(meta).strip(), re.I):
        return str(meta).strip()
    return guess_title_from_text(reader.pages[0].extract_text() or "") if reader.pages else None
