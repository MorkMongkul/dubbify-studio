"""Media (0.4.0): the rules a job's media clips follow (app/services/media.py)."""
import pytest

from app.services import media

VIDEO = {"kind": "video", "duration_secs": 7.0}
IMAGE = {"kind": "image", "duration_secs": None}
ASSETS = {"v": VIDEO, "i": IMAGE}


def clip(asset="v", start=2.0, end=9.0, lane=0, source_in=0.0, **kw):
    return {"asset_id": asset, "start_time": start, "end_time": end, "lane_index": lane, "source_in": source_in,
            "x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0, "volume_db": 0.0, **kw}


def test_a_video_never_plays_past_its_own_end():
    [c] = media.normalize([clip(start=2.0, end=12.0, source_in=1.5)], ASSETS, 88.0)
    assert (c["start_time"], c["end_time"]) == (2.0, 7.5)          # 7 s − 1.5 s in
    [c] = media.normalize([clip(asset="i", start=2.0, end=40.0)], ASSETS, 88.0)
    assert c["end_time"] == 40.0                                      # an image stretches


def test_clips_stay_inside_the_episode_and_their_settings_in_range():
    [c] = media.normalize([clip(start=85.0, end=92.0, volume_db=30.0, width=0.0)], ASSETS, 88.0)
    assert (c["start_time"], c["end_time"], c["volume_db"], c["width"]) == (85.0, 88.0, media.VOLUME_MAX_DB, 0.02)
    [c] = media.normalize([clip(start=-1.0, end=0.0)], ASSETS, 88.0)
    assert c["start_time"] == 0.0 and c["end_time"] == pytest.approx(0.1)


def test_two_clips_on_one_track_may_not_overlap():
    with pytest.raises(ValueError, match="overlap on track 0"):
        media.normalize([clip(start=2.0, end=9.0), clip(start=8.0, end=10.0)], ASSETS, 88.0)
    assert len(media.normalize([clip(start=2.0, end=9.0), clip(start=8.0, end=10.0, lane=1)], ASSETS, 88.0)) == 2


def test_media_that_is_not_in_the_library_is_refused():
    with pytest.raises(ValueError, match="unknown media"):
        media.normalize([clip(asset="gone")], ASSETS, 88.0)


def test_a_template_clip_past_a_shorter_episodes_end_moves_earlier():
    [c] = media.fit_template([clip(start=100.0, end=107.0)], 88.0, [])
    assert (c["start_time"], c["end_time"], c["lane_index"]) == (81.0, 88.0, 0)
    assert media.fit_template([clip(asset="i", start=0.0, end=95.0)], 88.0, []) == []   # longer than the episode


def test_a_template_never_lands_on_clips_already_there():
    existing = [clip(start=80.0, end=86.0, lane=0)]
    [c] = media.fit_template([clip(start=100.0, end=107.0, lane=0)], 88.0, existing)
    assert (c["start_time"], c["lane_index"]) == (81.0, 1)
    assert existing == [clip(start=80.0, end=86.0, lane=0)]         # untouched


def test_switches_of_media_tracks_without_clips_are_dropped():
    flags = media.flags_from('{"0": {"hidden": true}, "3": {"muted": true}, "x": {"locked": true}}')
    assert flags == {0: {"locked": False, "hidden": True, "muted": False}, 3: {"locked": False, "hidden": False, "muted": True}}
    assert media.prune_flags(flags, [clip(lane=0)]) == {0: flags[0]}
    assert media.flags_to({}) is None
