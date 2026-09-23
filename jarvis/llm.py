"""Client for an OpenAI-compatible chat endpoint (llama-server running Qwen)."""

from __future__ import annotations

import json
import urllib.request


def complete(messages: list[dict], endpoint: str, temperature: float, max_tokens: int, timeout: int) -> str:
    body = json.dumps({"messages": messages, "temperature": temperature, "max_tokens": max_tokens}).encode()
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read())
    return result["choices"][0]["message"]["content"]
