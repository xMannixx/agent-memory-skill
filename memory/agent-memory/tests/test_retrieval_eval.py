"""Retrieval eval harness.

Seeds a fixed set of synthetic German evidence facts into a fresh in-memory
AgentMemory, then measures retrieval quality with three guards:

- positives:      recall@3 over paraphrased queries must meet a baseline.
- hard_negatives: a forbidden fact must NOT appear in the top 3 (precision).
- regressions:    queries that failed before v3.0 must each pass strictly.

The set is self-contained and deterministic so it can run in CI.
"""

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from memory import AgentMemory

FIXTURE = Path(__file__).parent / "fixtures" / "retrieval_eval.json"
RECALL_AT = 3
RECALL_BASELINE = 0.8


def _load_fixture():
    with FIXTURE.open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture
def seeded_mem():
    data = _load_fixture()
    mem = AgentMemory(db_path=":memory:")
    for content in data["facts"]:
        mem.remember(
            content,
            authority_class="evidence",
            source="conversation",
            confidence=0.8,
        )
    return mem, data


def _top_contents(mem, query):
    return [fact.content for fact in mem.recall(query, limit=RECALL_AT)]


def test_positives_meet_recall_at_3_baseline(seeded_mem):
    mem, data = seeded_mem
    hits = 0
    misses = []
    for case in data["positives"]:
        contents = _top_contents(mem, case["query"])
        if any(case["expect"] in content for content in contents):
            hits += 1
        else:
            misses.append((case["query"], case["expect"], contents))

    recall = hits / len(data["positives"])
    assert recall >= RECALL_BASELINE, (
        f"recall@{RECALL_AT}={recall:.2f} < {RECALL_BASELINE}; misses={misses}"
    )


def test_hard_negatives_are_not_surfaced(seeded_mem):
    mem, data = seeded_mem
    for case in data["hard_negatives"]:
        contents = _top_contents(mem, case["query"])
        assert not any(case["forbid"] in content for content in contents), (
            f"query {case['query']!r} surfaced forbidden {case['forbid']!r}: {contents}"
        )


def test_regressions_pass_strictly(seeded_mem):
    mem, data = seeded_mem
    for case in data["regressions"]:
        contents = _top_contents(mem, case["query"])
        assert any(case["expect"] in content for content in contents), (
            f"regression {case['query']!r} did not surface {case['expect']!r}: {contents}"
        )
