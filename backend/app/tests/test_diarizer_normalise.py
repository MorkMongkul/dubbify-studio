"""
Regression test for _normalise_speakers cross-chunk speaker identity.

Bug: the old implementation renumbered speakers by treating each chunk's
raw MOSS label (e.g. "SPEAKER_00") as if it were already a stable global
identity token. MOSS numbers speakers fresh per call, so a real speaker
who talks in chunk 0 and again in chunk 2 (but wasn't the one who happened
to be duplicated across the chunk 0/1 and 1/2 overlap windows) could easily
end up under two or three different global labels — or, worse, get merged
with an unrelated speaker who just happened to get the same raw number in
another chunk.
"""
from app.services.diarizer import DiarizedSegment, _normalise_speakers


def _seg(chunk_index, label, start, end, text=""):
    return DiarizedSegment(
        speaker_label=label, start_time=start, end_time=end,
        gender="unknown", age_group="adult", source_text=text,
        chunk_index=chunk_index,
    )


def test_same_speaker_persists_across_two_chunk_boundaries():
    """
    Speaker A talks in chunk 0, chunk 1, and chunk 2. Chunk 0/1 overlap
    (10s) re-transcribes A's line near the boundary, as does chunk 1/2.
    Speaker B only ever appears in chunk 1. All of A's segments across all
    three chunks must end up under the SAME global label.
    """
    overlap = 10.0
    segments = [
        # Chunk 0: A speaks at the start, then again right at the boundary
        _seg(0, "SPEAKER_00", 0.0, 5.0, "A line one"),
        _seg(0, "SPEAKER_00", 535.0, 540.0, "A boundary line 0/1"),

        # Chunk 1 starts at offset 530 (540 - overlap). The boundary line
        # gets re-transcribed here as chunk 1's own "SPEAKER_00" (MOSS
        # numbers fresh per call — no relation to chunk 0's SPEAKER_00).
        _seg(1, "SPEAKER_00", 535.0, 540.0, "A boundary line 0/1"),
        _seg(1, "SPEAKER_01", 600.0, 605.0, "B line one"),
        # A speaks again later in chunk 1, right at the 1/2 boundary,
        # again under chunk 1's local "SPEAKER_00".
        _seg(1, "SPEAKER_00", 1065.0, 1070.0, "A boundary line 1/2"),

        # Chunk 2 starts at offset 1060. Re-transcribes the 1/2 boundary
        # line as chunk 2's own "SPEAKER_00".
        _seg(2, "SPEAKER_00", 1065.0, 1070.0, "A boundary line 1/2"),
        _seg(2, "SPEAKER_00", 1100.0, 1105.0, "A line in chunk 2"),
    ]

    result = _normalise_speakers(segments, overlap)

    a_labels = {s.speaker_label for s in result if "A " in s.source_text}
    b_labels = {s.speaker_label for s in result if "B " in s.source_text}

    assert len(a_labels) == 1, f"Speaker A split across multiple labels: {a_labels}"
    assert len(b_labels) == 1, f"Speaker B split across multiple labels: {b_labels}"
    assert a_labels != b_labels, "Speaker A and B were incorrectly merged"

    # Duplicate boundary segments should have been dropped, not double-counted.
    assert len(result) == 5


def test_unrelated_speakers_with_same_raw_label_are_not_merged():
    """
    Chunk 0's "SPEAKER_00" and chunk 1's "SPEAKER_00" are DIFFERENT people
    who never overlap in time — the old code merged them just because the
    label string matched. They must stay distinct.
    """
    segments = [
        _seg(0, "SPEAKER_00", 0.0, 5.0, "chunk0 person"),
        _seg(1, "SPEAKER_00", 600.0, 605.0, "chunk1 person"),
    ]

    result = _normalise_speakers(segments, 10.0)
    labels = {s.speaker_label for s in result}
    assert len(labels) == 2, f"Unrelated speakers were merged: {result}"
