#!/usr/bin/env python3
"""
Anki Audio Management via AnkiConnect

Integrates with Anki's AnkiConnect plugin to:
- Extract SRT-indexed audio segments from full audio files
- Upload audio to Anki's media collection
- Link audio files to existing notes in a deck

Requires:
- Anki running with AnkiConnect plugin installed
- srt_splitter.py for audio extraction utilities
- ffmpeg-python for audio processing
- requests for HTTP communication with AnkiConnect
"""

import sys
import base64
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

try:
    import pysrt
except ImportError:
    pysrt = None

try:
    import ffmpeg
except ImportError:
    ffmpeg = None

# Import utilities from srt_splitter
from srt_splitter import get_audio_duration_ms, load_subtitles


# ============================================================================
# AnkiConnect API Communication
# ============================================================================

def anki_invoke(action: str, **params) -> Dict:
    """
    Invoke AnkiConnect action via HTTP API.
    
    Args:
        action: AnkiConnect action name (e.g., 'findNotes', 'storeMediaFile')
        **params: Action parameters
        
    Returns:
        dict: Response from AnkiConnect
        
    Raises:
        RuntimeError: If Anki is not running or action fails
    """
    if requests is None:
        raise RuntimeError("requests library required. Install with: pip install requests")
    
    try:
        response = requests.post(
            "http://localhost:8765",
            json={
                "action": action,
                "version": 6,
                "params": params
            },
            timeout=5
        )
        result = response.json()
        
        if result.get("error"):
            raise RuntimeError(f"AnkiConnect error: {result['error']}")
        
        return result.get("result")
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "Cannot connect to AnkiConnect. Make sure:\n"
            "  1. Anki is running\n"
            "  2. AnkiConnect plugin is installed\n"
            "  3. AnkiConnect is listening on http://localhost:8765"
        )
    except Exception as e:
        raise RuntimeError(f"AnkiConnect request failed: {e}")


def get_deck_notes(deck_name: str) -> List[int]:
    """
    Get all note IDs from a specific Anki deck.
    
    Args:
        deck_name: Name of the Anki deck
        
    Returns:
        List of note IDs
    """
    try:
        return anki_invoke("findNotes", query=f'deck:"{deck_name}"')
    except RuntimeError as e:
        print(f"Error querying deck '{deck_name}': {e}")
        return []


def get_note_fields(note_id: int) -> Dict[str, str]:
    """
    Get all fields from a note.
    
    Args:
        note_id: The note ID
        
    Returns:
        dict: Field names mapped to values (strings)
    """
    info = anki_invoke("notesInfo", notes=[note_id])
    if info and len(info) > 0:
        raw_fields = info[0].get("fields", {})
        # AnkiConnect returns fields as {"fieldName": {"value": "...", "order": N}, ...}
        # Extract just the values
        fields = {}
        for field_name, field_data in raw_fields.items():
            if isinstance(field_data, dict) and "value" in field_data:
                fields[field_name] = field_data["value"]
            else:
                fields[field_name] = str(field_data)
        return fields
    return {}


# ============================================================================
# Audio Extraction & Upload
# ============================================================================

def extract_audio_segment(
    audio_path: Path,
    start_ms: int,
    end_ms: int,
    output_path: Path
) -> None:
    """
    Extract audio segment using ffmpeg.
    
    Args:
        audio_path: Path to source audio file
        start_ms: Start time in milliseconds
        end_ms: End time in milliseconds
        output_path: Path for output file (will be MP3)
        
    Raises:
        RuntimeError: If ffmpeg fails
    """
    if ffmpeg is None:
        raise RuntimeError(
            "ffmpeg-python required. Install with: pip install ffmpeg-python"
        )
    
    start_sec = start_ms / 1000.0
    duration_sec = (end_ms - start_ms) / 1000.0 + 0.3  # Add slight padding
    
    try:
        (
            ffmpeg
            .input(str(audio_path), ss=start_sec, t=duration_sec)
            .output(str(output_path), acodec='libmp3lame', audio_bitrate='128k', loglevel='error')
            .overwrite_output()
            .run(quiet=True, capture_stdout=True)
        )
    except ffmpeg.Error as e:
        raise RuntimeError(f"ffmpeg error: {e.stderr.decode() if e.stderr else str(e)}")


