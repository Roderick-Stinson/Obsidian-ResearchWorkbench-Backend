"""命令行入口：python -m pwb <命令>。"""
import argparse
import logging
import shutil
import sys
from pathlib import Path

from .config import CONTRACT_DIR, Settings


def cmd_serve(args, settings):
    import uvicorn
    from .api import create_app
    if settings.host not in ("127.0.0.1", "localhost", "::1") and not settings.token:
        sys.exit("监听非本机地址时必须设置 PWB_TOKEN（约定 2.1）")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


def cmd_seed_examples(args, settings):
    from .seed import seed_examples
    from .store import Store
    seed_examples(Store(settings), args.examples or CONTRACT_DIR / "examples")


def cmd_import_vault(args, settings):
    from .importer import import_vault
    from .store import Store
    count, warnings = import_vault(Store(settings), args.vault)
    print("共导入 {} 篇".format(count))
    for w in warnings:
        print("注意：", w)


def cmd_apply_interests(args, settings):
    """把一次关键词整理结果（JSON，schema interests-extraction）核对后并入关键词树。"""
    import json
    from .extraction import apply_extraction
    from .interests import walk
    from .store import Store
    try:
        tree = apply_extraction(Store(settings), json.loads(Path(args.file).read_text(encoding="utf-8")))
    except ValueError as e:
        sys.exit(str(e))
    count = sum(1 for _ in walk(tree["domains"]))
    print("已并入关键词树，共 {} 个关键词".format(count))


def cmd_survey(args, settings):
    """领域检索入库：在 CCF-A 会议 / 期刊等范围里找相关论文，写进论文库（见 pwb/survey.py）。"""
    from . import llm, survey
    from .store import Store
    store = Store(settings)
    stages = args.stages.split(",")
    result = survey.run(store, llm.get_config(store), years=args.years, stages=stages, workers=args.workers,
                        limit=args.limit, log=lambda m: print(m, flush=True))
    print("各阶段篇数：", result)


def cmd_dblp_index(args, settings):
    """从 DBLP 整库建核实用的小索引（data/dblp/index.sqlite3）；整库和 dblp.dtd 要放在同一目录。"""
    from . import dblp
    src = Path(args.xml or settings.data_dir / "dblp" / "dblp.xml.gz")
    dblp.build_index(src, settings.data_dir / "dblp" / "index.sqlite3", log=lambda m: print(m, flush=True))


def cmd_dblp_verify(args, settings):
    """用 DBLP 索引核实论文库里“待核实”的论文。"""
    from . import dblp
    from .store import Store
    index = settings.data_dir / "dblp" / "index.sqlite3"
    if not index.exists():
        sys.exit("还没有 DBLP 索引，先运行 dblp-index")
    print(dblp.verify_library(Store(settings), index, log=lambda m: print(m, flush=True)))


def cmd_fetch_pdfs(args, settings):
    """批量下载论文库里还没有 PDF 的论文（只用公开来源）。"""
    from . import pdfs
    from .store import Store
    store = Store(settings)
    if args.missing:
        for r in pdfs.missing_report(store):
            print("{status}\t{venue}\t{id}\t{title}\t{tried}".format(**{**r, "tried": "；".join(r["tried"])}))
        return
    print(pdfs.fetch_all(store, log=lambda m: print(m, flush=True), limit=args.limit, retry=args.retry))


def cmd_landscape(args, settings):
    """经典与必读、领域发展史：refs 取参考文献，classics 整理经典与必读，map 整理领域发展史。"""
    from . import landscape, llm
    from .store import Store
    store = Store(settings)
    log = lambda m: print(m, flush=True)
    if "refs" in args.steps:
        landscape.fetch_references(store, log, refresh=args.refresh)
    if "classics" in args.steps:
        landscape.build_classics(store, llm.get_config(store), log)
    if "map" in args.steps:
        landscape.build_map(store, llm.get_config(store), log)


