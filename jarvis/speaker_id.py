"""Speaker verification: only the enrolled owner's voice may command JARVIS.

ECAPA-TDNN (speechbrain/spkrec-ecapa-voxceleb) turns an utterance into a voice embedding;
the owner's voiceprint is the mean embedding of their past commands. Runs in .venv-tts
(needs torch) and fully offline once the model is downloaded.

    .venv-tts/bin/python -m jarvis.speaker_id    # (re)build the voiceprint from the dataset

The voiceprint is personal biometric data: it stays in the git-ignored .models/.
"""

from __future__ import annotations

import json
import subprocess
import sys

from .config import ROOT, load_config

MODEL_DIR = ROOT / ".models" / "spkrec"
VOICEPRINT = MODEL_DIR / "voiceprint.npy"
SAMPLE_RATE = 16_000


class SpeakerVerifier:
    def __init__(self) -> None:
        from speechbrain.inference.speaker import EncoderClassifier

        self._encoder = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb", savedir=str(MODEL_DIR), run_opts={"device": "cpu"},
        )
        self.voiceprint = None
        if VOICEPRINT.is_file():
            import numpy as np

            self.voiceprint = np.load(VOICEPRINT)

    def embed(self, pcm: bytes):
        import numpy as np
        import torch

        samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32) / 32768
        vector = self._encoder.encode_batch(torch.tensor(samples).unsqueeze(0)).squeeze().numpy()
        return vector / np.linalg.norm(vector)

    def score(self, pcm: bytes) -> float | None:
        """Cosine similarity to the owner's voiceprint; None when nobody is enrolled."""
        if self.voiceprint is None:
            return None
        return float(self.embed(pcm) @ self.voiceprint)


def _read_pcm(path: str) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout


def enroll() -> None:
    """Builds the voiceprint from dataset commands that began with the wake word.

    Follow-ups are left out: during the self-hearing loop some of them were JARVIS's own
    voice (they scored 0.12-0.28 against the owner, while wake-word commands had median 0.56).
    """
    import numpy as np

    config = load_config()
    if config.dataset_dir is None or not (config.dataset_dir / "index.jsonl").is_file():
        raise SystemExit("ไม่มี dataset ให้ลงทะเบียนเสียง ลองสั่งงานด้วยเสียงสักพักก่อน")
    rows = [json.loads(line) for line in (config.dataset_dir / "index.jsonl").open(encoding="utf-8")]
    clips = [str(config.dataset_dir / row["audio"]) for row in rows if row.get("wake")]
    if len(clips) < 5:
        raise SystemExit(f"มีคำสั่งที่เรียกชื่อแค่ {len(clips)} ครั้ง ต้องมีอย่างน้อย 5")
    verifier = SpeakerVerifier()
    vectors = [verifier.embed(_read_pcm(path)) for path in clips]
    own = []
    for index, vector in enumerate(vectors):
        others = np.mean([v for j, v in enumerate(vectors) if j != index], axis=0)
        own.append(float(vector @ (others / np.linalg.norm(others))))
    voiceprint = np.mean(vectors, axis=0)
    voiceprint /= np.linalg.norm(voiceprint)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    np.save(VOICEPRINT, voiceprint)
    own.sort()
    threshold = config.speaker_threshold
    print(f"ลงทะเบียนจาก {len(clips)} คำสั่ง บันทึกที่ {VOICEPRINT}")
    print(f"คะแนนเสียงคุณ (ทีละคลิป): ต่ำสุด {own[0]:.2f} ค่ากลาง {own[len(own) // 2]:.2f} สูงสุด {own[-1]:.2f}")
    print(f"ที่เกณฑ์ {threshold:.2f} จะปฏิเสธเสียงคุณ {sum(s < threshold for s in own)}/{len(own)} คลิป")


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["enroll"]):
        raise SystemExit("usage: python -m jarvis.speaker_id [enroll]")
    enroll()
