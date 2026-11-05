#!/usr/bin/env python3
"""
Extract subtitle text from an SRT file and remove parenthesized text.

Usage:
	python3 srt_script.py <input_srt> [output_txt]

If output_txt is omitted, the output is written beside the input file with
the same name and a .txt extension.
"""

import argparse
import re
from pathlib import Path


PARENTHESIZED_TEXT = re.compile(r"\([^()]*\)|（[^（）]*）")


def extract_subtitle_text(srt_text: str) -> list[str]:
	"""Return cleaned subtitle text, with one subtitle entry per line."""
	subtitles = []

	for block in re.split(r"\r?\n\s*\r?\n", srt_text.strip()):
		lines = block.splitlines()
		if len(lines) < 3 or not lines[0].strip().isdigit():
			continue

		subtitle_text = " ".join(line.strip() for line in lines[2:])
		subtitle_text = PARENTHESIZED_TEXT.sub("", subtitle_text)
		subtitle_text = re.sub(r"\s+", " ", subtitle_text).strip()

		if subtitle_text:
			subtitles.append(subtitle_text)

	return subtitles


def main() -> None:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("input_srt", type=Path)
	parser.add_argument("output_txt", type=Path, nargs="?")
	args = parser.parse_args()

	output_path = args.output_txt or args.input_srt.with_suffix(".txt")
	srt_text = args.input_srt.read_text(encoding="utf-8-sig")
	subtitles = extract_subtitle_text(srt_text)
	output_path.write_text("\n".join(subtitles) + ("\n" if subtitles else ""), encoding="utf-8")

	print(f"Extracted {len(subtitles)} subtitles to {output_path}")


if __name__ == "__main__":
	main()
