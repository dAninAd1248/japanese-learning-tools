#!/usr/bin/env python3
"""
Delete Anki notes whose term is classified by Yomitan as an interjection.

The script queries all notes in a deck via AnkiConnect, reads the requested
term field, asks Yomitan for the term entry, and deletes notes whose response
contains an interjection part-of-speech marker.

Usage:
    python eliminate_intjs.py <deck_name> <term_field>
        [--anki-url http://localhost:8765]
        [--yomitan-url http://127.0.0.1:19633]
        [--batch-size 100]
        [--dry-run]
        [--yes]

Examples:
    python eliminate_intjs.py "Japanese" "Term" --dry-run
    python eliminate_intjs.py "Japanese" "Expression" --yes

Requirements:
    pip install requests
    Anki running with AnkiConnect installed
    Yomitan running with its API enabled
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from typing import Dict, Iterable, List, Sequence, Tuple

try:
    import requests
except ImportError:
    requests = None


ANKI_DEFAULT_URL = "http://localhost:8765"
YOMITAN_DEFAULT_URL = "http://127.0.0.1:19633"
YOMITAN_TIMEOUT_SECONDS = 10
INTERJECTION_MARKERS = ("interjection", "kandoushi", "感動詞")


def anki_invoke(anki_url: str, action: str, **params):
    """Invoke an AnkiConnect action and return the result payload."""
    if requests is None:
        raise RuntimeError("requests library required. Install with: pip install requests")

    try:
        response = requests.post(
            anki_url,
            json={"action": action, "version": 6, "params": params},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            "Cannot connect to AnkiConnect. Make sure Anki is running and AnkiConnect is enabled."
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"AnkiConnect request failed: {exc}") from exc

    if payload.get("error"):
        raise RuntimeError(f"AnkiConnect error: {payload['error']}")

    return payload.get("result")


def chunked(items: Sequence[int], batch_size: int) -> Iterable[List[int]]:
    """Yield items in fixed-size batches."""
    for start in range(0, len(items), batch_size):
        yield list(items[start : start + batch_size])


def escape_anki_query(text: str) -> str:
    """Escape a string for use inside an Anki search query."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def get_deck_note_ids(anki_url: str, deck_name: str) -> List[int]:
    """Return all note IDs in the deck."""
    query = f'deck:"{escape_anki_query(deck_name)}"'
    note_ids = anki_invoke(anki_url, "findNotes", query=query)
    return list(note_ids or [])


def get_note_infos(anki_url: str, note_ids: Sequence[int], batch_size: int) -> List[Dict]:
    """Fetch noteInfo records for note IDs in batches."""
    note_infos: List[Dict] = []
    for batch in chunked(list(note_ids), batch_size):
        result = anki_invoke(anki_url, "notesInfo", notes=batch)
        if result:
            note_infos.extend(result)
    return note_infos


def get_note_fields(note_info: Dict) -> Dict[str, str]:
    """Normalize AnkiConnect noteInfo fields to a simple name -> value map."""
    raw_fields = note_info.get("fields", {}) or {}
    fields: Dict[str, str] = {}
    for field_name, field_data in raw_fields.items():
        if isinstance(field_data, dict) and "value" in field_data:
            fields[field_name] = field_data["value"]
        else:
            fields[field_name] = str(field_data)
    return fields


def normalize_term(value: str) -> str:
    """Strip markup and normalize whitespace around a note term."""
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def recursive_contains_interjection_marker(value) -> bool:
    """Detect interjection markers anywhere in the Yomitan response tree."""
    if isinstance(value, str):
        lower = value.lower()
        return any(marker.lower() in lower for marker in INTERJECTION_MARKERS)

    if isinstance(value, list):
        return any(recursive_contains_interjection_marker(item) for item in value)

    if isinstance(value, dict):
        title = str(value.get("title", "")).lower()
        content = value.get("content")
        content_text = content.lower() if isinstance(content, str) else ""
        data = value.get("data", {})
        data_content = ""
        if isinstance(data, dict):
            data_content = str(data.get("content", "")).lower()
            if data_content == "part-of-speech-info" and str(value.get("code", "")).lower() == "int":
                return True

        if any(marker.lower() in title for marker in INTERJECTION_MARKERS):
            return True
        if any(marker.lower() in content_text for marker in INTERJECTION_MARKERS):
            return True
        if any(marker.lower() in data_content for marker in INTERJECTION_MARKERS):
            return True

        return any(recursive_contains_interjection_marker(item) for item in value.values())

    return False


