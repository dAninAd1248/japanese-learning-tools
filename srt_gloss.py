#!/usr/bin/env python3
"""
Japanese SRT Gloss Generator using JUMAN++

This script reads a Japanese .srt subtitle file, tokenizes each line using JUMAN++,
converts kanji to hiragana (leaving katakana untouched), retrieves English
glosses for each token, and outputs a Markdown file with gloss tables.

Requirements:
    pip install pyknp jaconv jisho-api pysrt
    JUMAN++ must be installed separately (see implementation plan)

Usage:
    python srt_gloss.py input.srt output.md
"""

import sys
import re
import time
import shutil
import os
from io import StringIO

# Suppress JUMAN++ stderr output
devnull = open(os.devnull, 'w')
old_stderr = sys.stderr

# Monkey-patch distutils for Python 3.14+ compatibility with pyknp
if 'distutils' not in sys.modules:
    import types
    distutils = types.ModuleType('distutils')
    spawn = types.ModuleType('spawn')
    
    def find_executable(exe, path=None):
        """Find executable in PATH."""
        return shutil.which(exe) or exe
    
    spawn.find_executable = find_executable
    distutils.spawn = spawn
    sys.modules['distutils'] = distutils
    sys.modules['distutils.spawn'] = spawn

import pysrt
from pyknp import Juman
import jaconv
from jisho_api.word import Word
from collections import OrderedDict
from requests.exceptions import Timeout, ConnectTimeout, ReadTimeout

# ------------------------------
# Configuration
# ------------------------------
# Gloss mapping for common particles (based on example and linguistic glossing)
PARTICLE_GLOSS = {
    # Case particles (格助詞)
    "が": "SUBJ",                    # subject marker (unified - no alternatives)
    "を": "OBJ",                      # object marker (unified)
    "に": "LOC/ADV/DAT",              # location/adverbial/dative (merged multiple functions)
    "へ": "DIR",                       # direction
    "と": "QUOT/AND",                  # quotative/and (two distinct functions)
    "で": "LOC/INST",                  # location/instrumental (two distinct functions)
    "から": "SOURCE",                   # source/from
    "まで": "UNTIL",                     # until/up to
    "より": "THAN/FROM",                 # than/from (archaic/formal)
    
    # Binding particles (係助詞)
    "は": "TOP",                       # topic marker
    "も": "ALSO",                      # also/even
    "こそ": "EMPH",                     # emphasis (not just FP)
    "さえ": "EVEN",                     # even
    "でも": "EVEN/OR",                  # even/or (distinct meanings)
    "しか": "ONLY",                     # only (with negative)
    
    # Final particles (終助詞)
    "か": "Q",                         # question marker
    "ね": "FP-CONFIRM",                 # confirmation-seeking
    "よ": "FP-ASSERT",                  # assertion/information
    "な": "FP-PROHIB/EMPH",             # prohibition (with verbs) or emphasis
    "ぞ": "FP-EMPH",                    # emphatic (masculine)
    "ぜ": "FP-EMPH",                    # emphatic (masculine, casual)
    "わ": "FP-EMPH",                    # emphatic (feminine/regional)
    "さ": "FP-LIGHT",                   # light emphasis
    "ものか": "FP-RHET",                 # rhetorical question
    
    # Conjunctive particles (接続助詞)
    "て": "GER",                       # gerundive/te-form connector
    "で": "GER",                       # gerundive (voiced)
    "ながら": "WHILE",                   # while/although
    "し": "AND/REASON",                 # listing/reason
    "から": "BECAUSE",                   # because (different from source particle)
    "ので": "BECAUSE",                   # because (softer)
    "のに": "ALTHOUGH",                  # although/despite
    "ば": "COND",                       # conditional
    "たら": "IF",                        # if/when
    "ても": "EVEN-IF",                   # even if
    
    # Adverbial particles (副助詞)
    "くらい": "ABOUT",                   # approximately
    "ごろ": "AROUND",                    # around (time)
    "など": "ETC",                       # etcetera
    "ばかり": "ONLY/JUST",               # only/just
    "まで": "EVEN",                      # even (extent)
    "だけ": "ONLY",                      # only
    "ほど": "EXTENT",                    # extent/degree
    "ずつ": "EACH",                      # each
}

