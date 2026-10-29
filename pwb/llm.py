"""可切换的大模型引擎。后端需要大模型的地方（目前是关键词整理）都通过 complete_json 调用。

- claude-code：本机 Claude Code 命令行（claude -p），用你已登录的账号。
- codex：本机 Codex 命令行（codex exec）。
- deepseek：DeepSeek API（兼容 OpenAI 接口），API key 从环境变量 DEEPSEEK_API_KEY 或 data/secrets.json 读取。

用哪个引擎、用什么模型，存在后端设置里（GET/PUT /settings/llm），面板上可以切换。API key 不经过接口。
"""
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ENGINES = {
    "claude-code": {"name": "Claude Code（本机命令行）", "defaultModel": None},
    "codex": {"name": "Codex CLI（本机命令行）", "defaultModel": None},
    "deepseek": {"name": "DeepSeek API", "defaultModel": "deepseek-chat"},
}
DEFAULT_ENGINE = "claude-code"
TIMEOUT = 900


class LLMError(Exception):
    """引擎不可用或调用失败；message 直接展示给你看。"""


def find_bin(name, env_var):
    """命令行的位置：环境变量 > PATH > ~/.local/bin（后端由 launchd 等启动时 PATH 可能很短）。"""
    explicit = os.environ.get(env_var)
    if explicit:
        return explicit if Path(explicit).exists() else None
    found = shutil.which(name)
    if found:
        return found
    fallback = Path.home() / ".local/bin" / name
    return str(fallback) if fallback.exists() else None


def deepseek_key(settings):
    key = os.environ.get("DEEPSEEK_API_KEY")
    secrets = settings.data_dir / "secrets.json"
    if not key and secrets.exists():
        try:
            key = json.loads(secrets.read_text(encoding="utf-8")).get("deepseekApiKey")
        except ValueError:
            return None
    return key or None


def status(settings):
    """每个引擎现在能不能用，给面板展示。"""
    claude, codex = find_bin("claude", "PWB_CLAUDE_BIN"), find_bin("codex", "PWB_CODEX_BIN")
    return {
        "claude-code": (bool(claude), claude or "没找到 claude 命令；安装 Claude Code，或用 PWB_CLAUDE_BIN 指定路径"),
        "codex": (bool(codex), codex or "没找到 codex 命令；安装 Codex CLI，或用 PWB_CODEX_BIN 指定路径"),
        "deepseek": (bool(deepseek_key(settings)),
                     "已配置 API key" if deepseek_key(settings) else "没有 API key；设置环境变量 DEEPSEEK_API_KEY，或写入 data/secrets.json 的 deepseekApiKey"),
    }


def _run(cmd, stdin, cwd):
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd=cwd, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise LLMError("{} 超过 {} 秒没有返回".format(Path(cmd[0]).name, TIMEOUT))
    if proc.returncode != 0:
        raise LLMError("{} 退出码 {}：{}".format(Path(cmd[0]).name, proc.returncode, (proc.stderr or proc.stdout).strip()[-500:]))
    return proc.stdout


def _parse_json(text, who):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except ValueError:
        raise LLMError("{} 返回的不是 JSON：{}".format(who, text[:300]))


def _claude_code(system, prompt, schema, model, settings):
    exe = find_bin("claude", "PWB_CLAUDE_BIN")
    if not exe:
        raise LLMError(status(settings)["claude-code"][1])
    # 在空目录里运行、不给任何工具：只做"读材料、出 JSON"，不会去读写你的文件。
    cmd = [exe, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
           "--system-prompt", system, "--json-schema", json.dumps(schema, ensure_ascii=False)]
    if model:
        cmd += ["--model", model]
    with tempfile.TemporaryDirectory(prefix="pwb-llm-") as cwd:
        out = _parse_json(_run(cmd, prompt, cwd), "Claude Code")
    if out.get("is_error") or out.get("subtype") != "success":
        raise LLMError("Claude Code 调用失败：{}".format(str(out.get("result") or out.get("subtype"))[:300]))
    if isinstance(out.get("structured_output"), dict):
        return out["structured_output"]
    return _parse_json(out.get("result") or "", "Claude Code")


def _codex(system, prompt, schema, model, settings):
    exe = find_bin("codex", "PWB_CODEX_BIN")
    if not exe:
        raise LLMError(status(settings)["codex"][1])
    with tempfile.TemporaryDirectory(prefix="pwb-llm-") as cwd:
        schema_file, answer_file = Path(cwd) / "schema.json", Path(cwd) / "answer.json"
        schema_file.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        cmd = [exe, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
               "--output-schema", str(schema_file), "--output-last-message", str(answer_file)]
        if model:
            cmd += ["--model", model]
        _run(cmd + ["-"], system + "\n\n" + prompt, cwd)
        if not answer_file.exists():
            raise LLMError("Codex 没有写出结果")
        return _parse_json(answer_file.read_text(encoding="utf-8"), "Codex")


def _deepseek(system, prompt, schema, model, settings):
    key = deepseek_key(settings)
    if not key:
        raise LLMError(status(settings)["deepseek"][1])
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    # DeepSeek 的 JSON 模式不支持传 schema，只保证输出是 JSON，所以把 schema 写进提示词，结果再由调用方校验。
    body = {"model": model or ENGINES["deepseek"]["defaultModel"], "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system + "\n输出必须是符合下面 JSON Schema 的 JSON：\n" + json.dumps(schema, ensure_ascii=False)},
                         {"role": "user", "content": prompt}]}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            out = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError("DeepSeek API 返回 {}：{}".format(e.code, e.read().decode("utf-8", "replace")[:300]))
    except urllib.error.URLError as e:
        raise LLMError("连不上 DeepSeek API：{}".format(e.reason))
    return _parse_json(out["choices"][0]["message"]["content"], "DeepSeek")


CALLERS = {"claude-code": _claude_code, "codex": _codex, "deepseek": _deepseek}


def get_config(store):
    """当前引擎设置：{engine, models: {引擎: 模型或 None}, updatedAt}。"""
    saved = store.get_kv("llm") or {}
    models = {k: v["defaultModel"] for k, v in ENGINES.items()}
    models.update(saved.get("models") or {})
    engine = saved.get("engine") if saved.get("engine") in ENGINES else DEFAULT_ENGINE
    return {"engine": engine, "models": models, "updatedAt": saved.get("updatedAt")}


def complete_json(settings, config, system, prompt, schema):
    """按当前设置调用引擎，返回解析好的 JSON 对象（是否符合约定由调用方校验）。"""
    engine = config["engine"]
    return CALLERS[engine](system, prompt, schema, config["models"].get(engine), settings)