def is_interjection_term(yomitan_url: str, term: str) -> bool:
    """Ask Yomitan whether a term is an interjection."""
    if requests is None:
        raise RuntimeError("requests library required. Install with: pip install requests")

    response = requests.post(
        f"{yomitan_url.rstrip('/')}/termEntries",
        json={"term": term},
        timeout=YOMITAN_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    payload = response.json()
    first_definition = payload["dictionaryEntries"][0]["definitions"][0]['entries'][0]
    return recursive_contains_interjection_marker(first_definition)


def collect_interjection_notes(
    note_infos: Sequence[Dict],
    term_field: str,
    yomitan_url: str,
) -> Tuple[List[Tuple[int, str]], List[Tuple[int, str]]]:
    """Split notes into interjection matches and non-matches."""
    matches: List[Tuple[int, str]] = []
    skipped: List[Tuple[int, str]] = []
    term_cache: Dict[str, bool] = {}

    for note_info in note_infos:
        note_id = note_info.get("noteId") or note_info.get("id")
        if note_id is None:
            continue

        fields = get_note_fields(note_info)
        raw_term = fields.get(term_field, "")
        term = normalize_term(raw_term)

        if not term:
            skipped.append((int(note_id), "missing term"))
            continue

        if term not in term_cache:
            term_cache[term] = is_interjection_term(yomitan_url, term)

        if term_cache[term]:
            matches.append((int(note_id), term))
        else:
            skipped.append((int(note_id), term))

    return matches, skipped


def delete_notes(anki_url: str, note_ids: Sequence[int], batch_size: int) -> None:
    """Delete notes from Anki in batches."""
    for batch in chunked(list(note_ids), batch_size):
        anki_invoke(anki_url, "deleteNotes", notes=batch)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete notes in an Anki deck when Yomitan classifies the note term as an interjection.",
    )
    parser.add_argument("deck_name", help="Name of the Anki deck to scan")
    parser.add_argument("term_field", help="Name of the note field that contains the term")
    parser.add_argument("--anki-url", default=ANKI_DEFAULT_URL, help="AnkiConnect URL")
    parser.add_argument("--yomitan-url", default=YOMITAN_DEFAULT_URL, help="Yomitan API URL")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for AnkiConnect calls")
    parser.add_argument("--dry-run", action="store_true", help="Show matches without deleting anything")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive deletion confirmation")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if requests is None:
        print("Error: requests library required. Install with: pip install requests", file=sys.stderr)
        return 1

    print(f"Scanning deck: {args.deck_name}")
    print(f"Term field: {args.term_field}")

    note_ids = get_deck_note_ids(args.anki_url, args.deck_name)
    if not note_ids:
        print("No notes found.")
        return 0

    print(f"Found {len(note_ids)} notes")

    note_infos = get_note_infos(args.anki_url, note_ids, max(1, args.batch_size))
    if not note_infos:
        print("No note metadata returned from AnkiConnect.")
        return 1

    matches, skipped = collect_interjection_notes(note_infos, args.term_field, args.yomitan_url)

    print(f"Interjection matches: {len(matches)}")
    if matches:
        for note_id, term in matches[:20]:
            print(f"  {note_id}: {term}")
        if len(matches) > 20:
            print(f"  ... and {len(matches) - 20} more")

    if args.dry_run:
        print("Dry run requested; no notes were deleted.")
        return 0

    if not matches:
        print("Nothing to delete.")
        return 0

    if not args.yes:
        prompt = f"Delete {len(matches)} notes from '{args.deck_name}'? [y/N] "
        if sys.stdin.isatty():
            answer = input(prompt).strip().lower()
            if answer not in {"y", "yes"}:
                print("Aborted.")
                return 1
        else:
            print("Refusing to delete without --yes in a non-interactive session.", file=sys.stderr)
            return 1

    delete_notes(args.anki_url, [note_id for note_id, _ in matches], max(1, args.batch_size))
    print(f"Deleted {len(matches)} notes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
