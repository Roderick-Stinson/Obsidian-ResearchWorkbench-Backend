"""运行配置。全部可用环境变量覆盖，默认值适合本机运行。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = ROOT / "contract"


class Settings:
    def __init__(self, data_dir=None, token=None, host=None, port=None, start_worker=True):
        # 数据目录：数据库、PDF、本人论文都在这里，迁移服务器时整个拷走。
        self.data_dir = Path(data_dir or os.environ.get("PWB_DATA_DIR") or ROOT / "data").expanduser().resolve()
        # 为空表示不校验 token，只适合监听 127.0.0.1 时使用。
        self.token = token if token is not None else os.environ.get("PWB_TOKEN", "")
        self.host = host or os.environ.get("PWB_HOST", "127.0.0.1")
        self.port = int(port or os.environ.get("PWB_PORT", "8765"))
        self.start_worker = start_worker

    @property
    def db_path(self):
        return self.data_dir / "workbench.sqlite3"

    @property
    def pdf_dir(self):
        return self.data_dir / "pdfs"

    def ensure_dirs(self):
        for d in (self.data_dir, self.pdf_dir):
            d.mkdir(parents=True, exist_ok=True)
