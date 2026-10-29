"""让后端开机自启、崩溃后自动重启（每日推荐的定时在后端里，后端得一直开着）。

- macOS：写 ~/Library/LaunchAgents/<LABEL>.plist，用 launchctl 加载（登录后启动，退出了自动拉起）；
- Linux：写 ~/.config/systemd/user/pwb.service，用 systemctl --user 启用。
后端的日志写到数据目录的 serve.log。环境变量（PWB_*、S2_API_KEY、DEEPSEEK_*）在安装时照抄进去，改了要重新安装。
"""
import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import ROOT

LABEL = "com.paperworkbench.backend"
ENV_KEYS = ("PWB_DATA_DIR", "PWB_HOST", "PWB_PORT", "PWB_TOKEN", "PWB_TZ", "PWB_CLAUDE_BIN", "PWB_CODEX_BIN",
            "S2_API_KEY", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL")


def _env(settings):
    home = Path.home()
    # 后台服务没有你终端里的 PATH；claude / codex 命令常在这些地方。
    path = os.pathsep.join(dict.fromkeys([str(home / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
                                          *os.environ.get("PATH", "").split(os.pathsep)]))
    env = {"PATH": path, "PWB_DATA_DIR": str(settings.data_dir), "LANG": os.environ.get("LANG", "en_US.UTF-8")}
    env.update({k: os.environ[k] for k in ENV_KEYS if k in os.environ and k != "PWB_DATA_DIR"})
    return env


def _mac_plist():
    return Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


def _linux_unit():
    return Path.home() / ".config/systemd/user/pwb.service"


def install(settings):
    log = str(settings.data_dir / "serve.log")
    cmd = [sys.executable, "-m", "pwb", "serve"]
    if platform.system() == "Darwin":
        plist = _mac_plist()
        plist.parent.mkdir(parents=True, exist_ok=True)
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], check=False)
        plist.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": cmd, "WorkingDirectory": str(ROOT),
                                          "EnvironmentVariables": _env(settings), "RunAtLoad": True, "KeepAlive": True,
                                          "StandardOutPath": log, "StandardErrorPath": log}))
        subprocess.run(["launchctl", "load", str(plist)], check=True)
        return "已安装并启动：{}\n日志：{}\n卸载：python -m pwb service uninstall".format(plist, log)
    if platform.system() == "Linux":
        unit = _linux_unit()
        unit.parent.mkdir(parents=True, exist_ok=True)
        env = "\n".join('Environment="{}={}"'.format(k, v.replace('"', '\\"')) for k, v in _env(settings).items())
        unit.write_text("[Unit]\nDescription=Paper workbench backend\nAfter=network-online.target\n\n[Service]\n"
                        "WorkingDirectory={}\nExecStart={}\n{}\nRestart=always\nStandardOutput=append:{}\nStandardError=append:{}\n\n"
                        "[Install]\nWantedBy=default.target\n".format(ROOT, " ".join(cmd), env, log, log), encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "pwb.service"], check=True)
        return ("已安装并启动：{}\n日志：{}\n没登录时也要运行的话：loginctl enable-linger $USER\n"
                "卸载：python -m pwb service uninstall".format(unit, log))
    raise SystemExit("暂时只支持 macOS（launchd）和 Linux（systemd）；其他系统请用自己的方式让 python -m pwb serve 常驻")


def uninstall():
    if platform.system() == "Darwin":
        plist = _mac_plist()
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], check=False)
            plist.unlink()
            return "已停止并删除 " + str(plist)
    elif platform.system() == "Linux":
        unit = _linux_unit()
        if unit.exists():
            subprocess.run(["systemctl", "--user", "disable", "--now", "pwb.service"], check=False)
            unit.unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            return "已停止并删除 " + str(unit)
    return "没有安装过"


def status():
    if platform.system() == "Darwin":
        res = subprocess.run(["launchctl", "list", LABEL], capture_output=True, text=True)
        return res.stdout.strip() if res.returncode == 0 else "没有在运行（{} 不存在或没加载）".format(_mac_plist())
    if platform.system() == "Linux":
        return subprocess.run(["systemctl", "--user", "status", "pwb.service", "--no-pager"], capture_output=True, text=True).stdout
    return "不支持的系统"
