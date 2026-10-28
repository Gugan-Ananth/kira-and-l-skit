# Voice bank

Put short clips you are allowed to use in these folders. One speaker, no music, no second voice.

```
voices/l/neutral.wav
voices/l/neutral.txt
voices/kira/neutral.wav
voices/kira/neutral.txt
```

`neutral` is the clip F5 copies. The `.txt` file is the exact words spoken in that clip. A longer recording can use timestamped lines, `[mm:ss.ss - mm:ss.ss] words`, and F5 keeps one even span of about ten seconds.

Convert a file with:

```
python scripts/prepare_clip.py --speaker l --emotion neutral --input /path/to/clip.wav --transcript "The words in the clip."
```

The script trims to 10 seconds, mono, 24 kHz. It does not remove music or another speaker.
