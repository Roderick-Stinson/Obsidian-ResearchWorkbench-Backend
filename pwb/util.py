import json
import os
import re
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo


def _local_zone():
    """时区：环境变量 PWB_TZ（如 Asia/Shanghai、America/New_York），没设就用系统时区。返回 (tzinfo, IANA 名字或 None)。"""
    name = os.environ.get("PWB_TZ")
    if name:
        return ZoneInfo(name), name
    m = re.search(r"zoneinfo/(.+)$", os.path.realpath("/etc/localtime"))
    if m:
        try:
            return ZoneInfo(m.group(1)), m.group(1)
        except Exception:  # noqa: BLE001 — 系统时区文件不规范时退回固定偏移
            pass
    return datetime.now().astimezone().tzinfo, None


TZ, TZ_NAME = _local_zone()
PAPER_ID_RE = re.compile(r"^[a-z][a-z0-9]*-[A-Za-z0-9._-]+$")
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})")


def now():
    """带时区偏移的当前时间（约定 2.4，时区见 PWB_TZ）。"""
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
