#!/usr/bin/env python3
"""
Split FLAC audio files according to SRT subtitle timings.

Uses:
    - pysrt: Clean SRT parsing
    - ffmpeg-python: Direct ffmpeg control for fast audio slicing
    - ffmpeg: Backend audio engine

Usage:
    python srt_splitter.py <audio.flac> <subtitles.srt> [srt_indexes]
    
Examples:
    # Split all subtitles
    python srt_splitter.py episode.flac episode.srt
    
    # Split specific subtitle indexes (comma-separated or ranges)
    python srt_splitter.py episode.flac episode.srt 1,2,3
    python srt_splitter.py episode.flac episode.srt 1-10
    python srt_splitter.py episode.flac episode.srt 1,5-7,10
    
    # With output directory and format
    python srt_splitter.py episode.flac episode.srt 1-5 -o ./splits -f mp3
"""

-1

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Dict

try:
    import pysrt
    import ffmpeg
except ImportError as e:
    print(f"Error: Missing required package: {e}")
    print("Install with: pip install pysrt ffmpeg-python")
    sys.exit(1)


def load_subtitles(srt_path: Path) -> List[Dict]:
    """
    Load SRT file using pysrt and convert to dict format.
    
    Args:
        srt_path: Path to SRT file
        
    Returns:
        List of dicts with keys: 'index', 'start_ms', 'end_ms', 'text'
    """
    try:
        subs = pysrt.open(str(srt_path))
    except Exception as e:
        raise FileNotFoundError(f"Failed to load SRT file: {e}")
    
    subtitles = []
    for sub in subs:
        # pysrt provides SubRipTime objects; convert to milliseconds
        start_ms = (
            sub.start.hours * 3600000 +
            sub.start.minutes * 60000 +
            sub.start.seconds * 1000 +
            sub.start.milliseconds
        )
        end_ms = (
            sub.end.hours * 3600000 +
            sub.end.minutes * 60000 +
            sub.end.seconds * 1000 +
            sub.end.milliseconds
        )
        
        subtitles.append({
            'index': sub.index,
            'start_ms': start_ms,
            'end_ms': end_ms,
            'text': sub.text
        })
    
    return subtitles


def parse_index_spec(spec: str, max_index: int) -> List[int]:
    """
    Parse index specification like "1,2,3" or "1-5" or "1,3-5,7".
    
    Args:
        spec: Index specification string
        max_index: Maximum valid index
        
    Returns:
        Sorted list of unique indexes
    """
    indexes = set()
    
    # Split by comma
    parts = spec.split(',')
    
    for part in parts:
        part = part.strip()
        
        if '-' in part:
            # Range specification
            try:
                start, end = part.split('-')
                start = int(start.strip())
                end = int(end.strip())
                
                if start > end:
                    start, end = end, start
                
                for i in range(start, end + 1):
                    if 1 <= i <= max_index:
                        indexes.add(i)
            except ValueError:
                print(f"Warning: Invalid range specification: {part}")
        else:
            # Single index
            try:
                idx = int(part)
                if 1 <= idx <= max_index:
                    indexes.add(idx)
            except ValueError:
                print(f"Warning: Invalid index: {part}")
    
    return sorted(list(indexes))


def get_audio_duration_ms(audio_path: Path) -> float:
    """Get audio duration in milliseconds using ffprobe."""
    try:
        probe = ffmpeg.probe(str(audio_path))
        duration_sec = float(probe['format']['duration'])
        return duration_sec * 1000
    except Exception as e:
        raise RuntimeError(f"Failed to get audio duration: {e}")


def extract_audio_segment(
    audio_path: Path,
    output_path: Path,
    start_ms: int,
    end_ms: int,
    output_format: str = 'flac'
) -> None:
    """
    Extract a segment from audio file using ffmpeg-python.
    
    Args:
        audio_path: Path to source audio file
        output_path: Path to output file
        start_ms: Start time in milliseconds
        end_ms: End time in milliseconds
        output_format: Output format (flac, mp3, wav, etc.)
    """
    start_sec = start_ms / 1000.0
    duration_sec = (end_ms - start_ms) / 1000.0 + 0.2
    
    try:
        (
            ffmpeg
            .input(str(audio_path), ss=start_sec, t=duration_sec)
            .output(str(output_path), acodec='copy', loglevel='error')
            .overwrite_output()
            .run(quiet=True, capture_stdout=True)
        )
    except ffmpeg.Error as e:
        raise RuntimeError(f"ffmpeg error: {e.stderr.decode() if e.stderr else str(e)}")


def sanitize_filename(text: str, max_length: int = 30) -> str:
    """
    Sanitize subtitle text for use in filename.
    
    Args:
        text: Subtitle text
        max_length: Maximum length of filename component
        
    Returns:
        Cleaned filename component
    """
    # Remove newlines and extra spaces
    text = text.replace('\n', ' ').strip()
    # Remove special characters (keep alphanumeric, spaces, hyphens)
    text = re.sub(r'[^\w\s\-]', '', text, flags=re.UNICODE)
    # Truncate to max length
    return text[:max_length]


