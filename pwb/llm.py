"""可切换的大模型引擎。后端需要大模型的地方（目前是关键词整理）都通过 complete_json 调用。

- claude-code：本机 Claude Code 命令行（claude -p），用你已登录的账号。
- codex：本机 Codex 命令行（codex exec）。
- deepseek：DeepSeek API（兼容 OpenAI 接口），API key 从环境变量 DEEPSEEK_API_KEY 或 data/secrets.json 读取。

用哪个引擎、用什么模型、走哪个接口地址，存在后端设置里（GET/PUT /settings/llm），面板上都能改。
API key 在面板上只能写、不能读：保存到后端 data/secrets.json，接口只告诉面板“有没有配”。

每个引擎都可以单独配接口地址和 key，和你日常用的 Claude Code / Codex 配置分开：
- claude-code：配了 key 时，用 ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY 调用，并用后端自己的 CLAUDE_CONFIG_DIR（data/claude-home），不读你日常的登录和设置；
- codex：配了接口地址时，后端在 data/codex-home 里自己生成一份 config.toml（走 /v1/responses），用 CODEX_HOME 指过去，不读你日常的 ~/.codex；
- 都没配就用本机已登录的命令行，和以前一样。
"""
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from jsonschema import Draft202012Validator

ENGINES = {
    "claude-code": {"name": "Claude Code（本机命令行）", "defaultModel": None, "keyName": "claudeCodeApiKey"},
    "codex": {"name": "Codex CLI（本机命令行）", "defaultModel": None, "keyName": "codexApiKey"},
    "deepseek": {"name": "DeepSeek API", "defaultModel": "deepseek-chat", "keyName": "deepseekApiKey"},
}
DEFAULT_ENGINE = "claude-code"
# 每个引擎能选的思考强度（空 = 不支持，面板上不显示）。没选就用引擎自己的默认；Codex 没选时后端用 low（只做判断，不需要深想）。
EFFORTS = {"claude-code": ["low", "medium", "high", "xhigh", "max"],
           "codex": ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"], "deepseek": []}
CODEX_DEFAULT_EFFORT = "low"
# 本机命令行拿不到模型列表时（命令失败）才用的兜底：别名由 Claude Code 自己解析成最新版本。
BUILTIN_MODELS = {"claude-code": ["opus", "sonnet", "haiku"], "codex": [], "deepseek": ["deepseek-chat", "deepseek-reasoner"]}
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


def _secrets(settings):
    path = settings.data_dir / "secrets.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def engine_key(settings, engine):
    """引擎的 API key：面板里存的（secrets.json）；DeepSeek 还认环境变量 DEEPSEEK_API_KEY。"""
    key = _secrets(settings).get(ENGINES[engine]["keyName"])
    if engine == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY") or key
    return key or None


def deepseek_key(settings):
    return engine_key(settings, "deepseek")


def set_engine_key(settings, engine, value):
    """面板里写入 / 清除 key（空字符串清除）。只存后端，权限 600，不会出现在任何接口返回里。"""
    data = _secrets(settings)
    name = ENGINES[engine]["keyName"]
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    path = settings.data_dir / "secrets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def status(settings, endpoints=None):
    """每个引擎现在能不能用，给面板展示。endpoints 是面板里配的接口地址 {引擎: 地址}。"""
    endpoints = endpoints or {}
    claude, codex = find_bin("claude", "PWB_CLAUDE_BIN"), find_bin("codex", "PWB_CODEX_BIN")
    own = " ·用面板里配的接口和 key，不读你日常的配置"
    out = {}
    out["claude-code"] = (bool(claude), (claude + (own if engine_key(settings, "claude-code") else "")) if claude
                          else "没找到 claude 命令；安装 Claude Code，或用 PWB_CLAUDE_BIN 指定路径")
    if not codex:
        out["codex"] = (False, "没找到 codex 命令；安装 Codex CLI，或用 PWB_CODEX_BIN 指定路径")
    elif endpoints.get("codex") and not engine_key(settings, "codex"):
        out["codex"] = (False, "配了接口地址，还没填 API key")
    else:
        out["codex"] = (True, codex + (own if endpoints.get("codex") else ""))
    out["deepseek"] = (bool(deepseek_key(settings)),
                       "已配置 API key" if deepseek_key(settings) else "没有 API key；在这里填，或设置环境变量 DEEPSEEK_API_KEY")
    return out


def _run(cmd, stdin, cwd, env=None):
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd=cwd, timeout=TIMEOUT,
                              env={**os.environ, **env} if env else None)
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


