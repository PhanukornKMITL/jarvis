"""Actions and conversation. To add one, write a module exposing SKILLS and list it here.
Read-only information (weather, garden, devices, time) lives in jarvis/tools.py instead."""

from __future__ import annotations

from . import chat, light
from .base import Context, Skill

MODULES = (light, chat)
SKILLS: tuple[Skill, ...] = tuple(skill for module in MODULES for skill in module.SKILLS)
BY_INTENT = {skill.intent: skill for skill in SKILLS}

__all__ = ["BY_INTENT", "SKILLS", "Context", "Skill"]