# Extended mapping for copula and auxiliary verbs commonly needed
AUX_GLOSS = {
    "だ": "COP",                        # plain copula
    "です": "COP-POL",                   # polite copula
    "である": "COP-FORMAL",               # formal copula
    "ます": "POL",                       # polite auxiliary
    "たい": "WANT",                      # desire auxiliary
    "ない": "NEG",                       # negative auxiliary
    "ぬ": "NEG-CLASS",                   # classical negative
    "れる": "PASS/POT",                   # passive/potential
    "られる": "PASS/POT/RESP",            # passive/potential/honorific
    "せる": "CAUS",                       # causative
    "させる": "CAUS",                     # causative
    "そう": "SEEM",                       # hearsay/appearance
    "よう": "SEEM/VOL",                   # appearance/volitional
    "みたい": "SEEM",                      # appearance (colloquial)
}

# Common interjections and filler words typically mishandled
INTERJECTION_GLOSS = {
    "えっと": "FILLER",                   # filler word (um, like, uh)
    "あのう": "FILLER",                   # filler word
    "あの": "DEM",                        # that/that's (when used as filler)
    "その": "DEM",                        # that/that's
    "この": "DEM",                        # this/this's
    "あ": "INTJ",                        # ah/oh
    "え": "INTJ",                        # eh/what
    "お": "INTJ",                        # oh
    "ええ": "INTJ",                       # yeah/yeah yeah
    "ああ": "INTJ",                       # yeah/ugh
}

# Known sound effects (katakana) to skip or mark specially
SOUND_EFFECTS = {
    "ハハハ", "ハハ", "ウハハ", "フフフ", "クスクス",  # laughter
    "ププ", "プププ", "クク", "クククク",
    "ウウッ", "ウーン", "ウーッ",  # groaning
    "ズズズ", "ズズズッ", "ギギギ",  # grinding/dragging
    "ドドド", "ドドドド", "バタバタ",  # footsteps/impacts
    "パァン", "ドーン", "バーン",  # explosions
    "キーン", "ビーン",  # sounds
    "ギャー", "ギャアア", "ガアア",  # screams
    "えっ", "あっ", "おっ",  # exclamations (hiragana versions)
}

# Known phrases that JUMAN often missplits - these will be merged back together
MERGEABLE_PHRASES = {
    "えっと": ["え", "っ", "と"],          # filler word
    "あのう": ["あ", "の", "う"],          # filler word  
    "そのう": ["そ", "の", "う"],          # filler variant
    "えっ": ["え", "っ"],                   # exclamation
    "あっ": ["あ", "っ"],                   # exclamation
    "おっ": ["お", "っ"],                   # exclamation
    "いいえ": ["い", "い", "え"],           # "no" (formal)
    "いいや": ["い", "い", "や"],           # "no" (informal)
    "ああ": ["あ", "あ"],                   # "ah yeah"
    "ええ": ["え", "え"],                   # "yeah"
}

# Initialize JUMAN++ 
jumanpp = Juman()  # Uses jumanpp command

# Simple in-memory cache for dictionary lookups
gloss_cache = {}

# Pre-populate cache with common interjections and filler words
gloss_cache.update(INTERJECTION_GLOSS)
for afx, gloss in AUX_GLOSS.items():
    gloss_cache[afx] = gloss
for ptc, gloss in PARTICLE_GLOSS.items():
    gloss_cache[ptc] = gloss

