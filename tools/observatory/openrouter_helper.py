"""Private Hermes-native credential resolver and bounded OpenRouter SSE client.

Executed in the installed Hermes interpreter. Credentials never cross this
process boundary. Only fixed readiness metadata and model text reach stdout.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

MAX_PROMPT_BYTES = 8192
MAX_OUTPUT_TOKENS = 512
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("redirect refused")


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def resolve(root, selected):
    sys.path.insert(0, root)
    # Vendor diagnostics/config loaders must never enter our output protocol.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import hermes_bootstrap  # native launcher import path/dependency setup
        from hermes_cli.config import load_config
        from hermes_cli.runtime_provider import resolve_runtime_provider
        config = load_config()
        configured = config.get("model", {})
        model = selected or (configured.get("default", "") if isinstance(configured, dict)
                             else configured if isinstance(configured, str) else "")
        if not isinstance(model, str) or not model.startswith("deepseek/") or len(model) > 100:
            raise ValueError("configured-model-unavailable")
        runtime = resolve_runtime_provider(requested="openrouter", target_model=model)
    base = str(runtime.get("base_url") or "").rstrip("/")
    if base != "https://openrouter.ai/api/v1" or not runtime.get("api_key"):
        raise ValueError("native-account-unavailable")
    return model, runtime


def main():
    root, mode, selected = sys.argv[1:4]
    try:
        if mode not in {"probe", "infer"}:
            raise RuntimeError("invalid mode")
        model, runtime = resolve(root, selected)
        if mode == "probe":
            emit({"available": True, "model": model})
            return 0
        prompt = Path(sys.argv[4]).read_text(encoding="utf-8")
        if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise RuntimeError("prompt limit")
        body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "stream": True, "max_tokens": MAX_OUTPUT_TOKENS,
                "reasoning": {"effort": "none"},
                "provider": {"require_parameters": True,
                             "allow_fallbacks": False,
                             "max_price": {"prompt": 1, "completion": 4, "request": 0}},
                "usage": {"include": True}}
        request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + runtime["api_key"],
                     "Content-Type": "application/json", "Accept": "text/event-stream"})
        reply = ""
        usage = None
        received = 0
        opener = urllib.request.build_opener(NoRedirect)
        # No retries/fallback models, tools, auxiliary title or summarizer calls.
        with opener.open(request, timeout=30) as response:
            while True:
                line = response.readline(65537)
                if not line:
                    break
                received += len(line)
                if len(line) > 65536 or received > 1024 * 1024:
                    raise RuntimeError("response limit")
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                event = json.loads(data)
                if "error" in event:
                    raise RuntimeError("provider failure")
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                for choice in event.get("choices", []):
                    delta = choice.get("delta") or {}
                    if delta.get("tool_calls"):
                        raise RuntimeError("unexpected tools")
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        reply += text
                        emit({"type": "text", "text": text})
        if not reply.strip():
            raise RuntimeError("empty answer")
        emit({"type": "result", "exit_code": 0, "text": reply,
              "usage": {key: usage.get(key) for key in
                        ("prompt_tokens", "completion_tokens", "total_tokens", "cost")}
                       if usage else None})
        return 0
    except Exception as exc:
        # HTTP bodies and native AuthErrors can contain secrets. Never echo.
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in {
            "configured-model-unavailable", "native-account-unavailable"} else "native-resolver-unavailable"
        emit({"available": False, "reason": code, "error_type": type(exc).__name__} if mode == "probe" else
             {"type": "result", "exit_code": 1, "text": ""})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
