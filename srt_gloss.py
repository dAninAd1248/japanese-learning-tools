#!/usr/bin/env python3
"""
Japanese SRT Gloss Generator using SudachiPy + Yomitan API

This script reads a Japanese .srt subtitle file, tokenizes each line using Sudachi,
retrieves readings (with kanji converted to hiragana, katakana preserved), and gets English
glosses for each token from Yomitan API. Outputs a Markdown file with gloss tables.

Requirements:
    pip install pysrt requests sudachipy sudachidict-core
    Yomitan API server must be running (http://127.0.0.1:19633)

Usage:
    python srt_gloss.py input.srt output.md
"""

import sys
import re
import time
from typing import List, Dict, Tuple, Any

import pysrt
import requests
from requests.exceptions import Timeout, ConnectTimeout, ReadTimeout
from sudachipy import dictionary, tokenizer

# Yomitan API Configuration
YOMITAN_API_URL = "http://127.0.0.1:19633"
YOMITAN_API_TIMEOUT = 10

# Gloss mapping for common particles
PARTICLE_GLOSS = {
    "が": "SUBJ", "を": "OBJ", "に": "LOC/ADV/DAT", "へ": "DIR", "と": "QUOT/AND",
    "で": "LOC/INST", "から": "SOURCE", "まで": "UNTIL", "より": "THAN/FROM",
    "は": "TOP", "も": "ALSO", "こそ": "EMPH", "さえ": "EVEN", "でも": "EVEN/OR",
    "しか": "ONLY", "か": "Q", "ね": "FP-CONFIRM", "よ": "FP-ASSERT",
    "な": "FP-PROHIB/EMPH", "ぞ": "FP-EMPH", "ぜ": "FP-EMPH", "わ": "FP-EMPH",
    "さ": "FP-LIGHT", "ものか": "FP-RHET", "て": "GER", "ながら": "WHILE",
    "し": "AND/REASON", "ので": "BECAUSE", "のに": "ALTHOUGH", "ば": "COND",
    "たら": "IF", "ても": "EVEN-IF", "くらい": "ABOUT", "ごろ": "AROUND",
    "など": "ETC", "ばかり": "ONLY/JUST", "だけ": "ONLY", "ほど": "EXTENT", "ずつ": "EACH",
}

# Auxiliary verbs and copula
AUX_GLOSS = {
    "だ": "COP", "です": "COP-POL", "である": "COP-FORMAL", "ます": "POL",
    "たい": "WANT", "ない": "NEG", "ぬ": "NEG-CLASS", "れる": "PASS/POT",
    "られる": "PASS/POT/RESP", "せる": "CAUS", "させる": "CAUS", "そう": "SEEM",
    "よう": "SEEM/VOL", "みたい": "SEEM",
}

# Interjections and filler words
INTERJECTION_GLOSS = {
    "えっと": "FILLER", "あのう": "FILLER", "あの": "DEM", "その": "DEM",
    "この": "DEM", "え": "INTJ", "お": "INTJ", "ええ": "INTJ", "ああ": "INTJ",
}

# Special characters
SPECIAL_CHAR_GLOSS = {
    "\uFF5E": "~",  # Full-width prolonged sound mark
    "ー": "~",      # Katakana prolonged sound mark
}

# Sound effects
SOUND_EFFECTS = {
    "ハハハ", "ハハ", "ウハハ", "フフフ", "クスクス", "ププ", "プププ", "クク", "クククク",
    "ウウッ", "ウーン", "ウーッ", "ズズズ", "ズズズッ", "ギギギ", "ドドド", "ドドドド",
    "バタバタ", "パァン", "ドーン", "バーン", "キーン", "ビーン", "ギャー", "ギャアア",
    "ガアア", "えっ", "あっ", "おっ",
}

# Pre-populate cache
gloss_cache = {}
gloss_cache.update(INTERJECTION_GLOSS)
gloss_cache.update(AUX_GLOSS)
gloss_cache.update(PARTICLE_GLOSS)
gloss_cache.update(SPECIAL_CHAR_GLOSS)

