"""Optimization mode: quit apps JARVIS doesn't need so the models stop fighting for RAM.

Apps are asked to quit like Cmd+Q (they can still ask to save), never force-killed, and
only those listed in [optimize] quit_apps. macOS only.
"""

from __future__ import annotations

import platform
import re
import subprocess

# Quitting these would stop JARVIS, this conversation, or the desktop itself.
PROTECTED = {"terminal", "claude", "finder", "iterm2", "iterm", "dock", "systemuiserver"}


def _osascript(script: str) -> str:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15, check=False)
    return result.stdout.strip()


def memory_status() -> dict:
    """Swap in use and macOS memory pressure (normal / warning / critical)."""
    if platform.system() != "Darwin":
        return {}
    swap = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, check=False).stdout
    used = re.search(r"used = ([\d.]+)M", swap)
    level = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                           capture_output=True, text=True, check=False).stdout.strip()
    return {
        "swap_used_gb": round(float(used.group(1)) / 1024, 2) if used else None,
        "pressure": {"1": "normal", "2": "warning", "4": "critical"}.get(level, level or "unknown"),
    }


def running(app: str) -> bool:
    safe = app.replace('"', "")
    return _osascript(f'application "{safe}" is running') == "true"


def optimize(apps: list[str], dry_run: bool = False) -> dict:
    """Quits each listed, running, unprotected app. Returns what happened and memory before/after."""
    if platform.system() != "Darwin":
        return {"ok": False, "error": "optimization mode works on macOS only"}
    before = memory_status()
    results = []
    for app in apps:
        if app.strip().lower() in PROTECTED:
            results.append({"app": app, "result": "protected"})
        elif not running(app):
            results.append({"app": app, "result": "not running"})
        elif dry_run:
            results.append({"app": app, "result": "would quit"})
        else:
            _osascript(f'tell application "{app.replace(chr(34), "")}" to quit')
            results.append({"app": app, "result": "asked to quit" if running(app) else "quit"})
    return {"ok": True, "dry_run": dry_run, "apps": results, "before": before, "after": memory_status()}
