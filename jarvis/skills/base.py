from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from ..cli import request
from ..config import Config


@dataclass(frozen=True)
class Context:
    host: str
    port: int
    config: Config

    def devices(self) -> list[dict]:
        return asyncio.run(request(self.host, self.port, {"type": "status"})).get("devices", [])

    def action(self, target: str, action: str) -> dict:
        return asyncio.run(request(self.host, self.port, {"type": "action", "target": target, "action": action}))


@dataclass(frozen=True)
class Skill:
    intent: str
    description: str
    """Thai description shown to the LLM classifier."""
    handle: Callable[[Context, str], str]
    """Gets the command text, returns the spoken reply."""
    rule: Callable[[str], bool] | None = None
    """Deterministic match; checked before the LLM. Two skills' rules firing means unclear."""
    rule_only: bool = False
    """The LLM may not pick this skill by itself: a wrong guess acts on something (e.g. switches a light)."""
    mentions: Callable[[str], bool] | None = None
    """For rule_only skills: text that sounds like this topic becomes unclear instead of chat."""