# ===== PRIORITY CONFIGURATION =====
# Modify these to change priority order easily

# Priority for selecting gloss term (normalized, dictionary_form, or surface)
# Order matters: first match wins
GLOSS_TERM_PRIORITY = ['dictionary_form', 'surface', 'normalized']

# Priority for selecting reading display
# Each tuple is (condition_name, parameter)
# Conditions: 'is_katakana', 'has_furigana', 'has_kanji', 'default'
READING_PRIORITY = [
    ('is_katakana', 'reading'),        # If original surface is katakana, keep reading as-is
    ('has_furigana', 'furigana'),      # If explicit furigana annotation exists, use it
    ('default', 'hiragana'),           # Otherwise, convert reading to hiragana
]
# ===== END PRIORITY CONFIGURATION =====

class SudachiTokenizer:
    """Japanese tokenizer using SudachiPy"""
    
    SPLIT_MODES = {
        'A': tokenizer.Tokenizer.SplitMode.A,  # Shortest units
        'B': tokenizer.Tokenizer.SplitMode.B,  # Middle units
        'C': tokenizer.Tokenizer.SplitMode.C,  # Longest units
    }
    
    def __init__(self, mode: str = 'C', dict_type: str = 'core'):
        """Initialize Sudachi tokenizer"""
        self.mode = self.SPLIT_MODES.get(mode, tokenizer.Tokenizer.SplitMode.C)
        dict_obj = dictionary.Dictionary(dict=dict_type)
        self.tokenizer_obj = dict_obj.create()
    
    def get_tokens_with_details(self, text: str) -> List[Dict[str, Any]]:
        """Get detailed token information"""
        try:
            morphemes = self.tokenizer_obj.tokenize(text, self.mode)
            tokens = []
            
            for m in morphemes:
                token_info = {
                    'surface': m.surface(),
                    'reading': m.reading_form(),  # Katakana
                    'dictionary_form': m.dictionary_form(),
                    'normalized': m.normalized_form(),
                    'part_of_speech': m.part_of_speech(),
                }
                tokens.append(token_info)
            
            return tokens
        except Exception as e:
            print(f"[Warning] Error tokenizing text: '{text}' - {e}", file=sys.stderr)
            return []


# Global tokenizer
sudachi = SudachiTokenizer(mode='C', dict_type='core')



def has_kanji(text: str) -> bool:
    """Check if text contains kanji"""
    return any(0x4E00 <= ord(char) <= 0x9FFF for char in text)


def is_katakana(word: str) -> bool:
    """Check if word is entirely katakana"""
    if not word:
        return False
    return all(0x30A0 <= ord(c) <= 0x30FF for c in word)


def convert_katakana_to_hiragana(katakana: str) -> str:
    """Convert katakana to hiragana"""
    result = []
    for char in katakana:
        code = ord(char)
        if 0x30A0 <= code <= 0x30FF:
            result.append(chr(code - 0x60))
        else:
            result.append(char)
    return ''.join(result)


def is_likely_sound_effect(word: str) -> bool:

    """Check if word is likely a sound effect"""
    if word in SOUND_EFFECTS:
        return True
    
    if not is_katakana(word):
        return False
    
    # Repeated syllables pattern
    if len(word) >= 2:
        for repeat_len in [1, 2, 3]:
            if len(word) % repeat_len == 0:
                base = word[:repeat_len]
                if all(word[i:i+repeat_len] == base for i in range(0, len(word), repeat_len)):
                    if base in ['ハ', 'プ', 'ク', 'ド', 'バ', 'ギ', 'ピ', 'フ', 'ウ', 'ズ', 'ブ', 'グ', 'ポ', 'パ', 'ペ', 'ボ']:
                        return True
                    if len(base) == 2 and base[1] == 'ッ' and base[0] in ['ハ', 'プ', 'ク', 'ド', 'バ', 'ギ', 'ピ', 'フ', 'ウ', 'ズ', 'ブ', 'グ', 'ポ', 'パ', 'ペ', 'ボ']:
                        return True
    
    if word.endswith(('ッ', 'ン', 'ルッ', 'ーン', 'ーッ')) and len(word) <= 5:
        return True
    
    return False


