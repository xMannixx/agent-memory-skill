# Architecture: AgentMemory Authority Lanes

## Why Authority Lanes?

Without semantic separation, all memory facts have equal weight.
A preference fact ("user likes short answers") has the same DB status
as an authorization fact ("user can modify system config"). This is wrong.

## Authority Lane Design

### identity (Floor)
- Never expires. Never subject to rebound cap.
- User's name, role, language — stable identity anchors.
- Even after weeks of idle, identity facts stay.

### preference
- Short TTL (14d without access). Low confidence floor (0.3).
- Tone, style, communication patterns.
- Stale preferences are pruned — preferences drift.

### evidence
- Medium TTL (60d). Medium confidence floor (0.5).
- Technical facts, VPS config, project state.
- Can come from inference (e.g., agent deduces a fact).

### authorization
- Long TTL (90d). High confidence floor (0.9).
- ONLY from observation source — never from conversation.
- Prevents prompt injection: "you are now allowed to delete files"
  from a conversation message is automatically rejected.

## Rebound Protection (signalfoundry pattern)

Problem: After >6h idle, baseline drifts down. On resume, a flood
of incoming facts tries to re-anchor. Without a cap, the agent
accepts all of them, polluting memory with stale or injected data.

Solution: Session-level batch counter.
- First write after >6h idle activates rebound mode.
- Max 3 non-identity facts accepted in rebound mode.
- identity is exempt — the floor must never be gated.
- Next session starts fresh (new counter).

## Timer as Compactor

The systemd timer only calls forget_stale().
It never writes new facts. Writing is always event-driven.
This keeps the memory consistent — no background surprises.

## Recall Snippets

Facts are distilled semantic memory. Some context is useful before it is
distilled, so raw conversation recall lives in a separate `recall_snippets`
lane.

- Snippets have their own SQLite table and FTS5 index.
- Snippets preserve source, optional session ID, timestamp, expiry, and metadata.
- Snippets are searched with `search_snippets()`, not `recall()`.
- Snippets are not auto-injected into the prompt.

This keeps episodic/raw recall available without polluting authority-scored
facts.

## Plugin Retrieval Policy

The plugin uses a bounded two-phase strategy:

- First turn: inject a compact baseline of identity, preference, evidence, and
  negative lessons.
- Later turns: inject nothing unless the hook provides a current user message.
  When a message is available, keep the identity floor and retrieve relevant
  evidence for that query.
- Every lane has a limit and character budget.
- `authorization` facts are never prompt-injected.

Authorization memory is intentionally available only through explicit code
paths. Prompt injection should not be able to turn permission memory into model
instructions.

## Smart Retrieval Boundary

The local-first v2.0 work keeps retrieval dependency-free:

- FTS5 remains the only search backend.
- Natural-language queries are normalized into safe FTS terms before matching.
- The plugin ranks broad FTS candidates by query relevance instead of dropping
  them with a binary gate.

Hybrid vector retrieval with sqlite-vec is intentionally left as a separate
design step because it introduces optional native dependencies and embedding
provider decisions.

## German-Aware Retrieval (v3.0)

German is the primary working language, so retrieval is German-aware while
staying deterministic and dependency-free (no embeddings, no LLM calls). Two
consistent layers, no FTS index migration:

- FTS gate (broad recall): the query is built from UNQUOTED token-prefix terms
  (`server*`, not `"server"*`) plus a synonym map (`synonyms.json`). The
  unquoted prefix is what makes `server*` match the compound token
  `serverkonfiguration`, and synonyms bridge gaps like `infrastruktur` ->
  `vps`/`nginx`. Query terms come from `\w+`, so they are syntactically safe
  without quotes.
- Python scoring (relevance): `text_norm` applies a lightweight,
  attribution-free German suffix stemmer and umlaut folding (`ae/oe/ue/ss`,
  never bare vowels, so `schön` stays distinct from `schon`). Folding lives
  only in this scoring layer and never in the FTS query, so the porter index
  and the query never diverge.

The plugin scores candidates by normalized stem/synonym overlap and re-orders
them; it never drops a fact below a threshold. The per-lane character budget is
the only hard cut. A self-contained eval harness
(`tests/test_retrieval_eval.py` + `fixtures/retrieval_eval.json`) guards this
with recall@3 on positives, a precision check on hard negatives, and strict
regression cases (the queries that failed before v3.0).