def cmd_feed(args, settings):
    """取 arXiv 上整个智能体方向的论文（按月），给热点演变用。"""
    from . import feed
    from .store import Store
    feed.fetch(Store(settings), since=args.since, log=lambda m: print(m, flush=True), refresh=args.refresh)


def cmd_sync_contract(args, settings):
    """从 vault 的 后端约定/ 复制 schemas、examples、validate.py 到 contract/。"""
    src = Path(args.source)
    if not (src / "schemas").is_dir():
        sys.exit("找不到 {}/schemas".format(src))
    for name in ("schemas", "examples"):
        shutil.rmtree(CONTRACT_DIR / name, ignore_errors=True)
        shutil.copytree(src / name, CONTRACT_DIR / name)
    for name in ("validate.py", "数据格式约定.md"):
        shutil.copy2(src / name, CONTRACT_DIR / name)
    print("已同步约定：", src, "→", CONTRACT_DIR)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m pwb", description="论文工作台后端")
    parser.add_argument("--data-dir", help="数据目录，默认 ./data 或环境变量 PWB_DATA_DIR")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="启动 HTTP 服务")
    p = sub.add_parser("seed-examples", help="写入约定里的示例数据（不是真实结果）")
    p.add_argument("--examples", help="示例目录，默认 contract/examples")
    p = sub.add_parser("import-vault", help="从 vault 导入现有论文条目和 PDF（只读 vault）")
    p.add_argument("vault", help="vault 根目录")
    p = sub.add_parser("apply-interests", help="核对并并入一次关键词整理结果")
    p.add_argument("file", help="interests-extraction 格式的 JSON 文件")
    p = sub.add_parser("survey", help="领域检索：找相关的已发表论文并入库")
    p.add_argument("--years", default="2023-2026")
    p.add_argument("--stages", default="fetch,titles,abstracts,ingest", help="逗号分隔：fetch,titles,abstracts,ingest")
    p.add_argument("--workers", type=int, default=3, help="同时调用大模型的数量")
    p.add_argument("--limit", type=int, help="每个大模型阶段最多处理多少篇（试跑用）")
    p = sub.add_parser("dblp-index", help="从 DBLP 整库建核实用的索引")
    p.add_argument("--xml", help="dblp.xml.gz 的路径，默认 data/dblp/dblp.xml.gz")
    sub.add_parser("dblp-verify", help="用 DBLP 索引核实“待核实”的论文")
    p = sub.add_parser("fetch-pdfs", help="批量下载论文库里还没有 PDF 的论文（只用公开来源）")
    p.add_argument("--limit", type=int, help="最多下载多少篇（试跑用）")
    p.add_argument("--retry", action="store_true", help="之前没找到来源 / 被挡住的也重试")
    p.add_argument("--missing", action="store_true", help="列出没下到 PDF 的论文")
    p = sub.add_parser("landscape", help="整理经典与必读、领域发展史")
    p.add_argument("steps", nargs="*", default=["refs", "classics", "map"], help="refs / classics / map，默认全部")
    p.add_argument("--refresh", action="store_true", help="参考文献全部重新取")
    p = sub.add_parser("feed", help="取 arXiv 上整个智能体方向的论文（按月），给热点演变用")
    p.add_argument("--since", default="2023-07", help="从哪个月开始，默认 2023-07")
    p.add_argument("--refresh", action="store_true", help="取过的月份也重新取")
    p = sub.add_parser("sync-contract", help="从 vault 同步约定到 contract/")
    p.add_argument("source", help="vault 里的 论文工作台/后端约定 目录")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings(data_dir=args.data_dir)
    {"serve": cmd_serve, "seed-examples": cmd_seed_examples, "import-vault": cmd_import_vault, "apply-interests": cmd_apply_interests, "survey": cmd_survey, "dblp-index": cmd_dblp_index, "dblp-verify": cmd_dblp_verify, "fetch-pdfs": cmd_fetch_pdfs, "landscape": cmd_landscape,
     "feed": cmd_feed, "sync-contract": cmd_sync_contract}[args.command](args, settings)


if __name__ == "__main__":
    main()