def upload_audio_to_anki(audio_path: Path, filename: Optional[str] = None) -> str:
    """
    Upload audio file to Anki's media collection.
    
    Args:
        audio_path: Path to audio file
        filename: Optional custom filename (defaults to audio_path.name)
        
    Returns:
        str: Filename as stored in Anki
        
    Raises:
        RuntimeError: If upload fails
    """
    if not audio_path.exists():
        raise RuntimeError(f"Audio file not found: {audio_path}")
    
    filename = filename or audio_path.name
    
    # Read and encode file
    with open(audio_path, 'rb') as f:
        data = base64.b64encode(f.read()).decode('utf-8')
    
    # Upload to Anki
    result = anki_invoke("storeMediaFile", filename=filename, data=data)
    
    return result if isinstance(result, str) else filename


def add_audio_to_note(note_id: int, audio_filename: str, field_name: str = "Audio") -> bool:
    """
    Add audio reference to a note's field.
    
    Args:
        note_id: The note ID
        audio_filename: Filename of audio (as stored in Anki)
        field_name: Field name to update (default: "Audio")
        
    Returns:
        bool: True if successful
        
    Raises:
        RuntimeError: If update fails
    """
    # Get current note state
    fields = get_note_fields(note_id)
    
    if field_name not in fields:
        raise RuntimeError(f"Note {note_id} does not have field '{field_name}'")
    
    # Add sound reference
    audio_reference = f"[sound:{audio_filename}]"
    
    # Update the field
    anki_invoke("updateNoteFields", note={
        "id": note_id,
        "fields": {
            field_name: audio_reference
        }
    })
    
    return True


# ============================================================================
# Main Deck Audio Attachment
# ============================================================================

