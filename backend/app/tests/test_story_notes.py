"""
Story notes (app/services/story_notes.py): merging what the AI learned,
the user's edits (which always win) and the blocks given to the AI.
"""
from app.services import story_notes as sn


def _char(zh, km="", about="", by="ai", speaker=""):
    return {"id": f"c_{zh}", "zh": zh, "km": km, "about": about, "speaker": speaker, "by": by}


def test_missing_notes_read_as_empty():
    assert sn.normalize(None) == sn.empty_notes()
    assert sn.normalize("junk") == sn.empty_notes()
    assert sn.normalize({"characters": [{"zh": ""}, "junk"]})["characters"] == []


def test_learning_adds_and_updates_only_its_own_items():
    notes = sn.normalize({"characters": [_char("王医生", "វ៉ាង", by="you"), _char("小美", "")]})
    learned = {"characters": [
        {"zh": "王医生", "km": "ផ្សេង", "about": "man, 30s"},
        {"zh": "小美", "km": "ម៉ី", "about": "woman, 20s"},
        {"zh": "刘护士", "km": "លីវ", "about": "woman, 40s"},
    ]}
    merged = sn.merge_learned(notes, learned, ["j1", "j2"])
    by_zh = {c["zh"]: c for c in merged["characters"]}
    assert (by_zh["王医生"]["km"], by_zh["王医生"]["about"], by_zh["王医生"]["by"]) == ("វ៉ាង", "", "you")
    assert (by_zh["小美"]["km"], by_zh["小美"]["by"]) == ("ម៉ី", "ai")
    assert (by_zh["刘护士"]["km"], by_zh["刘护士"]["by"]) == ("លីវ", "ai")
    assert by_zh["刘护士"]["id"].startswith("c_")
    assert merged["learned"] == ["j1", "j2"]
    assert {c["zh"]: c for c in notes["characters"]}["小美"]["km"] == ""   # the input is left alone
    # An answer that leaves a field empty doesn't wipe what an ai item had
    again = sn.merge_learned(merged, {"characters": [{"zh": "小美", "km": "", "about": ""}]}, ["j3"])
    assert {c["zh"]: c for c in again["characters"]}["小美"]["km"] == "ម៉ី"
    assert again["learned"] == ["j1", "j2", "j3"]


def test_a_removed_item_is_not_learned_again():
    notes = sn.normalize({"characters": [_char("王医生", "វ៉ាង"), _char("小美", "ម៉ី")]})
    edited = sn.apply_user_edit(notes, {"characters": [{"id": "c_王医生", "zh": "王医生", "km": "វ៉ាង", "about": ""}]})
    assert [c["zh"] for c in edited["characters"]] == ["王医生"]
    assert "小美" in edited["dismissed"]["characters"]

    learned = sn.merge_learned(edited, {"characters": [{"zh": "小美", "km": "ម៉ី", "about": "woman"}]}, ["j1"])
    assert [c["zh"] for c in learned["characters"]] == ["王医生"]

    back = sn.apply_user_edit(learned, {"characters": [
        {"id": "c_王医生", "zh": "王医生", "km": "វ៉ាង", "about": ""},
        {"zh": "小美", "km": "ម៉ី", "about": "woman"},
    ]})
    assert "小美" not in back["dismissed"]["characters"]
    assert {c["zh"]: c["by"] for c in back["characters"]} == {"王医生": "ai", "小美": "you"}


def test_user_written_summary_and_tone_win():
    notes = sn.normalize({
        "tone": {"text": "Warm family comedy", "by": "you"},
        "episodes": {"j1": {"summary": "Mine", "by": "you"}, "j2": {"summary": "Old", "by": "ai"}},
    })
    merged = sn.merge_learned(
        notes, {"tone": "Tense palace drama", "summaries": {"j1": "AI's", "j2": "New", "j3": "Third"}}, ["j1", "j2", "j3"])
    assert merged["tone"] == {"text": "Warm family comedy", "by": "you"}
    assert merged["episodes"]["j1"] == {"summary": "Mine", "by": "you"}
    assert merged["episodes"]["j2"] == {"summary": "New", "by": "ai"}
    assert merged["episodes"]["j3"] == {"summary": "Third", "by": "ai"}

    ai_tone = sn.merge_learned(sn.normalize({"tone": {"text": "Old", "by": "ai"}}), {"tone": "New tone"}, [])
    assert ai_tone["tone"] == {"text": "New tone", "by": "ai"}


