#!/usr/bin/env python3
"""Review suspicious Lapis notes and optionally delete confirmed candidates.

The script finds notes matching both a deck and tag, then flags notes when:

* the stored definition identifies a low-information interjection; or
* the expression is not present in the note's sentence.

Use ``--dry-run`` to inspect candidates without prompting for deletion.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from html.parser import HTMLParser
from typing import Dict, List, Sequence

try:
    import requests
except ImportError:
    requests = None

from eliminate_intjs import anki_invoke, chunked


ANKI_DEFAULT_URL = "http://localhost:8765"


def clean_html(value: str) -> str:
    """Remove HTML and normalize whitespace for display and comparisons."""
    text = html.unescape(value or "")
    text = re.sub(
        r"<(style|script)\b[^>]*>.*?</\1>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def trim_for_display(value: str, limit: int = 240) -> str:
    """Keep console table cells readable without changing classification data."""
    text = clean_html(value)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def to_katakana(value: str) -> str:
    """Convert hiragana characters to katakana for sentence matching."""
    return "".join(
        chr(ord(character) + 0x60)
        if "\u3041" <= character <= "\u3096"
        else character
        for character in value
    )


def is_verb_definition(main_definition: str) -> bool:
    """Check for a structured Yomitan verb part-of-speech marker."""
    return bool(
        re.search(
            r"<[^>]*"
            r'(?=[^>]*data-sc-content=["\']part-of-speech-info["\'])'
            r'(?=[^>]*data-sc-code=["\']v[^"\']+["\'])[^>]*>',
            main_definition,
        )
    )


def verb_stem_in_sentence(
    expression: str, reading: str, sentence: str, definition: str
) -> bool:
    """Allow one inflectional ending to be absent for a stored verb."""
    if not is_verb_definition(definition):
        return False

    verb_endings = "うくぐすつぬぶむるウクグスツヌブル"
    forms = [expression]
    if "kana" in clean_html(definition).lower():
        forms.extend((reading, to_katakana(reading)))

    for form in forms:
        if len(form) > 1 and form[-1] in verb_endings and form[:-1] in sentence:
            return True
    return False


class GlossaryParser(HTMLParser):
    """Extract text from Yomitan glossary list items."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if dict(attrs).get("data-sc-content") == "glossary":
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth and tag == "li":
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)


def extract_glosses(main_definition: str) -> List[str]:
    """Extract only Yomitan glossary text, excluding examples and metadata."""
    parser = GlossaryParser()
    parser.feed(main_definition)
    text = clean_html(" ".join(parser.parts))
    return [gloss.strip() for gloss in re.split(r"[;；]", text) if gloss.strip()]


def get_note_infos(
    anki_url: str, note_ids: Sequence[int], batch_size: int
) -> List[Dict]:
    """Fetch noteInfo records in batches."""
    note_infos: List[Dict] = []
    for batch in chunked(list(note_ids), batch_size):
        result = anki_invoke(anki_url, "notesInfo", notes=batch)
        if result:
            note_infos.extend(result)
    return note_infos


def get_note_fields(note_info: Dict) -> Dict[str, str]:
    """Convert AnkiConnect's field objects to a simple name-to-value map."""
    fields: Dict[str, str] = {}
    for name, value in (note_info.get("fields", {}) or {}).items():
        fields[name] = value.get("value", "") if isinstance(value, dict) else str(value)
    return fields


def is_stupid_interjection(expression: str, main_definition: str) -> bool:
    """Recognize short, kana-only filler interjections from stored Yomitan HTML."""
    if not re.search(
        r"<[^>]*"
        r'(?=[^>]*data-sc-content=["\']part-of-speech-info["\'])'
        r'(?=[^>]*data-sc-code=["\']int["\'])[^>]*>',
        main_definition,
    ):
        return False

    reading = expression
    if not re.fullmatch(r"[ぁ-ゖァ-ヺー]{1,4}", reading):
        return False

    glosses = extract_glosses(main_definition)
    if not glosses:
        return False

    filler = re.compile(
        r"^(?:oh|ah|wow|hey|well|huh|um|uh|"
        r"(?:an?\s+)?(?:interjection|exclamation|expression|"
        r"(?:sound|word)\s+(?:of|expressing)\s+)?"
        r"(?:surprise|disbelief|doubt|hesitation|agreement|disagreement|"
        r"relief|disappointment|dismissal|attention|calling|astonishment|"
        r"surprise|shock|wonder|contempt|disgust|annoyance|emotion)"
        r"(?:\s+or\s+(?:surprise|disbelief|doubt|hesitation|agreement|"
        r"disagreement|relief|disappointment|attention|astonishment|shock))?)"
        r"[.!?！？」]*$",
        re.IGNORECASE,
    )
    return all(filler.fullmatch(gloss) for gloss in glosses)