def merge_morphemes(readings, glosses):
    """
    Merge morphemes that form known problematic phrases.
    This undoes JUMAN's missplitting of interjections like えっと.
    """
    i = 0
    while i < len(readings):
        # Try to match from longest phrases to shortest
        for phrase, components in sorted(MERGEABLE_PHRASES.items(), key=lambda x: -len(x[1])):
            # Check if the next N morphemes match this phrase's components
            if i + len(components) <= len(readings):
                # Compare readings (allow for mismatches due to reading variations)
                match = True
                for j, component in enumerate(components):
                    # Be lenient - just check if it contains the component
                    if component not in readings[i + j]:
                        match = False
                        break
                
                if match:
                    # Merge these morphemes
                    merged_reading = ''.join(readings[i:i+len(components)])
                    merged_gloss = gloss_cache.get(phrase, get_gloss(phrase))
                    
                    # Replace the morphemes with merged version
                    readings[i:i+len(components)] = [merged_reading]
                    glosses[i:i+len(components)] = [merged_gloss]
                    break
        
        i += 1
    
    return readings, glosses

def has_kanji(text):
    """Return True if the string contains any CJK Unified Ideograph."""
    for char in text:
        if 0x4E00 <= ord(char) <= 0x9FFF:
            return True
    return False

def get_reading(mrph):
    """
    Return the appropriate reading for a morpheme from JUMAN++.
    - For tokens with kanji: use the reading field (already in hiragana)
    - For tokens without kanji: use surface form (preserves katakana)
    """
    surface = mrph.midasi
    
    if has_kanji(surface):
        # JUMAN++ provides reading in hiragana directly 
        return mrph.yomi
    else:
        # No kanji - keep surface as is (preserves katakana)
        return surface

def is_likely_sound_effect(word):
    """
    Check if a katakana word is likely a sound effect.
    Uses known sound effects list and pattern matching.
    """
    # Check against known sound effects
    if word in SOUND_EFFECTS:
        return True
    
    # Check if word is all katakana
    if not all(0x30A0 <= ord(c) <= 0x30FF for c in word):
        return False
    
    # Pattern 1: Repeated syllables (e.g., ハハハ, ドドド, ププ)
    if len(word) >= 3:
        # Check if consists of 2-3 char repetitions
        for repeat_len in [1, 2, 3]:
            if len(word) % repeat_len == 0:
                base = word[:repeat_len]
                if all(word[i:i+repeat_len] == base for i in range(0, len(word), repeat_len)):
                    # Additional check: common sound effect syllables
                    if base in ['ハ', 'プ', 'ク', 'ド', 'バ', 'ギ', 'ピ', 'フ', 'ウ']:
                        return True
    
    # Pattern 2: Known suffixes for sound effects
    if word.endswith(('ッ', 'ン', 'ルッ', 'ーン', 'ーッ')):
        # More likely to be a sound effect
        if len(word) <= 5:  # Keep short ones that seem like effects
            return True
    
    return False

def get_gloss(word):
    """
    Fetch English gloss for a word using jisho-api.
    Returns a string (first English definition or a fallback).
    """
    if word in gloss_cache:
        return gloss_cache[word]
    
    # Check if it's likely a sound effect (before network request)
    if is_likely_sound_effect(word):
        gloss_cache[word] = "SFX"
        return "SFX"

    try:
        # Query Jisho
        result = Word.request(word)  # there is no timeout parameter
        if result and result.data and len(result.data) > 0:
            # Take the first sense of the first entry
            first_entry = result.data[0]
            if first_entry.senses and len(first_entry.senses) > 0:
                english_defs = first_entry.senses[0].english_definitions
                if english_defs:
                    gloss = ", ".join(english_defs)
                    gloss_cache[word] = gloss
                    return gloss
    except (Timeout, ConnectTimeout, ReadTimeout) as e:
        # Warn about timeout but continue gracefully
        print(f"[Warning] Timeout fetching gloss for '{word}'", file=sys.stderr)
    except Exception as e:
        # Fail gracefully for other errors
        pass

    # Fallback
    gloss_cache[word] = "?"
    return "?"