def _claude_code(system, prompt, schema, model, settings, endpoint=None, effort=None):
    exe = find_bin("claude", "PWB_CLAUDE_BIN")
    if not exe:
        raise LLMError(status(settings)["claude-code"][1])
    # 在空目录里运行、不给任何工具：只做"读材料、出 JSON"，不会去读写你的文件。
    cmd = [exe, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
           "--system-prompt", system, "--json-schema", json.dumps(schema, ensure_ascii=False)]
    if model:
        cmd += ["--model", model]
    if effort in EFFORTS["claude-code"]:
        cmd += ["--effort", effort]
    env, key = {}, engine_key(settings, "claude-code")
    if endpoint:
        env["ANTHROPIC_BASE_URL"] = endpoint
    if key:
        # 配了 key 就和你日常的登录、设置、钩子分开：用后端自己的配置目录，key 同时按 API key 和 Bearer token 给（网关各有各的要求）。
        home = settings.data_dir / "claude-home"
        home.mkdir(parents=True, exist_ok=True)
        env.update({"ANTHROPIC_API_KEY": key, "ANTHROPIC_AUTH_TOKEN": key, "CLAUDE_CONFIG_DIR": str(home)})
    with tempfile.TemporaryDirectory(prefix="pwb-llm-") as cwd:
        out = _parse_json(_run(cmd, prompt, cwd, env), "Claude Code")
    if out.get("is_error") or out.get("subtype") != "success":
        raise LLMError("Claude Code 调用失败：{}".format(str(out.get("result") or out.get("subtype"))[:300]))
    if isinstance(out.get("structured_output"), dict):
        return out["structured_output"]
    return _parse_json(out.get("result") or "", "Claude Code")


def _codex_overrides(effort=None):
    """后端只要“读材料、出 JSON”，不需要你桌面版 Codex 配置里的东西，它们还会拖慢甚至卡住调用：
    插件和 MCP 服务启动时要联网（直连 chatgpt.com 不通的网络下一直重连）、提醒钩子、high 推理强度。
    MCP 服务名从 ~/.codex/config.toml 里读。"""
    cfg = Path.home() / ".codex" / "config.toml"
    text = cfg.read_text(encoding="utf-8", errors="replace") if cfg.exists() else ""
    out = ["--disable", "plugins", "--disable", "apps", "-c", "notify=[]", "-c", "model_reasoning_effort=" + json.dumps(effort or CODEX_DEFAULT_EFFORT)]
    for name in dict.fromkeys(re.findall(r"^\[mcp_servers\.([A-Za-z0-9_-]+)\]", text, re.M)):
        out += ["-c", "mcp_servers.{}.enabled=false".format(name)]
    return out


def _codex_home(settings, endpoint, model, effort=None):
    """配了接口地址：在 data/codex-home 里生成后端自己的 config.toml（走 /v1/responses，key 从环境变量读），
    用 CODEX_HOME 指过去，和你日常的 ~/.codex 完全分开。新目录里没有插件、MCP、提醒钩子。"""
    home = settings.data_dir / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    lines = ['model_provider = "pwb"', "model_reasoning_effort = " + json.dumps(effort or CODEX_DEFAULT_EFFORT)]
    if model:
        lines.append("model = " + json.dumps(model))
    lines += ["", "[model_providers.pwb]", 'name = "pwb"', "base_url = " + json.dumps(endpoint), 'wire_api = "responses"',
              'env_key = "PWB_CODEX_API_KEY"']
    (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return home


def _codex(system, prompt, schema, model, settings, endpoint=None, effort=None):
    exe = find_bin("codex", "PWB_CODEX_BIN")
    if not exe:
        raise LLMError(status(settings)["codex"][1])
    env, extra = {}, _codex_overrides(effort if effort in EFFORTS["codex"] else None)
    if endpoint:
        key = engine_key(settings, "codex")
        if not key:
            raise LLMError(status(settings, {"codex": endpoint})["codex"][1])
        env = {"CODEX_HOME": str(_codex_home(settings, endpoint, model, effort if effort in EFFORTS["codex"] else None)), "PWB_CODEX_API_KEY": key}
        extra = []
    with tempfile.TemporaryDirectory(prefix="pwb-llm-") as cwd:
        schema_file, answer_file = Path(cwd) / "schema.json", Path(cwd) / "answer.json"
        schema_file.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", *extra,
               "--output-schema", str(schema_file), "--output-last-message", str(answer_file)]
        if model:
            cmd += ["--model", model]
        _run(cmd + ["-"], system + "\n\n" + prompt, cwd, env)
        if not answer_file.exists():
            raise LLMError("Codex 没有写出结果")
        return _parse_json(answer_file.read_text(encoding="utf-8"), "Codex")


