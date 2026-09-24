"""Voice pipeline settings from config.toml at the repo root; every value has a default."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
PROFILE_PATH = ROOT / "profile.toml"


@dataclass(frozen=True)
class Profile:
    name: str = ""
    birth_date: str = ""
    birth_time: str = ""
    diet: str = ""


@dataclass(frozen=True)
class Config:
    profile: Profile = Profile()
    wake_model: Path = ROOT / ".models" / "ggml-base.bin"
    command_model: Path = ROOT / ".models" / "ggml-small.bin"
    llm_endpoint: str = "http://127.0.0.1:8080"
    llm_model: Path = ROOT / ".models" / "qwen2-7b-instruct-q4_k_m.gguf"
    fake_devices: tuple[str, ...] = ("light", "garden")
    autostart_voice: bool = True
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8766
    tts_voice: str = "Kanya"
    tts_engine: str = "say"
    f5_port: int = 8767
    f5_ref_audio: Path = ROOT / ".models" / "f5" / "reference.wav"
    f5_step: int = 16
    gender: str = "female"
    weather_lat: float = 13.7563
    weather_lon: float = 100.5018
    dataset_dir: Path | None = ROOT / "work" / "dataset"


def load_profile(path: Path = PROFILE_PATH) -> Profile:
    if not path.is_file():
        return Profile()
    data = tomllib.loads(path.read_text(encoding="utf-8")).get("profile", {})
    return Profile(
        name=str(data.get("name", "")),
        birth_date=str(data.get("birth_date", "")),
        birth_time=str(data.get("birth_time", "")),
        diet=str(data.get("diet", "")),
    )


def load_config(path: Path = CONFIG_PATH, profile_path: Path = PROFILE_PATH) -> Config:
    if not path.is_file():
        return Config(profile=load_profile(profile_path))
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    stt, llm, tts = data.get("stt", {}), data.get("llm", {}), data.get("tts", {})
    weather, dataset = data.get("weather", {}), data.get("dataset", {})
    run, dashboard = data.get("run", {}), data.get("dashboard", {})
    f5, persona = tts.get("f5", {}), data.get("persona", {})
    default = Config()
    dataset_dir = ROOT / dataset["dir"] if "dir" in dataset else default.dataset_dir
    return Config(
        profile=load_profile(profile_path),
        wake_model=ROOT / stt["wake_model"] if "wake_model" in stt else default.wake_model,
        command_model=ROOT / stt["command_model"] if "command_model" in stt else default.command_model,
        llm_endpoint=llm.get("endpoint", default.llm_endpoint),
        llm_model=ROOT / llm["model"] if "model" in llm else default.llm_model,
        fake_devices=tuple(run.get("fake_devices", default.fake_devices)),
        autostart_voice=bool(run.get("autostart_voice", default.autostart_voice)),
        dashboard_host=str(dashboard.get("host", default.dashboard_host)),
        dashboard_port=int(dashboard.get("port", default.dashboard_port)),
        tts_voice=tts.get("voice", default.tts_voice),
        tts_engine=str(tts.get("engine", default.tts_engine)),
        f5_port=int(f5.get("port", default.f5_port)),
        f5_ref_audio=ROOT / f5["ref_audio"] if "ref_audio" in f5 else default.f5_ref_audio,
        f5_step=int(f5.get("step", default.f5_step)),
        gender=str(persona.get("gender", default.gender)),
        weather_lat=float(weather.get("lat", default.weather_lat)),
        weather_lon=float(weather.get("lon", default.weather_lon)),
        dataset_dir=dataset_dir if dataset.get("enabled", True) else None,
    )
