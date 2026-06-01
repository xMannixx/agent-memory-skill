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

## Relation-Aware Plugin Recall (1-hop expansion)

On query turns (when the hook provides a current user message), the plugin can
inject direct entity relations into prompt context so v3.1 graph edges are
useful at recall time, not only via CLI.

- **Query-driven:** Known entities are detected by normalized term overlap
  between the user message and tracked entity names. For each matched entity,
  direct (1-hop) relations are fetched via `get_relations(..., direction="both")`.
- **Edge-only:** Only relation edges are injected (e.g.
  `- Manni --arbeitet_bei--> Arriva`), never facts. Authorization content
  cannot leak through this path.
- **Bounded:** Output is clipped by a dedicated `relations` budget (default:
  6 lines, 1000 characters). Expansion runs from at most 3 matched entities per
  turn (`RELATIONS_MAX_ENTITIES`).
- **Opt-out:** Set `AGENT_MEMORY_RELATIONS` to `0`, `false`, `no`, or `off` to
  disable. Per-lane limits can be overridden with `AGENT_MEMORY_BUDGET_RELATIONS`.

Injected relations appear under a `## Related` section alongside query-retrieved
evidence on later turns.

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

## Conflict Detection (single-valued lanes)

Some authority lanes represent at most one active truth per subject. The policy
flag `single_valued` marks this: `identity` and `authorization` are
single-valued; `preference` and `evidence` are not.

On `remember()`, when a new fact is written to a single-valued lane with a
non-empty tag set, the system looks for existing active facts in the same lane
with an identical tag set but different content. Each such pair is recorded in
`fact_conflicts` and an audit event `conflict_detected` is written. Detection is
non-blocking — the new fact is still stored.

Conflict scope is `(lane, set of tags)`. Untagged facts are not scoped for
conflicts; tag the subject so contradictions can be found.

Resolution API: `get_conflicts(include_resolved=False)` lists open (or all)
conflicts with both facts resolved; `resolve_conflict(keep_id, drop_ids)`
supersedes the losing facts and marks the conflict resolved, auditing
`conflict_resolved`. `stats()` includes `open_conflicts`.

**Design rationale:** Deterministic, no NLP or embeddings. Lane-scoped matching
avoids noise from unrelated facts. Non-blocking writes keep the hot path simple;
operators get visibility at write time plus an explicit resolution path.

**Interaction with `consolidate()`:** `consolidate()` still auto-collapses
same-(lane, tags) groups into one representative fact. Conflict detection adds
write-time visibility and manual resolution; consolidate remains the automatic
path. Both can apply to the same subject — not surprising if documented.

## Entity Relations

A lightweight entity graph without embeddings: directed edges between tracked
entities, e.g. `Manni -arbeitet_bei-> arriva`, stored in `entity_relations`.

API:
- `relate(from_name, predicate, to_name, ...)` — idempotent (same triple = one
  edge); auto-creates missing entities.
- `get_relations(name, direction="both", predicate=None)` — `direction` is
  `out`, `in`, or `both`.
- `related_entities(name, predicate=None, direction="both")` — neighbor
  `Entity` objects.

`forget_stale_lifecycle()` also removes expired relations and prunes orphan
edges whose endpoints no longer exist. `stats()` includes `relations`.

**Design rationale:** Stdlib-only, deterministic graph edges for structured
context (who works where, what owns what) without vector search or NLP parsing.
Complements facts and entities; retrieval stays explicit via name and predicate.
