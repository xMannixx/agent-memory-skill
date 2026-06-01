"""Small text normalization helpers for memory recall.

The stemming rules in this module are intentionally lightweight heuristics.
They are designed to make scoring and overlap checks a little more forgiving,
not to provide linguistically complete German stemming.
"""

from __future__ import annotations

import re


STOPWORDS = {
    "a",
    "an",
    "and",
    "das",
    "der",
    "die",
    "for",
    "how",
    "is",
    "ist",
    "mit",
    "of",
    "oder",
    "ohne",
    "or",
    "the",
    "to",
    "und",
    "was",
    "what",
    "wie",
    "with",
}

_UMLAUTS = str.maketrans({
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
})

_STEM_SUFFIXES = (
    "ungen",
    "heiten",
    "keiten",
    "lichen",
    "ische",
    "ungs",
    "heit",
    "keit",
    "lich",
    "isch",
    "igen",
    "ung",
    "ern",
    "en",
    "er",
    "e",
    "s",
)


def fold(term: str) -> str:
    """Lowercase a term and fold German umlauts without collapsing meanings."""
    return term.lower().translate(_UMLAUTS)


def stem_de(term: str) -> str:
    """Apply one lightweight German suffix rule to a lowercased term.

    This is a heuristic stemmer with ordered suffix stripping, not a complete
    linguistic stemmer. At most one suffix is removed, and only when the
    remaining stem has at least four characters.
    """
    lowered = term.lower()
    for suffix in _STEM_SUFFIXES:
        if lowered.endswith(suffix):
            stem = lowered[: -len(suffix)]
            if len(stem) >= 4:
                return stem
    return lowered


def normalize(term: str) -> str:
    """Return a comparable normalized form for overlap and scoring."""
    return fold(stem_de(term))


def query_terms(text: str) -> list[str]:
    """Extract lowercase, useful query terms while preserving first-seen order."""
    terms = []
    seen = set()
    for term in re.findall(r"\w+", text):
        lowered = term.lower()
        if len(lowered) < 3 or lowered in STOPWORDS or lowered in seen:
            continue
        seen.add(lowered)
        terms.append(lowered)
    return terms


def expand(terms: list[str], synonym_map: dict[str, list[str]]) -> list[str]:
    """Expand terms with canonical synonyms and reverse synonym lookups."""
    reverse: dict[str, list[str]] = {}
    canonical_map: dict[str, list[str]] = {}

    for key, values in synonym_map.items():
        canonical = key.lower()
        synonyms = [value.lower() for value in values]
        canonical_map[canonical] = synonyms
        for synonym in synonyms:
            reverse.setdefault(synonym, [])
            reverse[synonym].append(canonical)
            reverse[synonym].extend(
                sibling for sibling in synonyms if sibling != synonym
            )

    expanded = []
    seen = set()

    def add(value: str) -> None:
        lowered = value.lower()
        if lowered not in seen:
            seen.add(lowered)
            expanded.append(lowered)

    for term in terms:
        lowered = term.lower()
        add(lowered)

        for synonym in canonical_map.get(lowered, []):
            add(synonym)

        for related in reverse.get(lowered, []):
            add(related)

    return expanded
