"""Benchmark JARVIS's understanding and answers on bench/cases.toml, by category.

    python3 -m jarvis.bench                       # every case once, against the running LLM
    python3 -m jarvis.bench --runs 3 --only daily_life.food
    python3 -m jarvis.bench --endpoint http://127.0.0.1:8090 --label qwen3.5-9b

It goes through voice.answer() exactly as a spoken command would (rules, tool choice,
tools, chat), without audio. Replies vary between runs, so --runs repeats each case.
Results go to work/bench/<time>-<label>.json (git-ignored: replies can contain profile data)
so models and changes can be compared on the same questions.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import time
import tomllib
from datetime import datetime
from pathlib import Path

from .config import ROOT, load_config
from .persona import apply_persona
from .skills.base import Context
from .voice import answer

CASES_PATH = ROOT / "bench" / "cases.toml"
RESULTS_DIR = ROOT / "work" / "bench"


def load_cases(path: Path = CASES_PATH) -> list[tuple[str, dict]]:
    """[("operation.control", case), ...] in file order."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return [(f"{group}.{topic}", case) for group, topics in raw.items()
            for topic, cases in topics.items() for case in cases]


def placeholders(config) -> dict[str, str]:
    place = config.profile.home_place
    district = re.search(r"อำเภอ\s*(\S+)", place)
    province = re.search(r"จังหวัด\s*(\S+)", place)
    return {"name": config.profile.name or "ผู้ใช้", "home_district": district.group(1) if district else "",
            "home_province": province.group(1) if province else ""}


def fill(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def run_case(config, case: dict, values: dict[str, str]) -> dict:
    turns = case["say"] if isinstance(case["say"], list) else [case["say"]]
    ctx = Context("127.0.0.1", 8765, config)
    started = time.perf_counter()
    for index, said in enumerate(fill(turn, values) for turn in turns):
        if index:  # time passes between turns: age what the tools fetched
            ctx.facts.update({k: (at - 60 * case.get("age_min", 0), line) for k, (at, line) in ctx.facts.items()})
        decided, reply, _ = answer(ctx, said)
        spoken = apply_persona(reply if isinstance(reply, str) else " ".join(reply), config.gender)
        ctx.history.append((said, spoken))
    route = decided.split(":", 1)[0]
    tools = sorted({part.split("(")[0] for part in decided.split(":", 1)[1].split("+")}) if ":" in decided else []
    failures = []
    if "route" in case and route != case["route"]:
        failures.append(f"route {route} (want {case['route']})")
    missing = [tool for tool in case.get("tools", []) if tool not in tools]
    if missing:
        failures.append(f"missing tools {missing}")
    if "mention" in case and not re.search(fill(case["mention"], values), spoken):
        failures.append(f"should mention /{case['mention']}/")
    if "avoid" in case and re.search(fill(case["avoid"], values), spoken):
        failures.append(f"should avoid /{case['avoid']}/")
    return {"say": turns[-1] if len(turns) == 1 else turns, "decided": decided, "reply": spoken,
            "seconds": round(time.perf_counter() - started, 2), "failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--only", help="category prefix, e.g. operation or daily_life.food")
    parser.add_argument("--endpoint", help="another llama-server to test instead of the configured one")
    parser.add_argument("--label", help="name for the result file (default: model file)")
    args = parser.parse_args()
    config = load_config()
    if args.endpoint:
        config = dataclasses.replace(config, llm_endpoint=args.endpoint)
    label = args.label or config.llm_model.stem
    values = placeholders(config)
    cases = [(category, case) for category, case in load_cases()
             if not args.only or category.startswith(args.only)]
    results: dict[str, list] = {}
    for category, case in cases:
        for _ in range(args.runs):
            result = run_case(config, case, values)
            results.setdefault(category, []).append(result)
            mark = "✓" if not result["failures"] else "✗ " + "; ".join(result["failures"])
            print(f"[{result['seconds']:.1f}s] {category} {result['say']} → {result['reply'][:90]}  {mark}")
    print(f"\n{label}")
    total = passed = 0
    for category, items in results.items():
        ok = sum(not item["failures"] for item in items)
        total, passed = total + len(items), passed + ok
        seconds = sorted(item["seconds"] for item in items)[len(items) // 2]
        print(f"  {category:28} {ok}/{len(items)}  median {seconds:.1f}s")
    print(f"  {'total':28} {passed}/{total}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M}-{label}.json"
    path.write_text(json.dumps({"label": label, "runs": args.runs, "results": results}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"  saved {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