def query_note_ids(anki_url: str, deck_name: str, tag: str) -> List[int]:
    """Find notes in a deck with the requested tag."""
    escaped_deck = deck_name.replace("\\", "\\\\").replace('"', '\\"')
    return list(
        anki_invoke(
            anki_url,
            "findNotes",
            query=f'deck:"{escaped_deck}" tag:{tag}',
        )
        or []
    )


def candidate_reason(fields: Dict[str, str]) -> str | None:
    """Return why a note should be reviewed, or None when it looks valid."""
    expression = clean_html(fields.get("Expression", ""))
    reading = clean_html(fields.get("ExpressionReading", ""))
    raw_definition = fields.get("MainDefinition", "")
    definition = clean_html(raw_definition)
    sentence = clean_html(fields.get("Sentence", ""))
    if not expression:
        return None

    expression_in_sentence = expression in sentence
    kana_allowed = "kana" in definition.lower()
    katakana_reading = to_katakana(reading)
    definition_has_reading_form = reading in definition or katakana_reading in definition
    readings_in_sentence = (
        kana_allowed or definition_has_reading_form
    ) and any(candidate and candidate in sentence for candidate in (reading, katakana_reading))
    verb_stem_matches = verb_stem_in_sentence(
        expression, reading, sentence, raw_definition
    )
    if not expression_in_sentence and not readings_in_sentence and not verb_stem_matches:
        return "expression not in sentence"

    if is_stupid_interjection(reading or expression, raw_definition):
        return "interjection"

    return None


def print_table(rows: Sequence[Dict[str, str]]) -> None:
    """Print candidate note fields in a fixed, readable table."""
    columns = ["ExpressionFurigana", "MainDefinition", "Sentence", "note id"]
    separator = " | "
    print(separator.join(columns))
    print(separator.join("-" * len(column) for column in columns))
    for row in rows:
        print(separator.join(row[column] for column in columns))


def parse_exclusions(raw: str) -> set[int]:
    """Parse space-separated note IDs supplied by the user."""
    exclusions: set[int] = set()
    for value in raw.split():
        try:
            exclusions.add(int(value))
        except ValueError as exc:
            raise ValueError(f"Invalid note ID: {value!r}") from exc
    return exclusions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Review suspicious Lapis notes in a tagged Anki deck."
    )
    parser.add_argument("deck_name", help="Anki deck name")
    parser.add_argument("tag", help="Anki tag to scan")
    parser.add_argument("--anki-url", default=ANKI_DEFAULT_URL)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidates and do not prompt or delete notes",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if requests is None:
        print("Error: requests library required. Install with: pip install requests", file=sys.stderr)
        return 1

    note_ids = query_note_ids(args.anki_url, args.deck_name, args.tag)
    note_infos = get_note_infos(args.anki_url, note_ids, max(1, args.batch_size))
    candidates: List[Dict[str, str]] = []

    for note_info in note_infos:
        fields = get_note_fields(note_info)
        reason = candidate_reason(fields)
        if reason is None:
            continue
        candidates.append(
            {
            "ExpressionFurigana": trim_for_display(
                fields.get("ExpressionFurigana", "")
            ),
            "MainDefinition": trim_for_display(fields.get("MainDefinition", ""), limit=80),
            "Sentence": trim_for_display(fields.get("Sentence", "")),
                "note id": str(note_info.get("noteId", note_info.get("id", ""))),
                "reason": reason,
            }
        )

    print(f"Scanned {len(note_infos)} notes; found {len(candidates)} candidates.")
    if not candidates:
        return 0
    print_table(candidates)
    print("\nReasons: " + ", ".join(
        f"{row['note id']} ({row['reason']})" for row in candidates
    ))

    if args.dry_run:
        print("Dry run requested; no notes were deleted.")
        return 0

    try:
        excluded = parse_exclusions(
            input("\nEnter note IDs to keep, separated by spaces (or press Enter for none): ")
        )
    except (EOFError, ValueError) as exc:
        print(f"Aborted: {exc}", file=sys.stderr)
        return 1

    to_delete = [
        int(row["note id"]) for row in candidates if int(row["note id"]) not in excluded
    ]
    if not to_delete:
        print("No notes selected for deletion.")
        return 0

    anki_invoke(args.anki_url, "deleteNotes", notes=to_delete)
    print(f"Deleted {len(to_delete)} notes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
