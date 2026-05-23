"""
app/services/subtitle_parser.py
Parse .srt and .ass/.ssa subtitle files into structured segments.

SRT format:
  1
  00:00:01,234 --> 00:00:03,456
  Subtitle text here

ASS/SSA format:
  [Events]
  Dialogue: 0,0:00:01.23,0:00:03.45,Default,,0,0,0,,Subtitle text here

No external dependencies — pure Python parsing.
"""
import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class SubtitleSegment:
    index: int          # sequential number
    start_time: float   # seconds
    end_time: float     # seconds
    text: str           # cleaned subtitle text
    speaker_label: str = ""  # filled later by diarization


# ── Time parsers ──────────────────────────────────────────────

def _srt_time_to_seconds(time_str: str) -> float:
    """
    Convert SRT timestamp to seconds.
    Input:  "00:01:23,456"
    Output: 83.456
    """
    time_str = time_str.strip().replace(",", ".")
    parts = time_str.split(":")
    hours   = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def _ass_time_to_seconds(time_str: str) -> float:
    """
    Convert ASS timestamp to seconds.
    Input:  "0:01:23.45"
    Output: 83.45
    """
    time_str = time_str.strip()
    parts = time_str.split(":")
    hours   = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


# ── Text cleaners ─────────────────────────────────────────────

def _clean_srt_text(text: str) -> str:
    """
    Remove SRT HTML tags and formatting codes.
    <b>text</b> → text
    <i>text</i> → text
    {\\an8}text → text
    """
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Remove ASS override codes like {\an8} {\pos(100,200)}
    text = re.sub(r"\{[^}]+\}", "", text)
    # Normalize whitespace
    text = " ".join(text.split())
    return text.strip()


def _clean_ass_text(text: str) -> str:
    """
    Remove ASS/SSA formatting codes from dialogue text.
    Handles override blocks like {b1} and newline markers.
    """
    # Remove override blocks
    text = re.sub(r"\{[^}]*\}", "", text)
    # Replace hard/soft newlines
    text = text.replace("\\N", " ").replace("\\n", " ")
    # Normalize whitespace
    text = " ".join(text.split())
    return text.strip()


# ── SRT parser ────────────────────────────────────────────────

def parse_srt(content: str) -> List[SubtitleSegment]:
    """
    Parse SRT subtitle content into SubtitleSegment list.
    Handles malformed files gracefully.
    """
    segments = []

    # Split into blocks by blank lines
    blocks = re.split(r"\n\s*\n", content.strip())

    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue

        try:
            # Line 0: index number
            index = int(lines[0].strip())

            # Line 1: timestamp range
            time_match = re.match(
                r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})",
                lines[1].strip()
            )
            if not time_match:
                continue

            start = _srt_time_to_seconds(time_match.group(1))
            end   = _srt_time_to_seconds(time_match.group(2))

            # Lines 2+: subtitle text (can be multiple lines)
            raw_text = " ".join(lines[2:])
            text = _clean_srt_text(raw_text)

            if not text:
                continue

            segments.append(SubtitleSegment(
                index=index,
                start_time=round(start, 3),
                end_time=round(end, 3),
                text=text,
            ))

        except (ValueError, IndexError) as e:
            logger.debug(f"Skipping malformed SRT block: {e}")
            continue

    logger.info(f"Parsed {len(segments)} segments from SRT")
    return segments


# ── ASS/SSA parser ────────────────────────────────────────────

def parse_ass(content: str) -> List[SubtitleSegment]:
    """
    Parse ASS/SSA subtitle content into SubtitleSegment list.
    Only parses [Events] section Dialogue lines.
    Skips comment lines and non-dialogue events.
    """
    segments = []
    in_events = False
    format_fields = []
    index = 0

    for line in content.splitlines():
        line = line.strip()

        # Detect [Events] section
        if line.lower() == "[events]":
            in_events = True
            continue

        # Detect new section — exit events
        if line.startswith("[") and line.endswith("]") and in_events:
            break

        if not in_events:
            continue

        # Parse Format line to know column order
        if line.startswith("Format:"):
            format_fields = [f.strip().lower() for f in line[7:].split(",")]
            continue

        # Parse Dialogue lines only
        if not line.startswith("Dialogue:"):
            continue

        # Split into fields matching Format columns
        parts = line[9:].split(",", len(format_fields) - 1)
        if len(parts) < len(format_fields):
            continue

        try:
            field_map = dict(zip(format_fields, parts))

            start = _ass_time_to_seconds(field_map.get("start", "0:00:00.00"))
            end   = _ass_time_to_seconds(field_map.get("end",   "0:00:00.00"))
            raw_text = field_map.get("text", "")

            text = _clean_ass_text(raw_text)
            if not text:
                continue

            # Skip signs/styled subtitles that are not dialogue
            style = field_map.get("style", "").lower()
            if any(s in style for s in ["sign", "title", "credit", "note"]):
                continue

            index += 1
            segments.append(SubtitleSegment(
                index=index,
                start_time=round(start, 3),
                end_time=round(end, 3),
                text=text,
            ))

        except (ValueError, KeyError) as e:
            logger.debug(f"Skipping malformed ASS line: {e}")
            continue

    logger.info(f"Parsed {len(segments)} segments from ASS/SSA")
    return segments


# ── Main entry point ──────────────────────────────────────────

def parse_subtitle_file(file_path: str) -> List[SubtitleSegment]:
    """
    Parse a subtitle file (.srt or .ass/.ssa).
    Auto-detects format from extension.

    Returns list of SubtitleSegment sorted by start_time.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    # Try multiple encodings — Chinese subtitles often use GBK
    encodings = ["utf-8", "utf-8-sig", "gbk", "gb2312", "big5", "latin-1"]
    content = None

    for enc in encodings:
        try:
            content = path.read_text(encoding=enc)
            logger.debug(f"Read subtitle file with encoding: {enc}")
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if content is None:
        raise RuntimeError(f"Could not decode subtitle file: {file_path}")

    if suffix == ".srt":
        segments = parse_srt(content)
    elif suffix in (".ass", ".ssa"):
        segments = parse_ass(content)
    else:
        raise ValueError(f"Unsupported subtitle format: {suffix}. Use .srt or .ass")

    if not segments:
        raise ValueError(f"No subtitle segments found in: {file_path}")

    # Sort by start time just in case
    segments.sort(key=lambda s: s.start_time)
    return segments


def assign_speakers_by_timing(
    subtitle_segments: List[SubtitleSegment],
    diarized_segments: list,  # List[DiarizedSegment] from diarizer
) -> List[SubtitleSegment]:
    """
    Match subtitle segments to speakers by timestamp overlap.

    For each subtitle line, find the diarized speaker whose segment
    overlaps the most with the subtitle timing.
    Falls back to "SPEAKER_00" if no match found.
    """
    for sub in subtitle_segments:
        best_speaker = "SPEAKER_00"
        best_overlap = 0.0

        for dia in diarized_segments:
            overlap_start = max(sub.start_time, dia.start_time)
            overlap_end   = min(sub.end_time,   dia.end_time)
            overlap = max(0.0, overlap_end - overlap_start)

            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = dia.speaker_label

        sub.speaker_label = best_speaker

    return subtitle_segments
