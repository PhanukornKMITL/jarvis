"""Voice pipeline settings from config.toml at the repo root; every value has a default."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"


@dataclass(frozen=True)
class Config:
    wake_model: Path = ROOT / ".models" / "ggml-base.bin"
    command_model: Path = ROOT / ".models" / "ggml-small.bin"
    llm_endpoint: str = "http://127.0.0.1:8080"
    tts_voice: str = "Kanya"
    weather_lat: float = 13.7563
    weather_lon: float = 100.5018
    dataset_dir: Path | None = ROOT / "work" / "dataset"


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.is_file():
        return Config()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    stt, llm, tts = data.get("stt", {}), data.get("llm", {}), data.get("tts", {})
    weather, dataset = data.get("weather", {}), data.get("dataset", {})
    default = Config()
    dataset_dir = ROOT / dataset["dir"] if "dir" in dataset else default.dataset_dir
    return Config(
        wake_model=ROOT / stt["wake_model"] if "wake_model" in stt else default.wake_model,
        command_model=ROOT / stt["command_model"] if "command_model" in stt else default.command_model,
        llm_endpoint=llm.get("endpoint", default.llm_endpoint),
        tts_voice=tts.get("voice", default.tts_voice),
        weather_lat=float(weather.get("lat", default.weather_lat)),
        weather_lon=float(weather.get("lon", default.weather_lon)),
        dataset_dir=dataset_dir if dataset.get("enabled", True) else None,
    )