def _deepseek(system, prompt, schema, model, settings, endpoint=None, effort=None):
    key = deepseek_key(settings)
    if not key:
        raise LLMError(status(settings)["deepseek"][1])
    base = (endpoint or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
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
    """当前引擎设置：{engine, models: {引擎: 模型或 None}, endpoints: {引擎: 接口地址}, updatedAt}。"""
    saved = store.get_kv("llm") or {}
    models = {k: v["defaultModel"] for k, v in ENGINES.items()}
    models.update(saved.get("models") or {})
    engine = saved.get("engine") if saved.get("engine") in ENGINES else DEFAULT_ENGINE
    endpoints = {k: v for k, v in (saved.get("endpoints") or {}).items() if k in ENGINES and v}
    efforts = {k: v for k, v in (saved.get("efforts") or {}).items() if k in EFFORTS and v in EFFORTS[k]}
    return {"engine": engine, "models": models, "endpoints": endpoints, "efforts": efforts, "updatedAt": saved.get("updatedAt")}


SCHEMA_TRIES = 3


def _schema_problems(data, schema):
    """输出不符合 schema 的地方（最多三条，写成能直接发给模型的话）；符合返回空列表。"""
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(map(str, e.path)))
    return ["{}：{}".format("/".join(map(str, e.path)) or "最外层", e.message[:160]) for e in errors[:3]]


def complete_json(settings, config, system, prompt, schema):
    """按当前设置调用引擎，返回解析好的 JSON 对象，并按 schema 校验。
    引擎不一定遵守传进去的 schema（自己配的接口常常忽略，会返回字段名、类型、层级不对的 JSON），
    不符合就把问题告诉模型、让它重写，最多试 SCHEMA_TRIES 次，仍不符合才报错。"""
    engine = config["engine"]
    ask = prompt
    for attempt in range(SCHEMA_TRIES):
        data = CALLERS[engine](system, ask, schema, config["models"].get(engine), settings,
                               (config.get("endpoints") or {}).get(engine), (config.get("efforts") or {}).get(engine))
        problems = _schema_problems(data, schema)
        if not problems:
            return data
        ask = prompt + "\n\n你上一次的输出不符合要求的 JSON Schema：{}。请重新输出，字段名、类型、层级都严格按 schema，只输出 JSON。".format("；".join(problems))
    raise LLMError("{} 连续 {} 次输出都不符合格式：{}".format(ENGINES[engine]["name"], SCHEMA_TRIES, "；".join(problems)))


