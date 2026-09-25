"""Decide what to do with a transcript: action rules first, then the LLM picks read-only tools."""

from __future__ import annotations

from dataclasses import dataclass

from .skills import SKILLS
from .tools import Call, pick

UNCLEAR = "unclear"
FALLBACK = "chat"
INFO = "info"
"""Answer from tool results (weather, garden, devices, date and time)."""


@dataclass(frozen=True)
class Route:
    intent: str
    """A skill intent, INFO or UNCLEAR."""
    calls: tuple[Call, ...] = ()


def route(transcript: str, endpoint: str, alternatives: tuple[str, ...] = ()) -> Route:
    """`alternatives` are other transcripts of the same audio; their rules must agree."""
    texts = (transcript, *alternatives)
    fired = {skill.intent for skill in SKILLS if skill.rule and any(skill.rule(text) for text in texts)}
    if len(fired) > 1:
        return Route(UNCLEAR)
    if fired:
        return Route(fired.pop())
    calls = pick(transcript, endpoint)
    if calls:
        return Route(INFO, tuple(calls))
    if any(skill.mentions and skill.mentions(transcript) for skill in SKILLS):
        # A garbled command ("บริฟัยให้น้อย") would otherwise get a rambling chat reply,
        # and actions are never guessed: ask again.
        return Route(UNCLEAR)
    return Route(FALLBACK)
