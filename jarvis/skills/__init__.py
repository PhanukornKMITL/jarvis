"""Everything JARVIS can do. To add a skill, write a module exposing SKILLS and list it here."""

from __future__ import annotations

from . import chat, clock, garden, light, status, weather
from .base import Context, Skill

MODULES = (light, status, weather, garden, clock, chat)
SKILLS: tuple[Skill, ...] = tuple(skill for module in MODULES for skill in module.SKILLS)
BY_INTENT = {skill.intent: skill for skill in SKILLS}

__all__ = ["BY_INTENT", "SKILLS", "Context", "Skill"]
