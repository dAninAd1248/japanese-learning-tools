#!/usr/bin/env python3
"""
Japanese SRT Gloss Generator using Yomitan API

This script reads a Japanese .srt subtitle file, tokenizes each line using Yomitan's tokenizer,
retrieves readings (with kanji converted to hiragana, katakana preserved), and gets English
glosses for each token from the Yomitan API. Outputs a Markdown file with gloss tables.

Requirements:
    pip install pysrt requests
    Yomitan API server must be running (http://127.0.0.1:19633)

Usage:
    python srt_gloss.py input.srt output.md
"""

import sys
import re
import time
import os
from io import StringIO
import requests

import pysrt
from requests.exceptions import Timeout, ConnectTimeout, ReadTimeout

# Yomitan API Configuration
YOMITAN_API_URL = "http://127.0.0.1:19633"
YOMITAN_API_TIMEOUT = 10

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

# Special characters and markers that should be preserved
SPECIAL_CHAR_GLOSS = {
    "\uFF5E": "~",                         # full-width prolonged sound mark (U+FF5E)
    "ー": "~",                            # katakana prolonged sound mark
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

# Known phrases that tokenizer might missplit - these will be merged back together
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

# Simple in-memory cache for dictionary lookups
gloss_cache = {}

# Pre-populate cache with common interjections and filler words
gloss_cache.update(INTERJECTION_GLOSS)
for afx, gloss in AUX_GLOSS.items():
    gloss_cache[afx] = gloss
for ptc, gloss in PARTICLE_GLOSS.items():
    gloss_cache[ptc] = gloss
for char, gloss in SPECIAL_CHAR_GLOSS.items():
    gloss_cache[char] = gloss

def merge_morphemes(readings, glosses):
    """
    Merge tokens that form known problematic phrases.
    This undoes tokenizer's missplitting of interjections like えっと.
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

def is_katakana(word):
    """Check if the word is entirely katakana."""
    if not word:
        return False
    return all(0x30A0 <= ord(c) <= 0x30FF for c in word)

def is_likely_sound_effect(word):
    """
    Check if a katakana word is likely a sound effect.
    Uses known sound effects list and pattern matching.
    """
    # Check against known sound effects
    if word in SOUND_EFFECTS:
        return True
    
    # Check if word is all katakana
    if not is_katakana(word):
        return False
    
    # Pattern 1: Repeated syllables (e.g., ハハハ, ハッハッハッ, ドドド, ズズズッ)
    if len(word) >= 2:
        # Check if consists of 1-3 char repetitions
        for repeat_len in [1, 2, 3]:
            if len(word) % repeat_len == 0:
                base = word[:repeat_len]
                if all(word[i:i+repeat_len] == base for i in range(0, len(word), repeat_len)):
                    # Check if base is a common sound effect syllable
                    # Can be single char like ハ, or with small tsu like ハッ
                    if base in ['ハ', 'プ', 'ク', 'ド', 'バ', 'ギ', 'ピ', 'フ', 'ウ', 'ズ', 'ブ', 'グ', 'ポ', 'パ', 'ペ', 'ボ']:
                        return True
                    # Also check two-char patterns with small tsu
                    if len(base) == 2 and base[1] == 'ッ':
                        if base[0] in ['ハ', 'プ', 'ク', 'ド', 'バ', 'ギ', 'ピ', 'フ', 'ウ', 'ズ', 'ブ', 'グ', 'ポ', 'パ', 'ペ', 'ボ']:
                            return True
    
    # Pattern 2: Known suffixes for sound effects
    if word.endswith(('ッ', 'ン', 'ルッ', 'ーン', 'ーッ')):
        # More likely to be a sound effect
        if len(word) <= 5:  # Keep short ones that seem like effects
            return True
    
    return False

def _extract_text_from_element(element):
    """Extract plain text from a Yomitan structured content element."""
    if isinstance(element, str):
        return element
    elif isinstance(element, dict):
        if 'content' in element:
            result = _extract_text_from_element(element['content'])
            return result
    elif isinstance(element, list):
        texts = []
        for item in element:
            text = _extract_text_from_element(item)
            if text:
                texts.append(text)
        return ' '.join(texts) if texts else None
    
    return None

def _extract_glosses_from_structured(content, max_depth=10):
    """
    Recursively extract glossary items from Yomitan structured content.
    """
    glosses = []
    if max_depth <= 0:
        return glosses
    
    if isinstance(content, list):
        for item in content:
            glosses.extend(_extract_glosses_from_structured(item, max_depth - 1))
    elif isinstance(content, dict):
        # Check if this is a glossary list item
        if content.get('data', {}).get('content') == 'glossary' and content.get('tag') == 'ul':
            # Extract all glossary items from this list
            # content['content'] can be a single dict or a list of dicts
            gloss_content = content.get('content', [])
            if isinstance(gloss_content, dict):
                # Single glossary item - wrap in list
                gloss_content = [gloss_content]
            
            for item in gloss_content:
                if isinstance(item, dict) and item.get('tag') == 'li':
                    text = _extract_text_from_element(item)
                    if text:
                        glosses.append(text)
        else:
            # Recursively search in child content
            if 'content' in content:
                glosses.extend(_extract_glosses_from_structured(content['content'], max_depth - 1))
    
    return glosses

def get_gloss(word):
    """
    Fetch English gloss for a word using Yomitan API.
    Returns a string (first English definition or a fallback).
    """
    if word in gloss_cache:
        return gloss_cache[word]
    
    # Check if it's likely a sound effect (before network request)
    if is_likely_sound_effect(word):
        gloss_cache[word] = "SFX"
        return "SFX"

    try:
        # Query Yomitan API
        response = requests.post(
            f"{YOMITAN_API_URL}/termEntries",
            json={"term": word},
            timeout=YOMITAN_API_TIMEOUT
        )
        
        if response.status_code == 200:
            term_data = response.json()
            
            if 'dictionaryEntries' in term_data and term_data['dictionaryEntries']:
                entry = term_data['dictionaryEntries'][0]
                
                if 'definitions' in entry and entry['definitions']:
                    defn = entry['definitions'][0]
                    
                    # Extract glosses from structured content
                    glosses = []
                    if 'entries' in defn and defn['entries']:
                        for entry_item in defn['entries']:
                            if isinstance(entry_item, dict) and entry_item.get('type') == 'structured-content':
                                glosses.extend(_extract_glosses_from_structured(entry_item.get('content', [])))
                    
                    if glosses:
                        # Join first 2 glosses
                        gloss = ", ".join(glosses[:2])
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

def extract_furigana(text):
    """
    Extract word-reading pairs from Japanese furigana notation: 漢字(ふりがな)
    Returns a dict mapping surface forms to their readings.
    Example: "阪本(さかもと)" -> {"阪本": "さかもと"}
    """
    furigana_map = {}
    # Match patterns like: 文字(ふりがな) or 文(ぶん)
    pattern = r'([^\(（\)）]+)\(([^\)）]+)\)'
    matches = re.findall(pattern, text)
    for surface, reading in matches:
        furigana_map[surface] = reading
    return furigana_map

def merge_continuous_katakana(readings, glosses):
    """
    Merge consecutive katakana tokens into a single token.
    Katakana sound effects are often split by the tokenizer but should be kept together.
    Example: [ズズ, ズ, ッ] -> [ズズズッ]
    """
    if not readings:
        return readings, glosses
    
    merged_readings = []
    merged_glosses = []
    i = 0
    
    while i < len(readings):
        current_text = readings[i]
        
        # Check if current reading is katakana
        if is_katakana(current_text):
            # Accumulate all consecutive katakana
            combined_katakana = current_text
            combined_indices = [i]
            j = i + 1
            
            while j < len(readings) and is_katakana(readings[j]):
                combined_katakana += readings[j]
                combined_indices.append(j)
                j += 1
            
            # Only re-lookup gloss if we actually merged multiple tokens
            # If it's a single katakana token, keep the existing gloss
            if len(combined_indices) == 1:
                merged_readings.append(current_text)
                merged_glosses.append(glosses[i])
            else:
                # We merged multiple tokens, so get a new gloss for the combination
                merged_readings.append(combined_katakana)
                combined_gloss = get_gloss(combined_katakana)
                merged_glosses.append(combined_gloss)
            
            # Skip past all the merged tokens
            i = j
        else:
            # Not katakana, keep as is
            merged_readings.append(current_text)
            merged_glosses.append(glosses[i])
            i += 1
    
    return merged_readings, merged_glosses
def smart_merge_tokens(readings, glosses):
    """
    Post-process tokenization to merge tokens that shouldn't be split.
    Only merge if: both glosses are unknown (?) OR both tokens are single-char
    This avoids aggressively merging small morphemes that already have meanings.
    """
    if not readings or not glosses:
        return readings, glosses
    
    merged_readings = []
    merged_glosses = []
    i = 0
    
    while i < len(readings):
        current_reading = readings[i]
        current_gloss = glosses[i]
        
        # Only consider merging in very specific cases
        should_merge = False
        merged_reading_result = None
        merged_gloss_result = None
        
        if i + 1 < len(readings):
            next_reading = readings[i + 1]
            next_gloss = glosses[i + 1]
            concatenated = current_reading + next_reading
            
            # Merge only if ALL these conditions are true:
            # 1. Both tokens are unknown (?) OR both are single-character
            # 2. Neither token is a known particle/aux/interjection
            both_unknown = current_gloss == '?' and next_gloss == '?'
            both_single_char = len(current_reading) == 1 and len(next_reading) == 1
            # Check if the readings are known morphemes (particles, aux, etc.)
            not_known_particles = (current_reading not in gloss_cache and next_reading not in gloss_cache)
            
            if (both_unknown or both_single_char) and not_known_particles and len(concatenated) <= 4:
                # Try to look up the concatenation
                try:
                    response = requests.post(
                        f"{YOMITAN_API_URL}/termEntries",
                        json={"term": concatenated},
                        timeout=2
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if 'dictionaryEntries' in data and data['dictionaryEntries']:
                            merged_reading_result = concatenated
                            merged_gloss_result = get_gloss(concatenated)
                            should_merge = True
                except:
                    pass
        
        if should_merge:
            merged_readings.append(merged_reading_result)
            merged_glosses.append(merged_gloss_result)
            i += 2
        else:
            merged_readings.append(current_reading)
            merged_glosses.append(current_gloss)
            i += 1
    
    return merged_readings, merged_glosses

def sanitize_line(line, preserve_furigana=False):
    """
    Remove or normalize characters that tokenizers struggle with.
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

def remove_speaker_labels(text):
    """
    Remove speaker labels in parentheses like （キャラ名）, （はかせ）, or （narrator）
    but preserve furigana annotations that immediately follow kanji like 漢字(reading).
    Returns text with speaker labels removed.
    """
    result = []
    i = 0
    while i < len(text):
        # Look for opening parenthesis (ASCII or full-width)
        if text[i] in '（(':
            # Find the closing parenthesis
            if text[i] == '（':
                closing = '）'
            else:
                closing = ')'
            
            end = text.find(closing, i + 1)
            if end != -1:
                # Check if this parenthesis is preceded by kanji (furigana case)
                if i > 0 and 0x4E00 <= ord(text[i-1]) <= 0x9FFF:
                    # This is furigana - keep it
                    result.append(text[i:end+1])
                    i = end + 1
                else:
                    # This is a speaker label - skip it
                    i = end + 1
            else:
                # Unclosed parenthesis - keep it
                result.append(text[i])
                i += 1
        else:
            result.append(text[i])
            i += 1
    
    return ''.join(result)

def process_line(line):
    """
    Tokenize a single subtitle line using Yomitan API and return two lists:
        readings: list of kana readings (with kanji converted to hiragana)
        glosses:  list of English gloss strings
    """
    # First, extract any furigana annotations from the original text
    furigana_map = extract_furigana(line)
    
    # Remove speaker labels but preserve structure
    tokenization_text = remove_speaker_labels(line)
    
    # Remove furigana annotation parentheses (reading) since we have them in the map
    # Keep only (reading) that are NOT preceded by kanji (these are not furigana)
    # Actually, remove all (...) that are kana-only since they're furigana we already have
    tokenization_text = re.sub(r'[\(（][ぁ-ん゙ァ-ヴーa-zA-Z]*[\)）]', '', tokenization_text)
    
    tokenization_text = re.sub(r'[\[\]【】]', '', tokenization_text)  # Remove brackets
    tokenization_text = re.sub(r'[♪♫♬]', '', tokenization_text)  # Remove music notes
    tokenization_text = re.sub(r'　', ' ', tokenization_text)  # Full-width space to regular
    
    if not tokenization_text.strip():
        return [], []
    
    try:
        # Use Yomitan's tokenizer on text with furigana intact
        response = requests.post(
            f"{YOMITAN_API_URL}/tokenize",
            json={"text": tokenization_text, "scanLength": 30},
            timeout=YOMITAN_API_TIMEOUT
        )
        
        if response.status_code != 200:
            return [], []
        
        tokens_data = response.json()
        if not isinstance(tokens_data, list) or not tokens_data:
            return [], []
        
        readings = []
        glosses = []
        
        # Process tokenizer response: [{id, source, content: [[{text, reading, headwords}...]]}]
        for token_item in tokens_data:
            if not isinstance(token_item, dict):
                continue
            
            # Navigate the nested structure: content is [[{text, reading, ...}...]]
            content = token_item.get('content', [])
            if not isinstance(content, list):
                continue
            
            for group in content:
                if not isinstance(group, list):
                    continue
                
                # Each group represents alternative tokenizations of the same span
                # Only use the first (best) tokenization
                if len(group) == 0:
                    continue
                    
                token = group[0]  # Take the best match only
                if not isinstance(token, dict):
                    continue
                
                surface = token.get('text', '')
                reading = token.get('reading', '')
                headword_term = ''  # Initialize for all code paths
                
                # Skip empty tokens or pure punctuation
                if not surface:
                    continue
                
                # Skip pure punctuation/symbols (not speakers or furigana)
                # NOTE: Preserve ～ and ー (prolonged sound marks) so they don't disappear
                if re.fullmatch(r'[\s、。！？\\…\-_・]+', surface):
                    continue
                
                # Extract headword early so it's available for all priority paths
                headwords = token.get('headwords', [])
                headword_reading = ''
                if headwords and isinstance(headwords, list) and len(headwords) > 0:
                    hw_group = headwords[0]
                    if isinstance(hw_group, list) and len(hw_group) > 0:
                        headword = hw_group[0]
                        if isinstance(headword, dict):
                            headword_term = headword.get('term', '')
                            headword_reading = headword.get('reading', '')
                
                # Priority 1: If surface form is pure katakana, preserve it as-is
                if is_katakana(surface):
                    display_reading = surface
                # Priority 2: Check if we have furigana annotation for this surface form
                elif surface in furigana_map:
                    display_reading = furigana_map[surface]
                else:
                    # Priority 3: Get the best reading from tokenizer
                    # Prefer headword reading for dictionary-matched compounds
                    # headword_reading and headword_term already extracted above
                    
                    # Priority: headword reading > token reading > surface form
                    if headword_reading:
                        display_reading = headword_reading
                    elif reading:
                        display_reading = reading
                    else:
                        # No reading found - use surface, which may have kanji for unknown words
                        display_reading = surface
                
                # Determine which term to use for gloss lookup
                # Use headword when it's fuller/more complete than surface form
                gloss_term = surface
                
                if headword_term:
                    # Check if surface is a short/incomplete form of the headword
                    # Examples: "ページ" surface with "ページ番号" headword
                    #           or single kanji "落" with "落ち着く" headword
                    is_kanji_fragment = len(surface) == 1 and ord(surface[0]) > 0x4E00
                    is_shorter_than_headword = len(surface) < len(headword_term) and headword_term.startswith(surface)
                    
                    if is_kanji_fragment or is_shorter_than_headword:
                        gloss_term = headword_term
                        # Also update display_reading to show the full headword when using it for gloss
                        if headword_reading:
                            display_reading = headword_reading
                        else:
                            display_reading = headword_term
                
                readings.append(display_reading)
                gloss = get_gloss(gloss_term)
                glosses.append(gloss)
        
        # Merge missplitted phrases back together (in case Yomitan missplits some)
        readings, glosses = merge_morphemes(readings, glosses)
        
        # Merge consecutive katakana tokens (sound effects are often split)
        readings, glosses = merge_continuous_katakana(readings, glosses)
        
        # Smart post-processing to merge tokens that shouldn't be split
        readings, glosses = smart_merge_tokens(readings, glosses)
        
        return readings, glosses
    
    except (Timeout, ConnectTimeout, ReadTimeout) as e:
        print(f"[Warning] Timeout tokenizing line: '{line}'", file=sys.stderr)
        return [], []
    except Exception as e:
        print(f"[Warning] Error tokenizing line: '{line}' - {e}", file=sys.stderr)
        return [], []

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

                # Small delay to avoid hitting Yomitan API too hard
                time.sleep(0.05)

            # Blank line between subtitle entries
            out.write("\n")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python srt_gloss.py <input.srt> <output.md>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])