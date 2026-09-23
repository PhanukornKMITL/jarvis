$ErrorActionPreference = 'Stop'

Write-Host 'ตรวจสอบเครื่องมือที่จำเป็น...'
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host 'ไม่พบ ffmpeg: ติดตั้งจาก https://ffmpeg.org/download.html แล้วเพิ่มลง PATH'
    exit 1
}
if (-not (Get-Command whisper-cli -ErrorAction SilentlyContinue)) {
    Write-Host 'ไม่พบ whisper-cli: ติดตั้ง whisper.cpp แล้วเพิ่มโฟลเดอร์ executable ลง PATH'
    exit 1
}

$modelDir = Join-Path $PSScriptRoot '.models'
$modelPath = Join-Path $modelDir 'ggml-large-v3-turbo-q5_0.bin'
New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
if (-not (Test-Path $modelPath)) {
    Write-Host 'กำลังดาวน์โหลดโมเดลเสียง 142 MiB...'
    Invoke-WebRequest `
        -Uri 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin' `
        -OutFile $modelPath
}
Write-Host "พร้อมใช้งาน: $modelPath"
Write-Host 'ดูชื่อไมโครโฟน: ffmpeg -list_devices true -f dshow -i dummy'
Write-Host 'รันเสียง: py -m jarvis.cli voice --audio-device "ชื่อไมโครโฟน"'