def get_gloss(word: str) -> str:
    """Fetch gloss from Yomitan API"""
    if word in gloss_cache:
        return gloss_cache[word]
    
    if is_likely_sound_effect(word):
        gloss_cache[word] = "SFX"
        return "SFX"
    
    try:
        response = requests.post(
            f"{YOMITAN_API_URL}/termEntries",
            json={"term": word},
            timeout=YOMITAN_API_TIMEOUT
        )
        
        if response.status_code == 200:
            data = response.json()
            if 'dictionaryEntries' in data and data['dictionaryEntries']:
                entry = data['dictionaryEntries'][0]
                if 'definitions' in entry and entry['definitions']:
                    defn = entry['definitions'][0]
                    glosses = []
                    
                    if 'entries' in defn and defn['entries']:
                        for item in defn['entries']:
                            if isinstance(item, dict) and item.get('type') == 'structured-content':
                                glosses.extend(_extract_glosses(item.get('content', [])))
                    
                    if glosses:
                        gloss = ", ".join(glosses[:2])
                        gloss_cache[word] = gloss
                        return gloss
    
    except (Timeout, ConnectTimeout, ReadTimeout):
        print(f"[Warning] Timeout fetching gloss for '{word}'", file=sys.stderr)
    except Exception:
        pass
    
    gloss_cache[word] = "?"
    return "?"


def _extract_glosses(content: Any, max_depth: int = 10) -> List[str]:
    """Recursively extract glosses from structured content"""
    glosses = []
    if max_depth <= 0:
        return glosses
    
    if isinstance(content, list):
        for item in content:
            glosses.extend(_extract_glosses(item, max_depth - 1))
    elif isinstance(content, dict):
        if content.get('data', {}).get('content') == 'glossary' and content.get('tag') == 'ul':
            gloss_content = content.get('content', [])
            if isinstance(gloss_content, dict):
                gloss_content = [gloss_content]
            
            for item in gloss_content:
                if isinstance(item, dict) and item.get('tag') == 'li':
                    text = _extract_text(item)
                    if text:
                        glosses.append(text)
        else:
            if 'content' in content:
                glosses.extend(_extract_glosses(content['content'], max_depth - 1))
    
    return glosses


def _extract_text(element: Any) -> str:
    """Extract plain text from element"""
    if isinstance(element, str):
        return element
    elif isinstance(element, dict):
        if 'content' in element:
            return _extract_text(element['content'])
    elif isinstance(element, list):
        texts = [_extract_text(item) for item in element]
        return ' '.join(t for t in texts if t)
    return ""


def extract_furigana(text: str) -> Dict[str, str]:
    """Extract furigana annotations"""
    furigana_map = {}
    pattern = r'([^\(（\)）]+)\(([^\)）]+)\)'
    for surface, reading in re.findall(pattern, text):
        furigana_map[surface] = reading
    return furigana_map


def remove_speaker_labels(text: str) -> str:
    """Remove speaker labels but preserve furigana"""
    result = []
    i = 0
    while i < len(text):
        if text[i] in '（(':
            closing = '）' if text[i] == '（' else ')'
            end = text.find(closing, i + 1)
            
            if end != -1:
                if i > 0 and 0x4E00 <= ord(text[i-1]) <= 0x9FFF:
                    result.append(text[i:end+1])
                i = end + 1
            else:
                result.append(text[i])
                i += 1
        else:
            result.append(text[i])
            i += 1
    
    return ''.join(result)


