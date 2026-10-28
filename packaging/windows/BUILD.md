# Windows installer

Build `Kira vs L-Setup.exe` on a Windows PC that has a network connection. Copy that file to the PC that will run the app. That second PC does not need Python, ffmpeg, or a network.

## What the setup contains

- Embeddable Python 3.12.10, with CPU PyTorch, F5-TTS, and the desktop window installed into it
- `models/f5/model_1250000.safetensors` from `SWivid/F5-TTS`
- `models/vocos/` from `charactr/vocos-mel-24khz`
- `ffmpeg\ffmpeg.exe`, `ffmpeg\ffprobe.exe`, and the shared FFmpeg DLLs. TorchCodec needs those DLLs to read the reference clip. A static `ffmpeg.exe` alone is not enough.
- The L and Kira neutral clips
- A shortcut that opens the skit page in a window on `127.0.0.1`
- The Microsoft Edge WebView2 Runtime installer, used only when that PC does not already have the runtime

Speech uses the PC's RAM. The first line loads the model and keeps it until the window closes. 16 GB of RAM is the practical target. A line can take one to three minutes on CPU.

The F5 weights are CC-BY-NC. Keep the setup file private.

## Build

On the build PC, install [Inno Setup 6](https://jrsoftware.org/isinfo.php). The script downloads Python. It downloads the shared FFmpeg build unless `prereqs\ffmpeg` already contains `avcodec-*.dll` beside `ffmpeg.exe`. A static ffmpeg in that folder is ignored. `voices/l/neutral.wav`, `voices/l/neutral.txt`, `voices/kira/neutral.wav`, and `voices/kira/neutral.txt` must already be in the repo copy. Leave about 15 GB free. The compile is slow.

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

The script writes `packaging\windows\output\Kira vs L-Setup.exe`. The welcome page of this build says version 1.0.6. Speech in that installed app is the local F5 model on CPU. The window asks the program on this PC to speak. It does not call a speech service, and it does not download a model when someone generates a line. A normal line is one rk4 pass. A line long enough to pass about 30 seconds, reference included, still splits on sentences and is joined with no crossfade. Download copies the file into the user's Downloads folder. An MP3 is capped at −1 dBFS. While a line is generated, the page shows a progress screen. A failure is shown in red on the page, under Generate audio, as one short sentence. The full traceback stays in `%LOCALAPPDATA%\Kira vs L\desktop.log`.

The setup treats WebView2 as present when `msedgewebview2.exe` is already under `Program Files (x86)\Microsoft\EdgeWebView\Application`, and it also checks the 32-bit registry view. A setup whose welcome page says 1.0.0 still reports WebView2 as missing on a PC that has it. 1.0.1 is the build that installed, then showed Internal server error.

If pip stops with a path-length error, turn on long paths (Windows 10 or later) and run the script again:

```powershell
New-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

## Check, with the network off

1. Install `Kira vs L-Setup.exe` on a PC that does not have Python.
2. Disconnect the network.
3. Open Kira vs L from the Start menu.
4. Add one L line and one Kira line. Generate each. Play them.

The setup installs the Microsoft Edge WebView2 Runtime when that PC does not already have it. The runtime is bundled, so the target PC stays offline. Windows 11 usually already has it, and the setup skips the install in that case.

Skits are stored in `%LOCALAPPDATA%\Kira vs L`. If the window does not open, read `%LOCALAPPDATA%\Kira vs L\desktop.log`. Uninstall removes the program and the model. Those skits stay.