def _get_json(url, headers):
    req = urllib.request.Request(url, headers={"User-Agent": "paper-workbench/0.1", **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError("接口返回 {}：{}".format(e.code, e.read().decode("utf-8", "replace")[:200]))
    except (urllib.error.URLError, ValueError, OSError) as e:
        raise LLMError("连不上 {}：{}".format(url, getattr(e, "reason", e)))


def _item(model_id, label="", efforts=None):
    return {"id": model_id, "label": label or "", "efforts": list(efforts or [])}


def _claude_cli_models(exe, timeout=40):
    """和 Claude Code 里 /model 选单是同一份数据：给 claude 发一个 initialize 控制请求，它的回复里带可选模型和每个模型支持的思考强度。
    不调用任何模型，不花钱。回复里的账号等信息一概不取。"""
    cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--tools", "",
           "--no-session-persistence"]
    with tempfile.TemporaryDirectory(prefix="pwb-llm-") as cwd:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=cwd)
        try:
            proc.stdin.write(json.dumps({"type": "control_request", "request_id": "pwb-models", "request": {"subtype": "initialize"}}) + "\n")
            proc.stdin.flush()
            # select 看不到 Python 已经读进缓冲区的行，所以用线程逐行读、队列里取（claude 可能先输出别的消息）。
            lines = queue.Queue()
            threading.Thread(target=lambda: [lines.put(l) for l in proc.stdout] + [lines.put(None)], daemon=True).start()
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    line = lines.get(timeout=max(0.1, deadline - time.time()))
                except queue.Empty:
                    continue
                if line is None:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("type") == "control_response":
                    models = ((msg.get("response") or {}).get("response") or {}).get("models") or []
                    return [_item(m["value"], m.get("displayName"), m.get("supportedEffortLevels")) for m in models
                            if m.get("value") and m["value"] != "default"]
        finally:
            proc.kill()
            proc.wait()
    raise LLMError("Claude Code 没有返回模型列表")


def _codex_cli_models(exe):
    """和 Codex 里 /model 选单是同一份数据：codex debug models 打印的模型目录（只取选单里可见的）。"""
    try:
        proc = subprocess.run([exe, "debug", "models"], capture_output=True, text=True, timeout=60)
        catalog = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise LLMError("codex debug models 没有返回目录")
    models = catalog.get("models") if isinstance(catalog, dict) else catalog
    return [_item(m["slug"], m.get("display_name"), [l.get("effort") for l in m.get("supported_reasoning_levels") or [] if isinstance(l, dict)])
            for m in models or [] if m.get("slug") and m.get("visibility", "list") == "list"]


def list_models(settings, engine, endpoint=None):
    """面板里选模型用：返回 ([{id, label, efforts}], 来源, 说明)。
    来源 cli = 本机命令行自己的模型选单（没配接口地址 / key，用本机登录时，和在 Claude Code / Codex 里敲 /model 看到的一样）；
    api = 从接口拉的；builtin = 兜底的常用名。拉不到不报错，说明里写原因，面板仍可手动填。"""
    key = engine_key(settings, engine)
    fallback = [_item(m) for m in BUILTIN_MODELS[engine]]
    try:
        if engine == "deepseek":
            if not key:
                return fallback, "builtin", "没有 API key，列的是常用模型"
            base = (endpoint or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
            data = _get_json(base + "/models", {"Authorization": "Bearer " + key})
        elif engine == "codex":
            if not endpoint:
                exe = find_bin("codex", "PWB_CODEX_BIN")
                if not exe:
                    return [], "cli", status(settings)["codex"][1]
                return _codex_cli_models(exe), "cli", "来自本机 Codex 的模型选单（等同于在 Codex 里敲 /model）"
            if not key:
                return [], "api", "还没填 API key"
            data = _get_json(endpoint.rstrip("/") + "/models", {"Authorization": "Bearer " + key})
        else:
            if not key:
                exe = find_bin("claude", "PWB_CLAUDE_BIN")
                if not exe:
                    return fallback, "builtin", status(settings)["claude-code"][1]
                return _claude_cli_models(exe), "cli", "来自本机 Claude Code 的模型选单（等同于在 Claude Code 里敲 /model）"
            base = (endpoint or "https://api.anthropic.com").rstrip("/")
            url = base + ("/models" if base.endswith("/v1") else "/v1/models")
            data = _get_json(url, {"x-api-key": key, "Authorization": "Bearer " + key, "anthropic-version": "2023-06-01"})
    except LLMError as e:
        return fallback, "builtin" if fallback else "api", "拉取失败（{}）；可以手动填模型名".format(e)
    items = data.get("data") if isinstance(data, dict) else data
    ids = sorted({m["id"] if isinstance(m, dict) else str(m) for m in items or [] if (m.get("id") if isinstance(m, dict) else m)})
    return [_item(i) for i in ids], "api", "" if ids else "接口没有返回模型；可以手动填模型名"
