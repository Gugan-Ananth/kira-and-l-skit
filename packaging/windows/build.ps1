# Assemble the offline Windows payload, then compile "Kira vs L-Setup.exe".
# Run this on Windows, from the repository root:
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# The network is required on this build PC. The PC that later runs the setup exe is not.
# A venv is not used. It keeps a path back to the Python on the build PC.
# payload\runtime is the official embeddable Python and starts on a machine with no Python.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

foreach ($Voice in @("l", "kira")) {
  foreach ($Ext in @("wav", "txt")) {
    $Clip = Join-Path $Root "voices\$Voice\neutral.$Ext"
    if (-not (Test-Path $Clip)) {
      throw "Missing voices\$Voice\neutral.$Ext. Those clips have to be on this PC before the setup is built."
    }
  }
}

function Find-SharedFfmpeg([string]$Dir) {
  if (-not (Test-Path $Dir)) { return $null }
  $dll = Get-ChildItem $Dir -Recurse -Filter "avcodec-*.dll" -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if (-not $dll) { return $null }
  $exe = Join-Path $dll.DirectoryName "ffmpeg.exe"
  if (Test-Path $exe) { return $exe }
  return $null
}

# TorchCodec loads libtorchcodec_coreN.dll, which needs the FFmpeg shared DLLs.
# A static ffmpeg.exe has none of those, so the speech engine fails on Windows.
$FfmpegExe = Find-SharedFfmpeg (Join-Path $Root "prereqs\ffmpeg")
if (-not $FfmpegExe) {
  $FfmpegZip = Join-Path $env:TEMP "ffmpeg-master-latest-win64-gpl-shared.zip"
  $FfmpegExtract = Join-Path $env:TEMP "ffmpeg-master-latest-win64-gpl-shared"
  $FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"
  Write-Host "Downloading the shared FFmpeg build"
  Invoke-WebRequest -Uri $FfmpegUrl -OutFile $FfmpegZip
  if (Test-Path $FfmpegExtract) { Remove-Item -Recurse -Force $FfmpegExtract }
  Expand-Archive -Path $FfmpegZip -DestinationPath $FfmpegExtract -Force
  $FfmpegExe = Find-SharedFfmpeg $FfmpegExtract
  if (-not $FfmpegExe) { throw "The FFmpeg download did not contain avcodec DLLs." }
}

$Iscc = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Install Inno Setup 6, then run this script again." }

$Payload = Join-Path $PSScriptRoot "payload"
if (Test-Path $Payload) { Remove-Item -Recurse -Force $Payload }
New-Item -ItemType Directory -Force -Path $Payload | Out-Null

$PythonVersion = "3.12.10"
$ZipName = "python-$PythonVersion-embed-amd64.zip"
$ZipUrl = "https://www.python.org/ftp/python/$PythonVersion/$ZipName"
$Runtime = Join-Path $Payload "runtime"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
$ZipPath = Join-Path $env:TEMP $ZipName
Write-Host "Downloading Python $PythonVersion"
Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath
Expand-Archive -Path $ZipPath -DestinationPath $Runtime -Force
Remove-Item $ZipPath

# Isolated mode. ".." is the install folder, so `pythonw -m app.desktop` finds the app package.
@"
python312.zip
.
Lib\site-packages
..

import site
"@ | Set-Content -Path (Join-Path $Runtime "python312._pth") -Encoding ascii
New-Item -ItemType Directory -Force -Path (Join-Path $Runtime "Lib\site-packages") | Out-Null

$Python = Join-Path $Runtime "python.exe"
if (-not (Test-Path $Python)) { throw "The embeddable Python zip did not contain python.exe." }

$GetPip = Join-Path $env:TEMP "skitdesk-get-pip.py"
Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPip
Write-Host "Installing pip"
& $Python $GetPip
Remove-Item $GetPip

Write-Host "Installing CPU PyTorch and the app"
& $Python -m pip install --upgrade pip
& $Python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
& $Python -m pip install -r (Join-Path $Root "requirements.txt") -r (Join-Path $Root "requirements-windows.txt")
# f5-tts can replace that wheel with the default PyPI torch, which is a CUDA build.
& $Python -m pip install --index-url https://download.pytorch.org/whl/cpu --force-reinstall --no-deps torch torchaudio
& $Python -c "import torch; cuda = torch.version.cuda; assert cuda is None, 'expected a CPU torch, found CUDA ' + str(cuda); print('torch', torch.__version__)"

