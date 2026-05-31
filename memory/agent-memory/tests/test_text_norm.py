import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from text_norm import expand, fold, query_terms, stem_de


SEED_SYNONYMS = {
    "server": ["vps", "host", "maschine", "rechner", "serverkonfiguration"],
    "infrastruktur": ["setup", "system", "hosting", "umgebung"],
    "jobsuche": ["stellensuche", "bewerbung", "karriere", "job"],
    "datenbank": ["db", "sqlite", "datenbanken"],
    "fehler": ["bug", "problem", "error", "absturz"],
}


def test_fold_maps_german_umlauts_without_bare_vowels():
    assert fold("äöüß") == "aeoeuess"
    assert fold("schön") == "schoen"
    assert fold("schon") == "schon"
    assert fold("schön") != fold("schon")


def test_stem_de_reduces_infrastructure_problem_compound():
    compound = stem_de("infrastrukturprobleme")
    base = stem_de("infrastruktur")

    assert compound.startswith(base)


def test_query_terms_drops_stopwords_short_tokens_and_lowercases():
    assert query_terms("Wie ist AI Server und DB Setup?") == ["server", "setup"]


def test_expand_reverse_maps_synonym_to_canonical_term():
    expanded = expand(["vps"], SEED_SYNONYMS)

    assert expanded[0] == "vps"
    assert "server" in expanded
    assert "host" in expanded