def select_gloss_term(surface: str, normalized: str, dictionary_form: str) -> str:
    """Select which form to use for gloss lookup based on GLOSS_TERM_PRIORITY"""
    terms = {
        'normalized': normalized,
        'dictionary_form': dictionary_form,
        'surface': surface,
    }
    
    for priority_key in GLOSS_TERM_PRIORITY:
        if priority_key in terms and terms[priority_key]:
            return terms[priority_key]
    
    return surface


def select_reading_display(surface: str, reading: str, furigana_map: Dict[str, str]) -> str:
    """Select reading display based on READING_PRIORITY"""
    for condition, param in READING_PRIORITY:
        if condition == 'is_katakana' and is_katakana(surface):
            # Original is katakana - keep reading as-is
            return reading
        elif condition == 'has_furigana' and surface in furigana_map:
            # Explicit furigana annotation
            return furigana_map[surface]
        elif condition == 'default':
            if param == 'hiragana':
                # Convert katakana reading to hiragana
                return convert_katakana_to_hiragana(reading) if reading else surface
            elif param == 'reading':
                # Keep reading as-is
                return reading
            elif param == 'surface':
                # Use surface form
                return surface
    
    # Fallback
    return reading if reading else surface


def process_line(line: str) -> Tuple[List[str], List[str]]:
    """Tokenize line with Sudachi and return readings and glosses"""
    furigana_map = extract_furigana(line)
    tokenization_text = remove_speaker_labels(line)
    
    # Remove furigana annotations and other noise
    tokenization_text = re.sub(r'[\(（][ぁ-ん゙ァ-ヴーa-zA-Z]*[\)）]', '', tokenization_text)
    tokenization_text = re.sub(r'[\[\]【】♪♫♬]', '', tokenization_text)
    tokenization_text = re.sub(r'　', ' ', tokenization_text)
    
    if not tokenization_text.strip():
        return [], []
    
    tokens = sudachi.get_tokens_with_details(tokenization_text)
    if not tokens:
        return [], []
    
    readings = []
    glosses = []
    
    for token in tokens:
        surface = token['surface']
        reading = token['reading']
        normalized = token['normalized']
        dictionary_form = token['dictionary_form']
        
        if not surface:
            continue
        
        # Skip pure punctuation
        if re.fullmatch(r'[\s、。！？\\…\-_・]+', surface):
            continue
        
        # Select reading display based on priority configuration
        display_reading = select_reading_display(surface, reading, furigana_map)
        readings.append(display_reading)
        
        # Select gloss term based on priority configuration
        gloss_term = select_gloss_term(surface, normalized, dictionary_form)
        gloss = get_gloss(gloss_term)
        glosses.append(gloss)
    
    return readings, glosses


def format_timestamp(ts) -> str:
    """Convert pysrt time to MM:SS"""
    return f"{ts.minutes:02d}:{ts.seconds:02d}"


def main(srt_path: str, md_path: str):
    """Process SRT file and generate markdown with glosses"""
    subs = pysrt.open(srt_path)
    total_subs = len(subs)
    segments_processed = 0
    
    with open(md_path, "w", encoding="utf-8") as out:
        for sub in subs:
            segments_processed += 1
            if segments_processed % 100 == 0:
                print(f"Progress: {segments_processed}/{total_subs} segments processed...")
            
            start = format_timestamp(sub.start)
            end = format_timestamp(sub.end)
            out.write(f"### {sub.index}: {start} – {end}\n\n")
            
            lines = sub.text.split("\n")
            for line in lines:
                if not line.strip():
                    continue
                
                readings, glosses = process_line(line)
                
                if not readings or not glosses:
                    continue
                
                # Write original line
                out.write(f"{line}\n\n")
                
                # Build markdown table
                header = "| " + " | ".join(readings) + " |"
                separator = "|" + "|".join([" --- "] * len(readings)) + "|"
                gloss_row = "| " + " | ".join(glosses) + " |"
                
                out.write(header + "\n")
                out.write(separator + "\n")
                out.write(gloss_row + "\n\n")
                
                # Avoid hammering API
                time.sleep(0.05)
            
            out.write("\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python srt_gloss.py <input.srt> <output.md>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
