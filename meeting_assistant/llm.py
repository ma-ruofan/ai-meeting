import json
from dataclasses import dataclass, field

import httpx

from .config import Settings
from .models import AppError


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Reply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict = field(default_factory=dict)


class ChatClient:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport

    def chat(self, messages, schema=None, tools=None):
        cfg = self.settings
        if error := cfg.llm_error():
            raise AppError(error)
        messages = [dict(m) for m in messages]
        if schema:
            messages = [
                {
                    "role": "system",
                    "content": "输出 JSON，遵循 Schema：" + json.dumps(schema, ensure_ascii=False),
                },
                *messages,
            ]
        payload = {
            "model": cfg.model,
            "messages": messages,
            "stream": False,
            "temperature": 0.1,
            "max_tokens": cfg.max_output_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            # Only advertised for native mode. Unsupported provider errors are explicit.
        elif schema and cfg.json_mode == "schema":
            payload["response_format"] = (
                {"type": "json_schema", "schema": schema}
                if cfg.provider == "llamacpp"
                else {
                    "type": "json_schema",
                    "json_schema": {"name": "meeting_response", "schema": schema},
                }
            )
        elif schema and cfg.json_mode == "json":
            payload["response_format"] = {"type": "json_object"}
        # UTF-8 byte upper estimate + framing reserve, intentionally conservative.
        # This is not a model tokenizer and can reject inputs which would fit.
        input_estimate = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 512
        if input_estimate + cfg.max_output_tokens > cfg.context_tokens:
            raise AppError(
                "输入超过保守上下文预算，请缩短会议/问题或按目标模型能力调整预算。未截断输入。"
            )
        headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        try:
            with httpx.Client(
                timeout=cfg.timeout, transport=self.transport, follow_redirects=False
            ) as client:
                response = client.post(
                    cfg.base_url + "/chat/completions", json=payload, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise AppError("模型请求超时，已保存的会议不受影响，可以重试。") from exc
        except httpx.HTTPError as exc:
            raise AppError("无法连接模型服务，请检查地址、网络和服务状态。") from exc
        if response.status_code >= 300:
            reason = {
                401: "认证失败，请检查本机密钥",
                403: "没有模型访问权限",
                404: "接口或模型不存在",
                429: "请求限流或额度不足",
                400: "请求参数不兼容，请检查 JSON 模式、工具协议和模型配置",
            }.get(response.status_code, "模型服务返回错误")
            # Deliberately omit response body/URL: providers may echo secrets or content.
            raise AppError(f"{reason}（HTTP {response.status_code}）。")
        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            finish = choice.get("finish_reason", "stop")
            if finish in {"length", "content_filter"}:
                raise AppError("模型输出被截断或过滤，未作为完整结果保存。请调整预算或输入。")
            calls = [
                ToolCall(
                    id=x["id"], name=x["function"]["name"], arguments=x["function"]["arguments"]
                )
                for x in message.get("tool_calls", [])
            ]
            content = message.get("content") or ""
            if not isinstance(content, str) or any(not isinstance(c.arguments, str) for c in calls):
                raise TypeError("Invalid content")
            if len(content) > 100_000 or len(calls) > 16:
                raise AppError("模型返回内容过大，已停止处理。")
            usage = {
                k: v
                for k, v in (body.get("usage") or {}).items()
                if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and isinstance(v, int)
            }
            return Reply(content, calls, finish, usage)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AppError("模型响应不符合聊天接口格式，请检查服务适配配置。") from exc


def parse_json(text):
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    # Never extract an arbitrary inner object from a truncated/ambiguous response.
    return json.loads(text)
