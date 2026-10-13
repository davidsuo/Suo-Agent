# common/model_router.py
"""
多模型智能路由 + 跨模型失败回退（US-15）

职责：
1. 按 task_type / complexity 选择模型调用链
2. 主模型失败时自动跨模型回退
3. 记录每次调用的模型、耗时、成功/失败
4. 支持通过 config/models.json 调整模型和路由链
"""

import os
import json
import time
from typing import Optional

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from openai import OpenAI

UPLOAD_DIR = os.getenv(
    "UPLOAD_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "models.json"
)

_DEFAULT_CONFIG = {
    "models": {
        "flash": {"model": "deepseek-flash", "timeout": 30, "max_retries": 0},
        "chat": {"model": "deepseek-chat", "timeout": 60, "max_retries": 0},
        "reasoner": {"model": "deepseek-reasoner", "timeout": 120, "max_retries": 0},
        "local": {
            "model": "qwen2.5:7b",
            "base_url": "http://127.0.0.1:11434/v1",
            "timeout": 120,
            "max_retries": 0
        }
    },
    "routes": {
        "simple_chat": ["flash", "chat"],
        "analysis": ["chat", "flash"],
        "reasoning": ["reasoner", "chat"],
        "tool_use": ["flash", "chat", "reasoner"],
        "default": ["flash", "chat", "reasoner"]
    }
}


def _load_config() -> dict:
    """读取 config/models.json，失败时返回默认配置。"""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"###model_router### 读取配置失败，使用默认配置: {e}")
        return _DEFAULT_CONFIG


def _get_route_chain(task_type: str = "default", complexity: str = "normal") -> list:
    """返回模型回退链，例如 ['flash', 'chat', 'reasoner']。"""
    cfg = _load_config()
    routes = cfg.get("routes", {})
    if complexity == "high":
        return routes.get("reasoning", routes.get("default", ["flash", "chat", "reasoner"]))
    return routes.get(task_type, routes.get("default", ["flash", "chat", "reasoner"]))


def route(task_type: str = "default", complexity: str = "normal") -> str:
    """AC-2：返回首选模型别名。"""
    chain = _get_route_chain(task_type, complexity)
    return chain[0] if chain else "flash"


def _build_client(model_cfg: dict) -> OpenAI:
    """按模型配置创建客户端；支持本地 OpenAI 兼容服务。"""
    base_url = model_cfg.get("base_url") or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    api_key = (
        model_cfg.get("api_key")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
    )
    return OpenAI(api_key=api_key, base_url=base_url)


def _log_call(alias: str, model_name: str, latency: float, success: bool,
              error: str = "", task_type: str = "", complexity: str = ""):
    """记录模型调用日志到 uploads/model_calls.jsonl。"""
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "alias": alias,
        "model": model_name,
        "latency": latency,
        "success": success,
        "error": str(error)[:300],
        "task_type": task_type,
        "complexity": complexity,
    }
    try:
        with open(os.path.join(UPLOAD_DIR, "model_calls.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"###model_router### 日志写入失败: {e}")


def call_with_fallback(
    messages: list,
    task_type: str = "default",
    complexity: str = "normal",
    tools: Optional[list] = None,
    tool_choice: Optional[str] = None,
    **kwargs
):
    """
    按路由链依次调用模型。
    成功即返回；失败则切下一个模型。
    """
    cfg = _load_config()
    models_cfg = cfg.get("models", {})
    chain = _get_route_chain(task_type, complexity)
    last_error = None

    for idx, alias in enumerate(chain):
        mcfg = models_cfg.get(alias, {})
        model_name = mcfg.get("model", alias)
        max_retries = int(mcfg.get("max_retries", 0))
        client = _build_client(mcfg)

        for attempt in range(max_retries + 1):
            t0 = time.time()
            try:
                params = {
                    "model": model_name,
                    "messages": messages,
                    **kwargs
                }
                if tools:
                    params["tools"] = tools
                if tool_choice:
                    params["tool_choice"] = tool_choice
                if mcfg.get("timeout"):
                    params["timeout"] = mcfg["timeout"]

                resp = client.chat.completions.create(**params)
                latency = round(time.time() - t0, 3)
                _log_call(alias, model_name, latency, True, "", task_type, complexity)
                print(
                    f"###model_router### model={alias}({model_name}) "
                    f"latency={latency}s fallback_index={idx} success"
                )
                return resp

            except Exception as e:
                latency = round(time.time() - t0, 3)
                last_error = e
                _log_call(
                    alias, model_name, latency, False,
                    f"{type(e).__name__}: {e}", task_type, complexity
                )
                print(
                    f"###model_router### model={alias}({model_name}) "
                    f"latency={latency}s fallback_index={idx} "
                    f"attempt={attempt + 1} failed: {type(e).__name__}: {e}"
                )
                if attempt < max_retries:
                    time.sleep(1)
                    continue
                break

    raise RuntimeError(f"所有模型调用失败: {last_error}")