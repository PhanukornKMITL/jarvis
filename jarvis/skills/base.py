from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Callable

from ..cli import request
from ..config import Config


@dataclass(frozen=True)
class Context:
    host: str
    port: int
    config: Config
    history: list[tuple[str, str]] = field(default_factory=list)
    """Recent (user said, JARVIS replied) turns of the current conversation, oldest first."""
    facts: dict[str, tuple[float, str]] = field(default_factory=dict)
    """Latest (time.time(), result line) of each tool, kept in memory only, so "ทำไม" after
    a forecast, or "จะไปกินข้าวข้างนอก" later, can still use it."""

    def devices(self) -> list[dict]:
        return asyncio.run(request(self.host, self.port, {"type": "status"})).get("devices", [])

    def action(self, target: str, action: str) -> dict:
        return asyncio.run(request(self.host, self.port, {"type": "action", "target": target, "action": action}))


@dataclass(frozen=True)
class Skill:
    intent: str
    handle: Callable[[Context, str], str]
    """Gets the command text, returns the spoken reply."""
    rule: Callable[[str], bool] | None = None
    """Deterministic match; the only way an action runs, never an LLM guess. Two skills'
    rules firing means unclear."""
    mentions: Callable[[str], bool] | None = None
    """Text that sounds like this topic but matched no rule is asked again instead of chatted about."""
    slow: bool = False
    """Takes seconds (network, LLM): JARVIS says a short filler first so the wait isn't silent."""
    stream: Callable[[Context, str], Iterator[str]] | None = None
    """Yields the reply sentence by sentence so speech can start before it is complete."""