def test_edits_mark_changed_items_as_yours():
    notes = sn.normalize({
        "characters": [_char("王医生", "វ៉ាង", speaker="SPEAKER_03"), _char("小美", "ម៉ី")],
        "words": [{"id": "w_1", "zh": "主任", "km": "ប្រធាន", "note": "", "by": "ai"}],
    })
    edited = sn.apply_user_edit(notes, {
        "characters": [
            {"id": "c_王医生", "zh": "王医生", "km": "វ៉ាង", "about": ""},   # unchanged (speaker not sent)
            {"id": "c_小美", "zh": "小美", "km": "ម៉ីម៉ី", "about": ""},     # changed
            {"zh": "刘护士", "km": "លីវ", "about": "woman"},               # new
        ],
        "words": [],
        "intro": "  A hospital drama.  ",
        "tone": "Light and fast",
        "summaries": {"j1": "My summary"},
    })
    by_zh = {c["zh"]: c for c in edited["characters"]}
    assert (by_zh["王医生"]["by"], by_zh["王医生"]["speaker"]) == ("ai", "SPEAKER_03")
    assert by_zh["小美"]["by"] == "you"
    assert by_zh["刘护士"]["by"] == "you" and by_zh["刘护士"]["id"].startswith("c_")
    assert edited["words"] == [] and edited["dismissed"]["words"] == ["主任"]
    assert edited["intro"] == "A hospital drama."
    assert edited["tone"] == {"text": "Light and fast", "by": "you"}
    assert edited["episodes"]["j1"] == {"summary": "My summary", "by": "you"}


def test_limits():
    many = [{"zh": f"角色{i}", "km": f"k{i}", "about": ""} for i in range(70)]
    merged = sn.merge_learned(sn.empty_notes(), {"characters": many, "summaries": {"j1": "x" * 1000}}, ["j1"])
    assert len(merged["characters"]) == 60
    assert len(merged["episodes"]["j1"]["summary"]) == 400
    assert len(sn.with_intro(None, "y" * 5000)["intro"]) == 4000
    assert len(sn.normalize({"words": [{"zh": f"词{i}", "km": "k"} for i in range(200)]})["words"]) == 150


def test_translation_block_is_bounded():
    order = [f"j{i}" for i in range(1, 82)]
    notes = sn.normalize({"episodes": {f"j{i}": {"summary": f"Plot {i}", "by": "ai"} for i in range(1, 81)}})
    block = sn.for_translation(notes, "j81", order)
    for n in range(76, 81):
        assert f"Episode {n}: Plot {n}" in block
    assert "Episode 75" not in block
    assert "Episode 81" not in block
    # An earlier episode only looks back, never ahead
    early = sn.for_translation(notes, "j3", order)
    assert "Episode 2: Plot 2" in early and "Plot 3" not in early


def test_translation_block_names_characters_and_words():
    notes = sn.normalize({
        "intro": "A hospital drama in Shanghai.",
        "tone": {"text": "Light", "by": "ai"},
        "characters": [_char("王医生", "វ៉ាង", "man, 30s", speaker="SPEAKER_03")],
        "words": [{"id": "w_1", "zh": "主任", "km": "ប្រធាន", "note": "head of department", "by": "ai"}],
    })
    block = sn.for_translation(notes, "j1", ["j1"])
    assert block.startswith("ABOUT THIS DRAMA")
    assert "王医生 → វ៉ាង (SPEAKER_03): man, 30s" in block
    assert "主任 → ប្រធាន — head of department" in block
    assert "A hospital drama in Shanghai." in block
    assert sn.for_translation(sn.empty_notes(), "j", ["j"]) == ""
    assert sn.for_translation(None, "j", ["j"]) == ""
    assistant = sn.for_assistant(notes, ["j1"])
    assert assistant.startswith("STORY NOTES FOR THIS PROJECT") and "王医生" in assistant