def sanitize_line(line):
    """
    Remove or normalize characters that JUMAN++ struggles with.
    This includes sound effects, special symbols, and non-standard characters.
    """
    # Remove content within parentheses (ASCII and full-width, proper pairing)
    line = re.sub(r'\([^)]*\)', '', line)      # ASCII parens: (...)
    line = re.sub(r'（[^）]*）', '', line)      # Full-width parens: （...）
    
    # Remove common sound effects and symbols
    line = re.sub(r'[♪♫♬]', '', line)  # Musical notes
    line = re.sub(r'[…]', '。', line)   # Ellipsis → period
    line = re.sub(r'[\[\]【】]', '', line)  # Brackets (parens already handled)
    line = re.sub(r'[""『』「」【】]', '', line)  # Quotation marks
    line = re.sub(r'[\-\—\–]', '', line)  # Dashes
    line = re.sub(r'[・·]', '', line)  # Middle dots
    line = re.sub(r'\\', '', line)  # Backslashes
    line = re.sub(r'‐', '', line)  # Hyphens
    line = re.sub(r'　', ' ', line)  # Full-width space
    line = re.sub(r'\s+', ' ', line)  # Normalize whitespace
    return line.strip()

def process_line(line):
    """
    Tokenize a single subtitle line using JUMAN++ and return two lists:
        readings: list of kana readings (with kanji converted)
        glosses:  list of English gloss strings
    """
    # Sanitize the line first
    sanitized = sanitize_line(line)
    if not sanitized:
        return [], []
    
    try:
        # Suppress JUMAN++ error output temporarily
        sys.stderr = StringIO()
        result = jumanpp.analysis(sanitized)
        sys.stderr = old_stderr
    except (ValueError, Exception) as e:
        # If JUMAN++ fails to parse, return empty (skip problematic lines)
        sys.stderr = old_stderr
        return [], []
    
    readings = []
    glosses = []

    for mrph in result.mrph_list():  # Iterate through morphemes
        surface = mrph.midasi
        
        # Skip pure punctuation (optional, keeps tables clean)
        if re.fullmatch(r'[、。！？「」]', surface):
            continue

        # Get reading (handles kanji conversion automatically)
        reading = get_reading(mrph)
        readings.append(reading)

        # Use dictionary form (genkei) for lookup 
        base = mrph.genkei
        gloss = get_gloss(base if base else surface)
        glosses.append(gloss)

    # Merge missplitted phrases back together
    readings, glosses = merge_morphemes(readings, glosses)
    
    return readings, glosses

def format_timestamp(ts):
    """Convert pysrt time to a short string 'MM:SS'."""
    return f"{ts.minutes:02d}:{ts.seconds:02d}"

def main(srt_path, md_path):
    subs = pysrt.open(srt_path)
    total_subs = len(subs)
    segments_processed = 0

    with open(md_path, "w", encoding="utf-8") as out:
        for sub in subs:
            segments_processed += 1
            if segments_processed % 100 == 0:
                print(f"Progress: {segments_processed}/{total_subs} segments processed...")
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
                
                # Skip lines that couldn't be processed
                if not readings or not glosses:
                    continue

                # Build markdown table (two rows: readings, glosses)
                # Use pipe separator and align center if desired
                header = "| " + " | ".join(readings) + " |"
                separator = "|" + "|".join([" --- "] * len(readings)) + "|"
                gloss_row = "| " + " | ".join(glosses) + " |"

                out.write(header + "\n")
                out.write(separator + "\n")
                out.write(gloss_row + "\n\n")

                # Small delay to avoid hitting Jisho too hard (reduced for speed)
                time.sleep(0.05)

            # Blank line between subtitle entries
            out.write("\n")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python srt_gloss.py <input.srt> <output.md>")
        sys.exit(1)
    try:
        main(sys.argv[1], sys.argv[2])
    finally:
        devnull.close()
        sys.stderr = old_stderr