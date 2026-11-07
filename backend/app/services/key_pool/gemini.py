"""The gateway's general-purpose translator, for POST /gateway/translate (other tools can use the pool through it):
segments `{index, text}` in, the same indices out. Chunks of CHUNK_SIZE; a chunk whose answer doesn't match (a missing
or extra index, bad JSON) is halved and asked again, twice at most; what still fails comes back with `text: null`.
Dubify's own translation (translator.py) has its own prompts and doesn't come through here. Every request goes through
gemini_models.generate_content, so the pool picks the key and fails over on a refusal."""
import json
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.services import gemini_models
from app.services.key_pool.store import SQLiteStore, compute_cache_key

logger = logging.getLogger(__name__)

CHUNK_SIZE = 80
MAX_REPAIR_DEPTH = 2
EXPANSION_FACTORS = {"de": 1.35, "fi": 1.35, "ru": 1.35, "pl": 1.35, "hu": 1.35,
                     "fr": 1.15, "es": 1.15, "it": 1.15, "pt": 1.15}


def compute_max_chars(source_line: str, target_lang: str) -> int:
    """The longest a line may come out, from the source's length and the language's usual expansion (floor 40)."""
    return max(40, math.ceil(len(source_line) * EXPANSION_FACTORS.get(target_lang.lower(), 1.0)))


def compute_max_output_tokens(num_segments: int) -> int:
    return max(1024, math.ceil(num_segments * 55))


def _payload(chunk: List[Dict[str, Any]], target_lang: str, glossary: Optional[Dict[str, str]]) -> dict:
    system = ("You are an expert subtitling and dubbing translator. "
              f"Translate the text into {target_lang}. Preserve every index exactly. One output object per input "
              "segment. No merging or splitting. Respect max_chars per line for timing constraints. "
              "Output JSON only adhering to the specified schema.")
    if glossary:
        system += "\nApply the following glossary verbatim:\n" + "\n".join(f"- '{k}': '{v}'" for k, v in glossary.items())
    lines = [{"index": s["index"], "text": s["text"], "max_chars": compute_max_chars(s["text"], target_lang)} for s in chunk]
    return {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(lines, ensure_ascii=False)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "index": {"type": "INTEGER"}, "text": {"type": "STRING"}}, "required": ["index", "text"]}},
            "maxOutputTokens": compute_max_output_tokens(len(chunk)),
        },
    }


class QuotaExhausted(Exception):
    """Every key is at its limit (or refused): the rest of the request can't be done now."""


class Translator:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    async def _ask(self, chunk: List[Dict[str, Any]], target_lang: str, glossary) -> Optional[List[dict]]:
        """One request for a chunk; the parsed answer, None when it can't be read. Raises QuotaExhausted."""
        try:
            resp = await gemini_models.generate_content("translation", _payload(chunk, target_lang, glossary),
                                                        timeout=httpx.Timeout(120.0, connect=15.0))
        except httpx.HTTPError as e:
            logger.warning(f"/gateway/translate: couldn't reach Google ({type(e).__name__})")
            return None
        if resp.status_code in (429, 402):
            raise QuotaExhausted(gemini_models.error_message(resp.text))
        if resp.status_code != 200:
            logger.warning(f"/gateway/translate: Gemini {resp.status_code}: {gemini_models.error_message(resp.text)}")
            return None
        try:
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
        except (KeyError, IndexError, ValueError, TypeError):
            return None
        return data if isinstance(data, list) else None

    async def chunk(self, chunk: List[Dict[str, Any]], target_lang: str, glossary=None, depth: int = 0) -> List[dict]:
        """Index-aligned results for `chunk`; segments that couldn't be done have `text: None`."""
        if not chunk:
            return []
        want = {s["index"] for s in chunk}
        result = await self._ask(chunk, target_lang, glossary)
        if (result is not None and len(result) == len(chunk)
                and {r.get("index") for r in result if isinstance(r, dict)} == want):
            model = gemini_models.current_model("translation")
            by_index = {s["index"]: s["text"] for s in chunk}
            for r in result:
                if r.get("text"):
                    await self._remember(by_index[r["index"]], target_lang, model, r["text"])
            return sorted(result, key=lambda r: r["index"])
        if depth < MAX_REPAIR_DEPTH and len(chunk) > 1:
            mid = len(chunk) // 2
            logger.warning(f"/gateway/translate: halving a chunk of {len(chunk)} lines (depth {depth})")
            return (await self.chunk(chunk[:mid], target_lang, glossary, depth + 1)
                    + await self.chunk(chunk[mid:], target_lang, glossary, depth + 1))
        return [{"index": s["index"], "text": None} for s in chunk]

    async def _remember(self, source: str, target_lang: str, model: str, translated: str) -> None:
        try:
            await self.store.set_cached_translation(compute_cache_key(source, target_lang, model), target_lang, model,
                                                    source, translated)
        except Exception as e:  # noqa: BLE001 — the cache is an extra
            logger.warning(f"/gateway/translate: couldn't cache a line: {e}")

    async def all(self, segments: List[Dict[str, Any]], target_lang: str, glossary=None) -> Tuple[List[dict], List[int]]:
        """Every segment: from the cache when seen before, the rest in chunks. (results in index order, failed indices)"""
        model = gemini_models.current_model("translation")
        done: Dict[int, dict] = {}
        todo: List[Dict[str, Any]] = []
        for s in segments:
            try:
                hit = await self.store.get_cached_translation(compute_cache_key(s["text"], target_lang, model))
            except Exception:  # noqa: BLE001
                hit = None
            if hit:
                done[s["index"]] = {"index": s["index"], "text": hit}
            else:
                todo.append(s)
        for start in range(0, len(todo), CHUNK_SIZE):
            part = todo[start:start + CHUNK_SIZE]
            try:
                for r in await self.chunk(part, target_lang, glossary):
                    done[r["index"]] = r
            except QuotaExhausted:
                for s in todo[start:]:
                    done.setdefault(s["index"], {"index": s["index"], "text": None})
                break
        results = [done.get(s["index"], {"index": s["index"], "text": None}) for s in segments]
        return results, [r["index"] for r in results if not r.get("text")]
