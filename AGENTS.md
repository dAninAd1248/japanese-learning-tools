# AGENTS.md

## Project overview
This repo is a small Japanese subtitle/Anki workflow toolkit for anime study. The scripts work with SRT files, Yomitan dictionary lookups, and AnkiConnect.

## Key scripts
- `srt_gloss.py`: process SRT files with Sudachi + Yomitan and emit gloss markdown.
- `glossed_to_anki.py`: convert gloss markdown into Anki CSV cards.
- `anki_audio.py`: attach audio snippets to Anki notes using SRT timing metadata.
- `eliminate_intjs.py`: scan a deck and delete notes whose term is classified as an interjection by Yomitan.
- `srt_splitter.py`: split audio by subtitle timing.
- `srt_trim.py`: trim subtitle files or audio ranges.

## Important conventions
- Run Python via `python3`, not `python` if it is not guaranteed on PATH.
- Keep CLI behavior consistent with the repo: positional arguments first, optional flags after.
- Prefer working from the repo root unless the script explicitly expects a different relative path.
- When interacting with Anki, follow the AnkiConnect patterns already used in the repo: `findNotes`, `notesInfo`, `deleteNotes`, and `updateNoteFields`.
- When interacting with Yomitan, use the local API at `http://127.0.0.1:19633` and the `/termEntries` endpoint.
- Do not assume the environment has all packages installed; many scripts rely on `requests`, `pysrt`, `SudachiPy`, `jaconv`, and `ffmpeg-python`.

## Common commands
- Dry-run a deck cleanup:
  `python3 eliminate_intjs.py "Deck Name" "Expression" --dry-run`
- Generate gloss markdown:
  `python3 srt_gloss.py input.srt output.md`
- Convert gloss markdown to Anki CSV:
  `python3 glossed_to_anki.py transfer.md transfer.csv`
- Attach audio to deck notes:
  `python3 anki_audio.py "日常::4" ../日常4.flac "[BD Timed] 日常 04-resync_trimmed.srt"`

## Environment notes
- The repo is used with Anki running locally and AnkiConnect enabled.
- Yomitan must be running and reachable at the default API URL above.
- Deck names often include namespace separators like `::` and may contain Japanese text; quote them when using the shell.

## Guidance for AI agents
- Preserve existing script structure and naming patterns rather than introducing a new framework.
- Prefer minimal, direct edits over refactors unless a script is clearly broken.
- Validate Python syntax with `python3 -m py_compile <file>.py` when changing scripts.
- If a tool depends on external services, mention the dependency and the expected local URL in the output.