def add_audio_to_deck(
    deck_name: str,
    audio_path: Path,
    srt_path: Path,
    audio_field: str = "Audio",
    srt_index_field: str = "SRT Index",
    verbose: bool = True
) -> Dict[int, bool]:
    """
    Add audio to all notes in a deck based on their SRT indexes.
    
    This function:
    1. Queries the deck for all notes
    2. Extracts SRT indexes from each note
    3. Uses srt_splitter logic to extract audio segments
    4. Uploads audio to Anki and links to note
    
    Args:
        deck_name: Name of Anki deck
        audio_path: Path to the full audio file (e.g., episode.flac)
        srt_path: Path to the SRT subtitle file
        audio_field: Name of the audio field in notes (default: "Audio")
        srt_index_field: Name of the SRT index field (default: "SRT Index")
        verbose: Print progress messages
        
    Returns:
        dict: note_id -> success (bool) mapping
        
    Example:
        results = add_audio_to_deck(
            deck_name="日常",
            audio_path=Path("../日常1.flac"),
            srt_path=Path("[BD Timed] 日常 01.srt")
        )
    """
    if requests is None or pysrt is None or ffmpeg is None:
        raise RuntimeError(
            "Missing required libraries. Install with:\n"
            "  pip install requests pysrt ffmpeg-python"
        )
    
    audio_path = Path(audio_path)
    srt_path = Path(srt_path)
    
    if not audio_path.exists():
        raise RuntimeError(f"Audio file not found: {audio_path}")
    if not srt_path.exists():
        raise RuntimeError(f"SRT file not found: {srt_path}")
    
    if verbose:
        print(f"\n{'='*60}")
        print(f"Adding audio to deck: {deck_name}")
        print(f"Audio: {audio_path}")
        print(f"SRT: {srt_path}")
        print(f"{'='*60}\n")
    
    # Get all notes in deck
    note_ids = get_deck_notes(deck_name)
    if not note_ids:
        print(f"No notes found in deck '{deck_name}'")
        return {}
    
    if verbose:
        print(f"Found {len(note_ids)} notes in deck\n")
    
    # Collect SRT indexes and map to notes
    srt_indexes = set()
    note_index_map = {}  # note_id -> srt_index
    
    for note_id in note_ids:
        try:
            fields = get_note_fields(note_id)
            if srt_index_field in fields:
                srt_idx = fields[srt_index_field].strip()
                if srt_idx:
                    srt_indexes.add(int(srt_idx))
                    note_index_map[note_id] = int(srt_idx)
        except Exception as e:
            if verbose:
                print(f"Warning: Could not read SRT index from note {note_id}: {e}")
    
    if not srt_indexes:
        print(f"No SRT indexes found in deck notes")
        return {}
    
    if verbose:
        print(f"Extracted SRT indexes: {sorted(srt_indexes)}\n")
    
    # Load subtitles and extract timings
    try:
        subtitles = load_subtitles(srt_path)
    except Exception as e:
        raise RuntimeError(f"Failed to load SRT file: {e}")
    
    # Build timing map: srt_index -> (start_ms, end_ms)
    timings = {}
    for sub in subtitles:
        if sub['index'] in srt_indexes:
            timings[sub['index']] = (sub['start_ms'], sub['end_ms'])
    
    if verbose:
        print(f"Found timings for {len(timings)} subtitles\n")
    
    # Process each note
    results = {}
    temp_dir = Path(tempfile.gettempdir()) / "anki_audio_temp"
    temp_dir.mkdir(exist_ok=True)
    
    for count, (note_id, srt_idx) in enumerate(note_index_map.items(), 1):
        if srt_idx not in timings:
            if verbose:
                print(f"[{count}/{len(note_index_map)}] Skipping note {note_id}: No timing for SRT {srt_idx}")
            results[note_id] = False
            continue
        
        try:
            # Check if note already has audio
            current_fields = get_note_fields(note_id)
            if audio_field in current_fields:
                current_audio = current_fields[audio_field].strip()
                if current_audio and "[sound:" in current_audio:
                    if verbose:
                        print(f"[{count}/{len(note_index_map)}] Skipping note {note_id}: Already has audio")
                    results[note_id] = True  # Mark as success (already done)
                    continue
            
            start_ms, end_ms = timings[srt_idx]
            
            if verbose:
                print(f"[{count}/{len(note_index_map)}] Processing note {note_id} (SRT {srt_idx}): {start_ms/1000:.2f}s - {end_ms/1000:.2f}s")
            
            # Extract audio segment
            audio_temp_name = f"srt_{srt_idx:03d}_{note_id}.mp3"
            audio_temp_path = temp_dir / audio_temp_name
            
            if verbose:
                print(f"  └─ Extracting audio...", end=" ", flush=True)
            
            extract_audio_segment(audio_path, start_ms, end_ms, audio_temp_path)
            
            if verbose:
                print("✓")
            
            # Upload to Anki
            if verbose:
                print(f"  └─ Uploading to Anki...", end=" ", flush=True)
            
            anki_filename = upload_audio_to_anki(audio_temp_path, audio_temp_name)
            
            if verbose:
                print("✓")
            
            # Attach to note
            if verbose:
                print(f"  └─ Updating note field...", end=" ", flush=True)
            
            add_audio_to_note(note_id, anki_filename, audio_field)
            
            if verbose:
                print("✓")
            
            results[note_id] = True
            
            # Clean up temp file
            audio_temp_path.unlink(missing_ok=True)
            
        except Exception as e:
            if verbose:
                print(f"✗ Error: {e}")
            results[note_id] = False
    
    # Summary
    successful = sum(1 for v in results.values() if v)
    if verbose:
        print(f"\n{'='*60}")
        print(f"Complete! {successful}/{len(results)} notes updated with audio")
        print(f"{'='*60}\n")
    
    return results


# ============================================================================
# CLI Interface
# ============================================================================

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  anki_audio.py <deck_name> <audio_file> <srt_file>")
        print()
        print("Example:")
        print("  python anki_audio.py '日常' ../日常1.flac '[BD Timed] 日常 01.srt'")
        print()
        print("Optional arguments:")
        print("  --audio-field <name>    Field name for audio (default: 'Audio')")
        print("  --index-field <name>    Field name for SRT index (default: 'SRT Index')")
        sys.exit(1)
    
    deck_name = sys.argv[1]
    audio_file = Path(sys.argv[2])
    srt_file = Path(sys.argv[3])
    
    # Optional field overrides
    audio_field = "Audio"
    srt_index_field = "SRT Index"
    
    for i in range(4, len(sys.argv)):
        if sys.argv[i] == "--audio-field" and i + 1 < len(sys.argv):
            audio_field = sys.argv[i + 1]
        elif sys.argv[i] == "--index-field" and i + 1 < len(sys.argv):
            srt_index_field = sys.argv[i + 1]
    
    try:
        results = add_audio_to_deck(
            deck_name=deck_name,
            audio_path=audio_file,
            srt_path=srt_file,
            audio_field=audio_field,
            srt_index_field=srt_index_field
        )
        
        successful = sum(1 for v in results.values() if v)
        if successful == 0:
            sys.exit(1)
    
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
