import json
import re
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
PAPER_ID_RE = re.compile(r"^[a-z][a-z0-9]*-[A-Za-z0-9._-]+$")
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})")


def now():
    """带 +08:00 时区的当前时间（约定 2.4）。"""
    return datetime.now(TZ).isoformat(timespec="seconds")


def today():
    return datetime.now(TZ).date().isoformat()


def new_id(prefix):
    """形如 task-20261027T153000-a1b2c3 的 ID。"""
    return "{}-{}-{}".format(prefix, datetime.now(TZ).strftime("%Y%m%dT%H%M%S"), secrets.token_hex(3))


def arxiv_id_from_url(url):
    m = ARXIV_RE.search(url or "")
    return "arxiv-" + m.group(1) if m else None


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