def split_audio(
    audio_path: Path,
    subtitles: List[Dict],
    indexes: Optional[List[int]] = None,
    output_dir: Optional[Path] = None,
    output_format: str = 'flac',
    merge_threshold_ms: int = 0,
) -> None:
    """
    Split audio file according to subtitle timings using ffmpeg.
    
    Args:
        audio_path: Path to audio file
        subtitles: List of subtitle dicts from load_subtitles
        indexes: List of subtitle indexes to extract. If None, extract all.
        output_dir: Output directory for split files. Defaults to audio directory.
        output_format: Output format (flac, mp3, wav, etc.)
        merge_threshold_ms: Merge consecutive subtitles within this ms threshold (0 = no merge)
    """
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    
    if output_dir is None:
        output_dir = audio_path.parent
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Get audio duration
    print(f"📁 Loading: {audio_path.name}")
    try:
        audio_duration_ms = get_audio_duration_ms(audio_path)
        print(f"⏱️  Duration: {audio_duration_ms / 1000:.2f}s")
    except RuntimeError as e:
        print(f"⚠️  Warning: {e}")
        audio_duration_ms = float('inf')
    
    # Determine which indexes to extract
    if indexes is None:
        indexes = [sub['index'] for sub in subtitles]
    
    # Create mapping of index to subtitle
    sub_map = {sub['index']: sub for sub in subtitles}
    
    # Filter to only requested indexes
    items_to_export = [sub_map[idx] for idx in indexes if idx in sub_map]
    
    if not items_to_export:
        print("❌ No subtitles found for specified indexes")
        return
    
    # Optional: merge small gaps between consecutive subtitles
    if merge_threshold_ms > 0:
        items_to_export = merge_close_subtitles(items_to_export, merge_threshold_ms)
    
    # Split and save
    base_name = audio_path.stem
    total = len(items_to_export)
    
    for count, sub in enumerate(items_to_export, 1):
        start_ms = sub['start_ms']
        end_ms = sub['end_ms']
        idx = sub['index']
        
        # Validate bounds
        if start_ms >= audio_duration_ms:
            print(f"⚠️  [{count}/{total}] Subtitle {idx} starts after audio ends, skipping")
            continue
        
        end_ms = min(end_ms, audio_duration_ms)
        duration_sec = (end_ms - start_ms) / 1000
        
        # Create output filename
        text_part = sanitize_filename(sub['text'])
        if text_part:
            output_name = f"{base_name}_{idx:03d}_{text_part}.{output_format}"
        else:
            output_name = f"{base_name}_{idx:03d}.{output_format}"
        
        output_path = output_dir / output_name
        
        # Extract segment
        print(f"✂️  [{count}/{total}] {output_name:60s} ({duration_sec:6.2f}s)")
        
        try:
            extract_audio_segment(audio_path, output_path, start_ms, end_ms, output_format)
        except RuntimeError as e:
            print(f"    ❌ Error: {e}")
    
    print(f"\n✅ Done! Split {len(items_to_export)} audio segments to {output_dir.name}/")


def merge_close_subtitles(
    subtitles: List[Dict],
    threshold_ms: int
) -> List[Dict]:
    """
    Merge consecutive subtitles with gaps smaller than threshold.
    
    Useful for handling very short lines that should be combined.
    
    Args:
        subtitles: List of subtitle dicts
        threshold_ms: Maximum gap (in ms) to merge
        
    Returns:
        List of merged subtitle dicts
    """
    if not subtitles:
        return subtitles
    
    merged = [subtitles[0].copy()]
    
    for current in subtitles[1:]:
        last = merged[-1]
        gap = current['start_ms'] - last['end_ms']
        
        if gap <= threshold_ms:
            # Merge: extend the last subtitle
            last['end_ms'] = current['end_ms']
            last['text'] += '\n' + current['text']
        else:
            # Keep separate
            merged.append(current.copy())
    
    return merged


def main():
    parser = argparse.ArgumentParser(
        description='Split audio files according to SRT subtitle timings using ffmpeg-python.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # Split all subtitles
  python srt_splitter.py episode.flac episode.srt
  
  # Split specific indexes
  python srt_splitter.py episode.flac episode.srt 1,2,3
  python srt_splitter.py episode.flac episode.srt 1-10
  python srt_splitter.py episode.flac episode.srt 1,5-7,10
  
  # With output directory and format
  python srt_splitter.py episode.flac episode.srt -o ./splits -f mp3
  
  # Merge subtitles within 500ms gaps
  python srt_splitter.py episode.flac episode.srt --merge 500
        """
    )
    parser.add_argument(
        'audio',
        type=Path,
        help='Path to audio file'
    )
    parser.add_argument(
        'srt',
        type=Path,
        help='Path to SRT subtitle file'
    )
    parser.add_argument(
        'indexes',
        nargs='?',
        help='SRT indexes to extract (e.g., "1,2,3" or "1-10" or "1,5-7")'
    )
    parser.add_argument(
        '-o', '--output',
        type=Path,
        default=None,
        help='Output directory (default: same as audio file)'
    )
    parser.add_argument(
        '-f', '--format',
        default='flac',
        help='Output format (default: flac)'
    )
    parser.add_argument(
        '--merge',
        type=int,
        default=0,
        metavar='MS',
        help='Merge subtitles with gaps <= MS milliseconds (default: 0 = no merge)'
    )
    
    args = parser.parse_args()
    
    # Load SRT file
    print(f"📖 Parsing: {args.srt.name}")
    try:
        subtitles = load_subtitles(args.srt)
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    
    if not subtitles:
        print("❌ No subtitles found in SRT file")
        sys.exit(1)
    
    print(f"✓ Found {len(subtitles)} subtitles")
    
    # Determine indexes to extract
    if args.indexes:
        indexes = parse_index_spec(args.indexes, max(sub['index'] for sub in subtitles))
        print(f"🎯 Extracting: {indexes}")
    else:
        indexes = None
        print("🎯 Extracting all subtitles")
    
    if args.merge > 0:
        print(f"🔗 Merging subtitles within {args.merge}ms gaps")
    
    # Split audio
    try:
        split_audio(
            args.audio,
            subtitles,
            indexes=indexes,
            output_dir=args.output,
            output_format=args.format,
            merge_threshold_ms=args.merge
        )
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
