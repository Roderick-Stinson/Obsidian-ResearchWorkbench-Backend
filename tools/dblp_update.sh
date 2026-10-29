#!/bin/bash
# 下载 DBLP 月度整库 → 建核实索引 → 核实论文库里“待核实”的论文 → 删掉整库原文件。
# 下载服务器会对长时间下载限速，这里不绕过，只是断点续传、慢慢下完。
# 用法：nohup tools/dblp_update.sh [整库地址] > data/dblp/update.log 2>&1 &
set -u -o pipefail
cd "$(dirname "$0")/.."
URL="${1:-https://drops.dagstuhl.de/storage/artifacts/dblp/xml/2026/dblp-2026-10-03.xml.gz}"
DIR=data/dblp
mkdir -p "$DIR"
log() { echo "$(date '+%F %T') $*"; }

[ -f "$DIR/dblp.dtd" ] || curl -sSL -A "paper-workbench/0.1 (research library)" -o "$DIR/dblp.dtd" https://dblp.org/xml/dblp.dtd

log "开始下载 $URL"
until gzip -t "$DIR/dblp.xml.gz" 2>/dev/null; do
  curl -sS -L -C - -A "paper-workbench/0.1 (research library)" -o "$DIR/dblp.xml.gz" "$URL"
  log "已下载 $(( $(stat -f %z "$DIR/dblp.xml.gz" 2>/dev/null || echo 0) / 1000000 )) MB"
  gzip -t "$DIR/dblp.xml.gz" 2>/dev/null || sleep 60
done
log "下载完成，文件完整"

# 建索引和核实的报错要完整留在日志里；任何一步失败都停下，不删整库（下次可以直接重建索引，不用重新下载）。
.venv/bin/python -m pwb dblp-index 2>&1 | grep -v " INFO " | awk 'NR % 50 == 0 || !/^已扫描/' || { log "建索引失败，整库保留在 $DIR"; exit 1; }
.venv/bin/python -m pwb dblp-verify 2>&1 | grep -v " INFO " || { log "核实失败，整库保留在 $DIR"; exit 1; }
rm -f "$DIR/dblp.xml.gz"
log "已删除整库原文件，只保留索引 $DIR/index.sqlite3"
