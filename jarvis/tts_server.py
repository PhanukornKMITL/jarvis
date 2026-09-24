"""F5-TTS Thai voice server: keeps the model loaded and returns WAV for text.

Runs inside .venv-tts (needs torch and f5-tts-th); the rest of JARVIS stays stdlib-only
and talks to it over HTTP on 127.0.0.1. Generated audio is cached on disk, so fixed
replies like "เปิดไฟให้แล้วครับ" are instant after the first time.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import ROOT, load_config

CACHE_DIR = ROOT / "work" / "tts_cache"
MAX_TEXT_CHARS = 400
MAX_VERIFY_BYTES = 2_000_000  # ~45 s of 16 kHz audio as base64


def main() -> None:
    import soundfile as sf
    from f5_tts_th.tts import TTS

    config = load_config()
    ref_audio = config.f5_ref_audio
    ref_text_path = ref_audio.with_suffix(".txt")
    if not ref_audio.is_file() or not ref_text_path.is_file():
        print(f"ไม่พบเสียงต้นแบบ {ref_audio} หรือ {ref_text_path.name}", file=sys.stderr, flush=True)
        raise SystemExit(1)
    ref_text = ref_text_path.read_text(encoding="utf-8").strip()
    started = time.monotonic()
    tts = TTS(model="v1", hf_cache_dir=str(ROOT / ".models" / "f5"))
    print(f"F5-TTS loaded in {time.monotonic() - started:.1f}s", flush=True)
    verifier = None
    if config.speaker_check:
        from .speaker_id import SpeakerVerifier

        verifier = SpeakerVerifier()
        print("speaker check: " + ("owner enrolled" if verifier.voiceprint is not None
                                   else "nobody enrolled yet (run python -m jarvis.speaker_id)"), flush=True)
    verify_lock = threading.Lock()
    lock = threading.Lock()  # one inference at a time: the model is not thread-safe
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(ref_audio.read_bytes() + ref_text.encode() + str(config.f5_step).encode()).hexdigest()

    def synthesize(text: str) -> bytes:
        path = CACHE_DIR / f"{hashlib.sha256((fingerprint + text).encode()).hexdigest()}.wav"
        if path.is_file():
            return path.read_bytes()
        with lock:
            wav = tts.infer(ref_audio=str(ref_audio), ref_text=ref_text, gen_text=text, step=config.f5_step)
        buffer = io.BytesIO()
        sf.write(buffer, wav, 24000, format="WAV")
        path.write_bytes(buffer.getvalue())
        return buffer.getvalue()

    port = config.f5_port

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._reply(200, b'{"status":"ok"}', "application/json")
            else:
                self._reply(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            # Same guards as the dashboard: only local callers, only JSON (blocks cross-site forms).
            if self.headers.get("Host", "") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self._reply(403, b"forbidden host", "text/plain")
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self._reply(415, b"application/json required", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(min(length, MAX_VERIFY_BYTES)))
            except ValueError:
                self._reply(400, b"bad request", "text/plain")
                return
            if self.path == "/verify":
                self._verify(body)
                return
            text = str(body.get("text", "")).strip() if isinstance(body, dict) else ""
            if self.path != "/synthesize" or not text or len(text) > MAX_TEXT_CHARS:
                self._reply(400, b"text required (max 400 chars)", "text/plain")
                return
            started = time.monotonic()
            audio = synthesize(text)
            print(f"{time.monotonic() - started:4.1f}s  {text}", flush=True)
            self._reply(200, audio, "audio/wav")

        def _verify(self, body: object) -> None:
            if verifier is None:
                self._reply(200, b'{"score": null}', "application/json")
                return
            try:
                pcm = base64.b64decode(body["pcm"], validate=True)  # 16 kHz mono int16
            except (KeyError, TypeError, ValueError):
                self._reply(400, b"pcm (base64) required", "text/plain")
                return
            with verify_lock:
                score = verifier.score(pcm)
            self._reply(200, json.dumps({"score": score}).encode(), "application/json")

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"F5-TTS listening on http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
