# Steam Focus Studio - installazione su Windows 11 (PowerShell)
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
# Installa solo software gratuito/open source. Chiede conferma prima di usare winget.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "== Steam Focus Studio: installazione ==" -ForegroundColor Cyan

function Ask($q) { $r = Read-Host "$q [S/n]"; return ($r -eq "" -or $r -match "^[sSyY]") }

# 1) Python 3.10-3.13
$py = $null
foreach ($c in @("py -3.12", "py -3.13", "py -3.11", "py -3.10", "python")) {
  try { $v = & cmd /c "$c --version" 2>$null; if ($v -match "Python 3\.(1[0-3])") { $py = $c; break } } catch {}
}
if (-not $py) {
  if (Ask "Python 3.12 non trovato. Installarlo con winget (Python.Python.3.12)?") {
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    $py = "py -3.12"
  } else { throw "Python 3.10+ necessario" }
}
Write-Host "Python: $py"

# 2) FFmpeg (build GPL di Gyan con NVENC, x264, x265, libass)
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  if (Ask "FFmpeg non trovato. Installarlo con winget (Gyan.FFmpeg)?") {
    winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
    Write-Host "Riapri il terminale dopo l'installazione se ffmpeg non risulta nel PATH." -ForegroundColor Yellow
  }
}

# 3) Ambiente virtuale e dipendenze
if (-not (Test-Path ".venv")) { & cmd /c "$py -m venv .venv" }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[voice,asr,desktop]"

# 4) Voce italiana Piper (facoltativa) - licenza da verificare nella MODEL_CARD
$voices = Join-Path $env:LOCALAPPDATA "SteamFocusStudio\voices"
New-Item -ItemType Directory -Force -Path $voices | Out-Null
if (-not (Get-ChildItem $voices -Filter *.onnx -ErrorAction SilentlyContinue)) {
  if (Ask "Scaricare la voce italiana Piper 'it_IT-paola-medium' (~63 MB, da HuggingFace rhasspy/piper-voices)?") {
    $base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/paola/medium"
    foreach ($f in @("it_IT-paola-medium.onnx", "it_IT-paola-medium.onnx.json", "MODEL_CARD")) {
      Invoke-WebRequest "$base/$f" -OutFile (Join-Path $voices $f)
    }
    Write-Host "Leggi $voices\MODEL_CARD per la licenza del dataset della voce." -ForegroundColor Yellow
  }
}

# 5) Collegamento sul desktop
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Steam Focus Studio.lnk"
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = Join-Path $Root "start.bat"
$s.WorkingDirectory = $Root
$s.Save()

Write-Host "`nVerifica del sistema:" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m focusstudio doctor
Write-Host "`nInstallazione completata. Avvia con 'start.bat' o dal collegamento sul desktop." -ForegroundColor Green
