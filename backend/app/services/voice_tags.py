"""
app/services/voice_tags.py
Tags that help pick a voice for an actor: who the voice sounds like (gender)
and roughly how old (age_group), plus free personality and role tags.

Who + age put a voice in a section of the Voices tab and the voice pickers
(voice_section); the frontend only maps the section keys to labels. A voice
with no "who" yet has no section: it stays under "My voices".
"""
GENDERS = ("man", "woman", "boy", "girl")
AGE_GROUPS = ("child", "teen", "20s", "30s", "40s", "50s", "60s", "70s+")
SECTIONS = ("young_men", "young_women", "older_men", "older_women", "elders", "teens_children", "narrators")
MAX_TAGS = 6
MAX_TAG_LENGTH = 24


def normalize_tags(tags: list[str] | None) -> list[str]:
    """Trim and de-duplicate tags (ignoring case, keeping the first spelling); raise past the limits."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        tag = " ".join(str(tag).split())
        if not tag or tag.lower() in seen:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise ValueError(f"A tag can be at most {MAX_TAG_LENGTH} characters: '{tag}'")
        seen.add(tag.lower())
        cleaned.append(tag)
    if len(cleaned) > MAX_TAGS:
        raise ValueError(f"A voice can have at most {MAX_TAGS} tags of each kind")
    return cleaned


def voice_section(gender: str | None, age_group: str | None, roles: list[str] | None) -> str | None:
    """The Voices-tab section for these tags; None = "My voices" (no "who" chosen yet)."""
    if any(" ".join(str(r).split()).lower() == "narrator" for r in roles or []):
        return "narrators"
    if not gender:
        return None
    if gender in ("boy", "girl") or age_group in ("child", "teen"):
        return "teens_children"
    if age_group in ("60s", "70s+"):
        return "elders"
    older = age_group in ("40s", "50s")
    if gender == "man":
        return "older_men" if older else "young_men"
    return "older_women" if older else "young_women"
