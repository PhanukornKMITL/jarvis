"""Client for an OpenAI-compatible chat endpoint (llama-server running Qwen)."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterator


def _request(messages: list[dict], endpoint: str, temperature: float, max_tokens: int, stream: bool) -> urllib.request.Request:
    body = json.dumps({"messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": stream}).encode()
    return urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )


def complete(messages: list[dict], endpoint: str, temperature: float, max_tokens: int, timeout: int) -> str:
    with urllib.request.urlopen(_request(messages, endpoint, temperature, max_tokens, False), timeout=timeout) as response:
        result = json.loads(response.read())
    return result["choices"][0]["message"]["content"]


def stream(messages: list[dict], endpoint: str, temperature: float, max_tokens: int, timeout: int) -> Iterator[str]:
    """Yields text pieces as the model writes them (server-sent events)."""
    with urllib.request.urlopen(_request(messages, endpoint, temperature, max_tokens, True), timeout=timeout) as response:
        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                return
            try:
                piece = json.loads(data)["choices"][0]["delta"].get("content")
            except (ValueError, KeyError, IndexError):
                continue
            if piece:
                yield piece