Write-Host "Copying the app"
foreach ($Name in @("app", "static", "voices")) {
  Copy-Item -Recurse -Force (Join-Path $Root $Name) (Join-Path $Payload $Name)
}
Get-ChildItem -Path $Payload -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force

$F5 = Join-Path $Payload "models\f5"
$Vocos = Join-Path $Payload "models\vocos"
New-Item -ItemType Directory -Force -Path $F5, $Vocos | Out-Null
& $Python -c @"
from huggingface_hub import hf_hub_download, snapshot_download
from pathlib import Path
ckpt = hf_hub_download('SWivid/F5-TTS', 'F5TTS_v1_Base/model_1250000.safetensors')
target = Path(r'$F5') / 'model_1250000.safetensors'
target.write_bytes(Path(ckpt).read_bytes())
if target.stat().st_size < 1_000_000_000:
    raise SystemExit('F5 checkpoint is too small: ' + str(target.stat().st_size))
snapshot_download('charactr/vocos-mel-24khz', local_dir=r'$Vocos')
vocos = Path(r'$Vocos')
if not any(vocos.rglob('config.yaml')):
    raise SystemExit('Vocos config.yaml is missing')
weights = list(vocos.rglob('*.safetensors')) + list(vocos.rglob('*.bin'))
if not weights:
    raise SystemExit('Vocos model weights are missing')
for path in [target, *vocos.rglob('*')]:
    if path.is_symlink():
        raise SystemExit('download left a link, which will break offline: ' + str(path))
print(target)
"@

$FfmpegDir = Split-Path $FfmpegExe
$FfmpegDest = Join-Path $Payload "ffmpeg"
New-Item -ItemType Directory -Force -Path $FfmpegDest | Out-Null
Copy-Item $FfmpegExe (Join-Path $FfmpegDest "ffmpeg.exe")
$Ffprobe = Join-Path $FfmpegDir "ffprobe.exe"
if (Test-Path $Ffprobe) { Copy-Item $Ffprobe (Join-Path $FfmpegDest "ffprobe.exe") }
$FfmpegDlls = @(Get-ChildItem $FfmpegDir -Filter "*.dll")
if (-not $FfmpegDlls) { throw "The shared FFmpeg folder has no DLLs." }
$FfmpegDlls | Copy-Item -Destination $FfmpegDest
if (-not (Get-ChildItem $FfmpegDest -Filter "avcodec-*.dll")) {
  throw "FFmpeg shared libraries were not copied into the installer."
}
$TorchCodec = Join-Path $Runtime "Lib\site-packages\torchcodec"
if (Test-Path $TorchCodec) {
  $FfmpegDlls | Copy-Item -Destination $TorchCodec
}
# Embeddable Python does not search PATH for DLL dependencies. This runs before the app imports.
@"
import os
from pathlib import Path

folder = Path(__file__).resolve().parents[3] / "ffmpeg"
if folder.is_dir() and hasattr(os, "add_dll_directory"):
    try:
        os.add_dll_directory(str(folder))
    except OSError:
        pass
    os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
"@ | Set-Content -Path (Join-Path $Runtime "Lib\site-packages\sitecustomize.py") -Encoding ascii

Write-Host "Checking the packed app imports"
& $Python -c "import app.desktop, app.engines.f5_torch; print('app import ok')"

# Offline Evergreen Standalone Installer, x64. The setup runs it only when WebView2 is missing.
$WebView2 = Join-Path $PSScriptRoot "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
$WebView2Url = "https://go.microsoft.com/fwlink/?linkid=2124701"
Write-Host "Downloading the WebView2 runtime installer"
Invoke-WebRequest -Uri $WebView2Url -OutFile $WebView2
if ((Get-Item $WebView2).Length -lt 50MB) {
  throw "The WebView2 download is too small to be the x64 standalone installer."
}

Write-Host "Compiling the setup exe"
& $Iscc (Join-Path $PSScriptRoot "skitdesk.iss")
Write-Host "Wrote packaging\windows\output\Kira vs L-Setup.exe"
