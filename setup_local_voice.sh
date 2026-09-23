#!/bin/bash
set -euo pipefail

if ! command -v brew >/dev/null 2>&1; then
  echo "ติดตั้ง Homebrew ก่อน: https://brew.sh"
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  brew install ffmpeg
fi
if ! command -v whisper-cli >/dev/null 2>&1; then
  brew install whisper.cpp
fi

model_dir="$(cd "$(dirname "$0")" && pwd)/.models"
mkdir -p "$model_dir"
if [ ! -s "$model_dir/ggml-base.bin" ]; then
  echo "กำลังดาวน์โหลดโมเดลรู้จำเสียงขนาดเล็ก (ครั้งแรกเท่านั้น)"
  curl --fail --location --retry 2 \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin" \
    --output "$model_dir/ggml-base.bin"
fi
echo "พร้อมแล้ว ใช้: python3 -m jarvis.cli voice"
