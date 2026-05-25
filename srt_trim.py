#!/usr/bin/env python3
"""
Trim an SRT file to only include a range of subtitle indices.

Usage:
    python srt_trim.py <input_srt> <start_index> <end_index> [output_srt]
    
Example:
    python srt_trim.py input.srt 1 10 output.srt
    python srt_trim.py input.srt 5 15  # outputs to input_trimmed.srt
"""

import sys
import re
from pathlib import Path


def parse_srt(file_path):
    """
    Parse an SRT file and return a list of subtitle entries.
    
    Each entry is a dict with keys: 'index', 'timecode', 'text'
    """
    entries = []
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read().strip()
    
    # Split by double newlines to separate entries
    blocks = content.split('\n\n')
    
    for block in blocks:
        if not block.strip():
            continue
            
        lines = block.split('\n')
        if len(lines) < 3:
            continue
        
        try:
            index = int(lines[0].strip())
            timecode = lines[1].strip()
            text = '\n'.join(lines[2:])
            
            entries.append({
                'index': index,
                'timecode': timecode,
                'text': text
            })
        except (ValueError, IndexError):
            # Skip malformed entries
            continue
    
    return entries


def filter_entries(entries, start_index, end_index):
    """Filter entries to only include those within the index range (inclusive)."""
    return [e for e in entries if start_index <= e['index'] <= end_index]


def write_srt(entries, output_path):
    """Write entries to an SRT file."""
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, entry in enumerate(entries):
            f.write(f"{entry['index']}\n")
            f.write(f"{entry['timecode']}\n")
            f.write(f"{entry['text']}\n")
            
            # Add blank line between entries (except after the last one)
            if i < len(entries) - 1:
                f.write('\n')


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    
    input_srt = sys.argv[1]
    try:
        start_index = int(sys.argv[2])
        end_index = int(sys.argv[3])
    except ValueError:
        print("Error: start_index and end_index must be integers")
        sys.exit(1)
    
    # Determine output path
    if len(sys.argv) >= 5:
        output_srt = sys.argv[4]
    else:
        input_path = Path(input_srt)
        output_srt = input_path.parent / f"{input_path.stem}_trimmed.srt"
    
    # Parse and filter
    try:
        entries = parse_srt(input_srt)
        filtered = filter_entries(entries, start_index, end_index)
        
        if not filtered:
            print(f"No entries found in range {start_index}-{end_index}")
            sys.exit(1)
        
        write_srt(filtered, output_srt)
        print(f"✓ Trimmed {len(filtered)} entries to {output_srt}")
        
    except FileNotFoundError:
        print(f"Error: Input file not found: {input_srt}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
