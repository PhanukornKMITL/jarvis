"""Voice pipeline settings from config.toml at the repo root; every value has a default."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
PROFILE_PATH = ROOT / "profile.toml"
SECRETS_PATH = ROOT / "secrets.toml"


@dataclass(frozen=True)
class Profile:
    name: str = ""
    birth_date: str = ""
    birth_time: str = ""
    birth_place: str = ""
    diet: str = ""
    about: str = ""
    interests: tuple[str, ...] = ()
    personality: str = ""
    """How JARVIS should come across ([jarvis] personality)."""
    rules: tuple[str, ...] = ()
    """Behaviour rules for conversation ([jarvis] rules)."""
    home: tuple[float, float] | None = None
    """([home] lat, lon); kept here because config.toml is committed and this is where you live."""
    home_place: str = ""
    """([home] place) in words, e.g. the district, so "ผมอยู่ที่ไหน" can be answered."""


@dataclass(frozen=True)
class Config:
    profile: Profile = Profile()
    wake_model: Path = ROOT / ".models" / "ggml-base.bin"
    command_model: Path = ROOT / ".models" / "ggml-thonburian-medium-q5_0.bin"
    wake_port: int = 8768
    command_port: int = 8769
    llm_endpoint: str = "http://127.0.0.1:8080"
    llm_model: Path = ROOT / ".models" / "qwen2-7b-instruct-q4_k_m.gguf"
    llm_args: tuple[str, ...] = ()
    """Extra llama-server arguments for the model ([llm] args)."""
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
    barge_in: bool = True
    follow_up_seconds: float = 5.0
    speaker_check: bool = True
    speaker_threshold: float = 0.30
    speaker_follow_up_threshold: float = 0.20
    remember_across_restarts: bool = True
    """Keep the last 30 minutes of conversation in work/conversation.json ([voice])."""
    weather_lat: float = 13.7563
    weather_lon: float = 100.5018
    dataset_dir: Path | None = ROOT / "work" / "dataset"
    quit_apps: tuple[str, ...] = ("ChatGPT", "Codex")
    gistda_api_key: str = ""
    """From the git-ignored secrets.toml; without it the flood tool is not offered."""


def load_profile(path: Path = PROFILE_PATH) -> Profile:
    if not path.is_file():
        return Profile()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    data, jarvis = raw.get("profile", {}), raw.get("jarvis", {})
    home = raw.get("home", {})
    return Profile(
        home=(float(home["lat"]), float(home["lon"])) if "lat" in home and "lon" in home else None,
        home_place=str(home.get("place", "")),
        name=str(data.get("name", "")),
        birth_date=str(data.get("birth_date", "")),
        birth_time=str(data.get("birth_time", "")),
        birth_place=str(data.get("birth_place", "")),
        diet=str(data.get("diet", "")),
        about=str(data.get("about", "")),
        interests=tuple(str(item) for item in data.get("interests", ())),
        personality=str(jarvis.get("personality", "")),
        rules=tuple(str(item) for item in jarvis.get("rules", ())),
    )


def load_secrets(path: Path = SECRETS_PATH) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def load_config(path: Path = CONFIG_PATH, profile_path: Path = PROFILE_PATH,
                secrets_path: Path = SECRETS_PATH) -> Config:
    gistda_key = str(load_secrets(secrets_path).get("gistda", {}).get("api_key", ""))
    profile = load_profile(profile_path)
    if not path.is_file():
        config = Config(profile=profile, gistda_api_key=gistda_key)
        return _at_home(config)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    stt, llm, tts = data.get("stt", {}), data.get("llm", {}), data.get("tts", {})
    weather, dataset = data.get("weather", {}), data.get("dataset", {})
    run, dashboard = data.get("run", {}), data.get("dashboard", {})
    f5, persona, voice = tts.get("f5", {}), data.get("persona", {}), data.get("voice", {})
    optimize = data.get("optimize", {})
    default = Config()
    dataset_dir = ROOT / dataset["dir"] if "dir" in dataset else default.dataset_dir
    return _at_home(Config(
        profile=profile,
        wake_model=ROOT / stt["wake_model"] if "wake_model" in stt else default.wake_model,
        command_model=ROOT / stt["command_model"] if "command_model" in stt else default.command_model,
        wake_port=int(stt.get("wake_port", default.wake_port)),
        command_port=int(stt.get("command_port", default.command_port)),
        llm_endpoint=llm.get("endpoint", default.llm_endpoint),
        llm_model=ROOT / llm["model"] if "model" in llm else default.llm_model,
        llm_args=tuple(str(arg) for arg in llm.get("args", default.llm_args)),
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
        barge_in=bool(voice.get("barge_in", default.barge_in)),
        follow_up_seconds=float(voice.get("follow_up_seconds", default.follow_up_seconds)),
        speaker_check=bool(voice.get("speaker_check", default.speaker_check)),
        speaker_threshold=float(voice.get("speaker_threshold", default.speaker_threshold)),
        speaker_follow_up_threshold=float(voice.get("speaker_follow_up_threshold", default.speaker_follow_up_threshold)),
        remember_across_restarts=bool(voice.get("remember_across_restarts", default.remember_across_restarts)),
        weather_lat=float(weather.get("lat", default.weather_lat)),
        weather_lon=float(weather.get("lon", default.weather_lon)),
        dataset_dir=dataset_dir if dataset.get("enabled", True) else None,
        quit_apps=tuple(optimize.get("quit_apps", default.quit_apps)),
        gistda_api_key=gistda_key,
    ))


def _at_home(config: Config) -> Config:
    """profile.toml [home] overrides the committed [weather] coordinates."""
    if config.profile.home is None:
        return config
    lat, lon = config.profile.home
    return replace(config, weather_lat=lat, weather_lon=lon)
