# Kira vs L

A private page for writing a two-character skit and hearing each line in a cloned English-dub voice. Speech runs on this Mac. The app does not download audio from the show.

L is one voice. Kira is Light.

## Run

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-engines.txt
python -m app
```

Open http://127.0.0.1:8765

The first generate downloads a 4-bit model. Only one line generates at a time. Keep other heavy apps closed. This machine has 8 GB of memory.

`SKIT_ENGINE` is `f5` or `chatterbox`. F5 is the default. F5 copies the neutral clip, so that clip is the delivery. It needs a transcript beside the clip. A timestamped transcript of a longer recording is cut to one even span. Chatterbox copies the clip and then changes intensity. See `.env.example`.

## Clips

Put short clips you are allowed to use here:

```text
voices/l/neutral.wav
voices/l/neutral.txt
voices/kira/neutral.wav
voices/kira/neutral.txt
```

The page speaks in that neutral voice. Other deliveries are not offered.

```bash
python scripts/prepare_clip.py --speaker l --emotion neutral \
  --input /path/to/clip.wav --transcript "The words spoken in the clip."
```

That command makes mono 24 kHz audio and trims to 10 seconds. It does not remove music or a second speaker.

A line can also use a wav you already have, instead of generating one.

## Listen, then keep one engine

When both neutral clips exist:

```bash
python scripts/listen_test.py
```

Same lines, both engines, written to `data/listen/`. Keep the one that still sounds like the same person, and set `SKIT_ENGINE`.

The character listen has not been run in this repo. The clips are yours to add.

## Invite someone

The server binds to `127.0.0.1`. People on another machine can open it only while this Mac is awake.

1. Install Tailscale on this Mac and on their computer, and join the same tailnet.
2. Set `SKIT_PASSWORD` to a shared password.
3. Set `SKIT_HOST` to this Mac's Tailscale address, or `0.0.0.0` if you accept every local interface.
4. Restart `python -m app`.

Starting on a non-local address without `SKIT_PASSWORD` is refused. There are no accounts. Do not put this on the public internet.

The speech model stays on this Mac. A small VPS does not have the memory for it.

## Windows

A Windows PC cannot run the MLX engine. Build `packaging/windows/output/Kira vs L-Setup.exe` on Windows with `packaging/windows/build.ps1`. After install it opens a window, loads F5 into that PC's RAM, and does not use the network. See `packaging/windows/BUILD.md`.

## Licenses

F5-TTS MLX code is MIT. The weight card used here is CC-BY-NC, so keep this an unpaid private tool and keep the attribution. Chatterbox is MIT, and Resemble watermarks its audio. GPT-SoVITS, if you fine-tune later, is MIT.

## A closer voice, later

A short clip is zero-shot. It will be close, not identical. A one-minute clean dry recording per character can be fine-tuned once with GPT-SoVITS on a rented 24 GB NVIDIA GPU, about $1–$3, then the machine is shut off. Training does not fit in 8 GB, and the running model is still about 4 GB, because two characters do not shrink the network. Do that only if the listen test is not close enough.

## Tests

```bash
pip install -r requirements.txt
pytest
```

The tests use a silent stand-in engine. They do not download a model.
