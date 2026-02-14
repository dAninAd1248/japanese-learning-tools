#!/usr/bin/env python3
"""
Japanese SRT Gloss Generator

This script reads a Japanese .srt subtitle file, tokenizes each line,
converts kanji to hiragana (leaving katakana untouched), retrieves English
glosses for each token, and outputs a Markdown file with gloss tables.

Requirements:
    pip install sudachipy sudachidict-core jaconv jisho-api pysrt

Usage:
    python srt_gloss.py input.srt output.md
"""

import sys
import re
import time
import pysrt
from sudachipy import Dictionary, SplitMode
import jaconv
from jisho_api.word import Word
from collections import OrderedDict

# ------------------------------
# Configuration
# ------------------------------
# Gloss mapping for common particles (based on example and linguistic glossing)
PARTICLE_GLOSS = {
    "は": "TOP",      # topic marker
    "が": "SUBJ",     # subject marker
    "を": "OBJ",      # object marker
    "に": "ADV",      # adverbial / dative (simplified)
    "へ": "DIR",      # direction
    "で": "LOC",      # location / instrument
    "と": "QUOT",     # quotative / and
    "から": "from",   # source
    "まで": "until",
    "の": "NMLZ",     # nominalizer / possessive
    "だ": "COP",      # copula
    "ね": "FP",       # final particle
    "よ": "FP",       # final particle
    "か": "Q",        # question marker
    "も": "also",
    "や": "and",
    "し": "and",
    "な": "FP",       # (various)
    "ぞ": "FP",
    "ぜ": "FP",
    "さ": "FP",
}

# Initialize Sudachi tokenizer (split mode C yields more fine-grained tokens)
tokenizer = Dictionary().create()
mode = SplitMode.C

# Simple in-memory cache for dictionary lookups
gloss_cache = {}

def has_kanji(text):
    """Return True if the string contains any CJK Unified Ideograph."""
    for char in text:
        if 0x4E00 <= ord(char) <= 0x9FFF:
            return True
    return False

def get_reading(token):
    """
    Return the appropriate reading for a token.
    - If the token contains kanji, convert its katakana reading to hiragana.
    - Otherwise (hiragana/katakana only), return the surface form.
    """
    surface = token.surface()
    if has_kanji(surface):
        # Sudachi provides reading in katakana; convert to hiragana for output
        katakana_reading = token.reading_form()
        return jaconv.kata2hira(katakana_reading)
    else:
        # No kanji – keep surface as is (may be hiragana or katakana)
        return surface

def get_gloss(word):
    """
    Fetch English gloss for a word using jisho-api.
    Returns a string (first English definition or a fallback).
    """
    if word in gloss_cache:
        return gloss_cache[word]

    # Special handling for particles
    if word in PARTICLE_GLOSS:
        gloss_cache[word] = PARTICLE_GLOSS[word]
        return PARTICLE_GLOSS[word]

    try:
        # Query Jisho
        result = Word.request(word)
        if result and result.data and len(result.data) > 0:
            # Take the first sense of the first entry
            first_entry = result.data[0]
            if first_entry.senses and len(first_entry.senses) > 0:
                english_defs = first_entry.senses[0].english_definitions
                if english_defs:
                    gloss = ", ".join(english_defs)
                    gloss_cache[word] = gloss
                    return gloss
    except Exception as e:
        # Fail gracefully – you might want to log this
        pass

    # Fallback
    gloss_cache[word] = "?"
    return "?"

def process_line(line):
    """
    Tokenize a single subtitle line and return two lists:
        readings: list of kana readings (with kanji converted)
        glosses:  list of English gloss strings
    """
    tokens = tokenizer.tokenize(line, mode)
    readings = []
    glosses = []

    for token in tokens:
        surface = token.surface()
        # Skip pure punctuation (optional, but keeps tables clean)
        if re.fullmatch(r'[、。！？「」]', surface):
            continue

        reading = get_reading(token)
        readings.append(reading)

        # Determine the word to look up: use the dictionary form (base form) if available
        base = token.dictionary_form()
        gloss = get_gloss(base if base else surface)
        glosses.append(gloss)

    return readings, glosses

def format_timestamp(ts):
    """Convert pysrt time to a short string 'MM:SS'."""
    return f"{ts.minutes:02d}:{ts.seconds:02d}"

def main(srt_path, md_path):
    subs = pysrt.open(srt_path)

    with open(md_path, "w", encoding="utf-8") as out:
        for sub in subs:
            # Header with index and time range
            start = format_timestamp(sub.start)
            end = format_timestamp(sub.end)
            out.write(f"### {sub.index}: {start} – {end}\n\n")

            # Split subtitle text into individual lines (by \n)
            lines = sub.text.split("\n")
            for line in lines:
                if not line.strip():
                    continue
                readings, glosses = process_line(line)

                # Build markdown table (two rows: readings, glosses)
                # Use pipe separator and align center if desired
                header = "| " + " | ".join(readings) + " |"
                separator = "|" + "|".join([" --- "] * len(readings)) + "|"
                gloss_row = "| " + " | ".join(glosses) + " |"

                out.write(header + "\n")
                out.write(separator + "\n")
                out.write(gloss_row + "\n\n")

                # Small delay to avoid hitting Jisho too hard
                time.sleep(0.2)

            # Blank line between subtitle entries
            out.write("\n")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python srt_gloss.py <input.srt> <output.md>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])