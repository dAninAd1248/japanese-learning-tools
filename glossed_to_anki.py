#!/usr/bin/env python3
"""
Convert glossed SRT subtitle files to Anki CSV flashcards.

Each SRT index becomes one card.
Front: The reading (first row of table, with | and spaces removed)
Back: HTML table with each word and its gloss
"""

# TODO add audio column (empty)

import re
import csv
import sys
from pathlib import Path


def parse_gloss_file(filepath):
    """
    Parse a glossed markdown file and extract flashcard data.
    One card per SRT index (merges all lines within an index).
    
    Returns:
        list of dict: Each dict has 'srt_index', 'reading', 'back_html', 'time'
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    cards = []
    
    # Split by SRT index headers (### 1: ..., ### 2: ..., etc)
    sections = re.split(r'### (\d+):', content)
    
    # sections[0] is before first header (usually empty), then alternates between index and content
    for i in range(1, len(sections), 2):
        srt_index = sections[i].strip()
        section_content = sections[i + 1] if i + 1 < len(sections) else ""
        
        # Extract timestamp and rest of content
        lines = section_content.strip().split('\n')
        if not lines:
            continue
        
        # First line usually has timestamp like "00:02 – 00:03"
        timestamp_line = lines[0] if lines else ""
        
        # Find the markdown table(s) in this section
        table_data = extract_tables(section_content)
        
        if table_data:
            # Merge all tables for this SRT index into one card
            card = merge_tables_to_card(srt_index, table_data, timestamp_line)
            if card:
                cards.append(card)
    
    return cards


def extract_tables(text):
    """
    Extract markdown tables from text.
    
    Returns:
        list of tables, where each table is list of rows (each row is list of cells)
    """
    lines = text.split('\n')
    tables = []
    current_table = []
    in_table = False
    
    for line in lines:
        # Table rows start with |
        if line.strip().startswith('|'):
            in_table = True
            # Parse the row
            cells = [cell.strip() for cell in line.split('|')[1:-1]]  # Remove first/last empty
            current_table.append(cells)
        else:
            # End of table
            if in_table and current_table:
                tables.append(current_table)
                current_table = []
            in_table = False
    
    # Don't forget last table
    if current_table:
        tables.append(current_table)
    
    return tables


def merge_tables_to_card(srt_index, tables, timestamp_line):
    """
    Merge all tables for an SRT index into a single flashcard.
    
    Args:
        srt_index: The SRT index number
        tables: List of tables for this index
        timestamp_line: The timestamp/context line
    
    Returns:
        dict with 'srt_index', 'reading', 'back_html', 'time' or None if invalid
    """
    if not tables:
        return None
    
    # Extract time from timestamp line (e.g., "00:02 – 00:03")
    time_match = re.search(r'(\d{2}:\d{2}\s*–\s*\d{2}:\d{2})', timestamp_line)
    time_str = time_match.group(1) if time_match else ""
    
    # Merge all readings and glosses, grouped by table (line)
    line_readings = []   # list of lists, one per table/line
    line_glosses = []    # parallel list of lists
    
    for table in tables:
        if len(table) < 3:
            continue
        
        reading_row = table[0]
        gloss_row = table[-1]
        
        # Check if second row is separator (all dashes)
        separator_row = table[1]
        is_separator = all(re.match(r'^-+$', cell.strip()) or cell.strip() == '' for cell in separator_row)
        
        if not is_separator:
            continue
        
        # Collect readings and glosses for this line
        line_readings_group = []
        line_glosses_group = []
        for reading, gloss in zip(reading_row, gloss_row):
            reading = reading.strip()
            gloss = gloss.strip()
            if reading and gloss:  # Only add non-empty cells
                line_readings_group.append(reading)
                line_glosses_group.append(gloss)
        
        if line_readings_group:
            line_readings.append(line_readings_group)
            line_glosses.append(line_glosses_group)
    
    if not line_readings:
        return None
    
    # Front: join readings within each line, then lines with newline
    front_lines = [''.join(group).replace('|', '').replace(' ', '') for group in line_readings]
    front = '<br>'.join(front_lines)
    
    # Back: HTML table with reading | gloss pairs
    back_html = "<table style='border-collapse: collapse;'>"
    for line_group, gloss_group in zip(line_readings, line_glosses):
        for reading, gloss in zip(line_group, gloss_group):
            back_html += f"<tr><td style='border: 1px solid black; padding: 5px;'>{reading}</td>"
            back_html += f"<td style='border: 1px solid black; padding: 5px;'>{gloss}</td></tr>"
    back_html += "</table>"
    
    return {
        'srt_index': srt_index,
        'reading': front,
        'back_html': back_html,
        'time': time_str,
        'tags': 'japanese srt-gloss'
    }


def create_card(srt_index, table, timestamp_line):
    """
    Create a flashcard from a table.
    
    Args:
        srt_index: The SRT index number
        table: List of rows, where each row is list of cells
        timestamp_line: The timestamp/context line
    
    Returns:
        dict with 'srt_index', 'reading', 'back_html' or None if invalid
    """
    if len(table) < 3:  # Need at least reading row, separator row, gloss row
        return None
    
    # First row is the reading (could have multiple words)
    reading_row = table[0]
    
    # Last row is the gloss/meaning
    gloss_row = table[-1]
    
    # Check if second row is separator (all dashes)
    separator_row = table[1]
    is_separator = all(re.match(r'^-+$', cell.strip()) or cell.strip() == '' for cell in separator_row)
    
    if not is_separator:
        # If not a proper table, skip
        return None
    
    # Front: combine reading cells, remove pipes and spaces
    front = ''.join(reading_row).replace('|', '').replace(' ', '')
    
    if not front:
        return None
    
    # Back: HTML table with reading | gloss pairs
    back_html = "<table style='border-collapse: collapse;'>"
    for reading, gloss in zip(reading_row, gloss_row):
        reading = reading.strip()
        gloss = gloss.strip()
        if reading and gloss:  # Only add non-empty cells
            back_html += f"<tr><td style='border: 1px solid black; padding: 5px;'>{reading}</td>"
            back_html += f"<td style='border: 1px solid black; padding: 5px;'>{gloss}</td></tr>"
    back_html += "</table>"
    
    return {
        'srt_index': srt_index,
        'reading': front,
        'back_html': back_html,
        'tags': 'japanese srt-gloss'
    }


def save_to_anki_csv(cards, output_filepath):
    """
    Save cards to Anki CSV format with metadata columns.
    
    Columns: front, back, srt_index, time, tags
    """
    with open(output_filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        
        for card in cards:
            # Anki format: front, back, index, time, tags
            writer.writerow([
                card['reading'],
                card['back_html'],
                card['srt_index'],
                card['time'],
                card['tags']
            ])


def main():
    if len(sys.argv) < 2:
        print("Usage: glossed_to_anki.py <input_file.md> [output_file.csv]")
        sys.exit(1)
    
    input_file = Path(sys.argv[1])
    
    if not input_file.exists():
        print(f"Error: File '{input_file}' not found")
        sys.exit(1)
    
    # Default output filename
    output_file = Path(sys.argv[2]) if len(sys.argv) > 2 else input_file.with_suffix('.csv')
    
    print(f"Parsing '{input_file}'...")
    cards = parse_gloss_file(input_file)
    
    print(f"Found {len(cards)} cards")
    
    if cards:
        save_to_anki_csv(cards, output_file)
        print(f"Saved to '{output_file}'")
        
        # Print first few cards as preview
        print("\nFirst 3 cards:")
        for card in cards[:3]:
            print(f"  Index {card['srt_index']}: {card['reading']}")
    else:
        print("No cards found!")


if __name__ == '__main__':
    main()
