import numpy as np
import pytest

from app.services.characters.decide import (
    SURE_VOICE_MARGIN,
    UNKNOWN_VOICE_FLOOR,
    Decision,
    Profile,
    decide,
    overlapping,
    profile_from,
)
from app.services.characters.types import LinePrints


def axis(i, n=4):
    v = np.zeros(n)
    v[i] = 1.0
    return v


def line_with(cosines, n=4):
    """A unit vector whose cosine to the axes 0, 1, ... is `cosines`."""
    v = np.zeros(n)
    v[: len(cosines)] = cosines
    v[len(cosines)] = np.sqrt(1 - float(np.dot(v, v)))
    return v


PROFILES = {"A": Profile(axis(0), axis(0), 5), "B": Profile(axis(1), axis(1), 5)}


def prints(voice=None, face=None):
    return LinePrints(voice, face, 1.0 if face is not None else None, None)


def test_a_face_print_never_decides():
    """0.5.2: recognition is by voice only (the face step took minutes per episode); a face print an older line still
    has is ignored."""
    d = decide(prints(line_with([0.8, 0.1]), line_with([0.1, 0.9])), PROFILES)
    assert d == Decision("A", True, [])
    # a face that agrees can't make an unclear voice sure
    d = decide(prints(line_with([0.60, 0.55]), line_with([0.9, 0.1])), PROFILES)
    assert d.speaker_id == "A" and not d.sure and d.guesses == [("A", "voice"), ("B", "voice")]
    # a face alone says nothing
    assert decide(prints(None, line_with([0.9, 0.1])), PROFILES) == Decision(None, False, [])


@pytest.mark.parametrize("second, sure", [(0.50, True), (0.51, False)])
def test_off_screen_voice_leading_by_0_10_is_sure(second, sure):
    assert SURE_VOICE_MARGIN == 0.10
    d = decide(prints(line_with([0.60, second])), PROFILES)
    assert d.speaker_id == "A" and d.sure is sure
    assert d.guesses == ([] if sure else [("A", "voice"), ("B", "voice")])


def test_no_profiles_decides_nothing():
    assert decide(prints(line_with([0.8, 0.1])), {}) == Decision(None, False, [])


def test_an_unknown_voice_with_no_face_match_is_nobody():
    low = UNKNOWN_VOICE_FLOOR - 0.05
    d = decide(prints(line_with([low, low - 0.1])), PROFILES)
    assert d.speaker_id is None and not d.sure


def test_a_line_without_prints_is_to_check():
    d = decide(LinePrints(None, None, None, None), PROFILES)
    assert d.speaker_id is None and not d.sure and d.guesses == []


def test_a_profile_is_the_normalised_mean_of_its_prints():
    p = profile_from([prints(axis(0), axis(0)), prints(axis(1), None), prints(None, axis(1))])
    assert p.lines == 3
    assert np.allclose(p.voice, [2**-0.5, 2**-0.5, 0, 0])
    assert np.allclose(p.face, [2**-0.5, 2**-0.5, 0, 0])
    assert profile_from([prints(None, None)]).voice is None


def test_a_voice_mixed_with_another_line_is_never_sure_by_voice_alone():
    """Two lines whose actors speak at once give one voice print of both: its lead says nothing (Review Focus 3)."""
    d = decide(prints(line_with([0.60, 0.45])), PROFILES, mixed_voice=True)
    assert d.speaker_id == "A" and not d.sure and d.guesses == [("A", "voice"), ("B", "voice")]
    # nor can a face that agrees
    assert not decide(prints(line_with([0.60, 0.45]), line_with([0.9, 0.1])), PROFILES, mixed_voice=True).sure


@pytest.mark.parametrize("spans, mixed", [
    ([(0.0, 2.0), (1.5, 3.0), (4.0, 5.0), (5.0, 6.0)], [True, True, False, False]),   # touching isn't overlapping
    ([(0.0, 10.0), (11.0, 12.0), (2.0, 3.0)], [True, False, True]),
    ([], []),
])
def test_lines_whose_actors_speak_at_once_overlap(spans, mixed):
    assert overlapping(spans) == mixed


def test_a_single_voice_profile_is_never_sure_by_margin():
    d = decide(prints(line_with([0.9])), {"A": PROFILES["A"]})
    assert d.speaker_id == "A" and not d.sure and d.guesses == [("A", "voice")]
    assert not decide(prints(line_with([0.9]), line_with([0.9])), {"A": PROFILES["A"]}).sure


def test_nobody_still_offers_the_voices_top_two():
    low = UNKNOWN_VOICE_FLOOR - 0.1
    d = decide(prints(line_with([low, low - 0.05])), PROFILES)
    assert d.speaker_id is None and d.guesses == [("A", "voice"), ("B", "voice")]
