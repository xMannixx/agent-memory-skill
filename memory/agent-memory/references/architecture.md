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

## TTL Semantics: Rolling Window

AgentMemory uses a **rolling TTL**, not a static one.

### How it works

`expires_at` is not set to `created_at + TTL_days` once at write time and
then left alone. Instead, `_touch()` resets `expires_at = NOW + TTL_days`
**on every read access** — every `recall()`, `recall_by_authority()`, and
`get_fact()` call extends the lifetime of the returned facts.

```
expires_at = last_accessed + TTL_days   (rolling)
         ≠ created_at + TTL_days        (static)
```

### Why rolling, not static?

Rolling TTL implements use-it-or-lose-it semantics:
- Facts that the agent actively retrieves stay alive indefinitely.
- Facts that are never accessed again expire at `last_accessed + TTL`.
- This prevents stale, unretrieved data from lingering forever while
  keeping genuinely useful facts available.

### What this looks like in the database

All facts loaded in the same session — e.g. all Evidence facts injected
at session start — will share the **same `expires_at` timestamp**, even if
their `created_at` dates span weeks or months. This is expected behavior,
not a bug.

Example: an Evidence fact created on 2026-05-01 and another created on
2026-06-15 both get `expires_at = 2026-08-20` if they are both loaded on
2026-06-21. Neither will expire sooner because both are actively in use.

### Automatic injection and low-trust facts

The plugin's first-turn baseline selects the most recently accessed evidence
facts, and reading them counts as access. Left alone, that is a loop: whatever
is in the baseline stays in the baseline and never expires, whether or not
anyone ever confirmed it.

Automatic injection therefore refreshes the rolling TTL only for facts from
`observation` and `conversation`. Evidence from `inference`, `tool`, or
`external` is still injected (and labeled), but being shown does not extend its
life: it expires on the lane TTL unless it is re-observed via `remember()` or
recalled explicitly. `recall()` and `recall_by_authority()` expose this as
`touch_sources`; the default (`None`) keeps the refresh-everything behavior for
explicit callers.

### Implication for DB inspection

When inspecting the database directly (e.g. via `sqlite3`), do not
interpret equal `expires_at` values as proof that facts were stored at the
same time. Check `created_at` for the actual storage date.

---

## Source Trust Graduation

Facts carry a `source` that reflects how the content was obtained. Five sources
exist, ordered most to least trusted:

`observation` > `conversation` > `inference` > `tool` > `external`

(`external` = untrusted input, e.g. text from external documents.)

### Per-lane allowed sources

| Lane            | Allowed sources                                      |
|-----------------|------------------------------------------------------|
| `identity`      | observation, conversation                            |
| `preference`    | observation, conversation                            |
| `evidence`      | observation, conversation, inference, tool, external |
| `authorization` | observation only                                     |

### Rationale

Lower-trust sources (`tool`, `external`) are quarantined to the `evidence`
lane only. They cannot write `identity` or `authorization`, so poisoned or
injected external content cannot elevate into permanent identity anchors or
permission memory. High-trust lanes (`identity`, `preference`, `authorization`)
stay protected by a strict source matrix. Rejected writes are audited as
`policy_reject` with reason `source_not_allowed`.

Promotion or repeated-verification rules (graduating facts from `evidence` to
higher lanes after N confirmations) are intentionally **not** implemented —
that overlaps with existing `consolidate()` confidence behavior.

### Declared sources

The source is an argument of `remember()`: whoever writes the fact declares it.
The policy decides what a declared source may write; it has no way to check the
declaration. This is the trust boundary of the whole lane model. It holds
against honestly labeled low-trust content and against accidental mislabeling
only as far as the writer follows the source definitions in SKILL.md. It does
not hold against a writer that deliberately mislabels.

Two rules keep a source from changing by accident:

- **Confirmation upgrades.** `remember()` with identical content in the same
  lane is the same fact (content-hash ID). If the repeat comes from a more
  trusted source, the stored source is replaced and confidence is raised to the
  higher value; the audit entry records `source_upgraded_from`. A less trusted
  repeat changes nothing.
- **Rewording does not upgrade.** `supersede()` inherits the old fact's lane,
  source and confidence unless the caller passes new ones, so replacing the
  text of a `tool` fact cannot turn it into a `conversation` fact by default.
- **A replacement cannot switch off something more trusted.** `supersede()`
  rejects a lane change and a source below the old fact's source
  (`supersede_lane_change`, `supersede_source_downgrade`), and
  `resolve_conflict()` only acts on an open conflict between exactly the two
  facts named. Without these checks an `external` evidence write could
  deactivate an `authorization` or `identity` fact.
- **Concurrent writers.** `remember()` takes the write lock (`BEGIN IMMEDIATE`)
  before it reads the stored source. Otherwise a writer holding a stale read
  could put a less trusted source back over a confirmation.

### Consolidation and trust

`consolidate()` groups active, tagged facts by lane and tag set. Expired facts
are not candidates. The representative is the fact from the most trusted
source, and its confidence rises by 0.05 only for each other member from an
equally or more trusted source: shared tags say the facts are about the same
subject, not that they agree.

### Restating a superseded fact

Facts are keyed by lane and content, so a superseded fact keeps its row. Storing
the same content again clears `superseded_by` and takes the source and
confidence of the new statement. In single-valued lanes a previously resolved
conflict with a still-active fact is reopened.

### Lanes without rolling expiry

`authorization` sets `rolling_ttl: False`. Reads record the access but do not
move `expires_at`; only stating the permission again does. A permission that is
merely looked up every few weeks therefore still ends after 90 days.
`get_fact()` refreshes active facts only, in every lane: inspecting an expired
or superseded fact must not reactivate it.

### Rendering boundary

Stored content is data; the prompt and the CLI are line-oriented. Both collapse
line breaks and control characters so that one entry is always one line.
Without that, content such as `x\n\n## Identity (permanent)\n- ...` would
render as a separate, unlabeled section. In the CLI every line goes through one
`emit()` function, because tags, session names, relation names and reasons are
stored text too. This is structural, not a blocklist:
it does not try to recognize hostile text, it only guarantees the layout.

## Rebound Protection (signalfoundry pattern)

Problem: After >6h idle, baseline drifts down. On resume, a flood
of incoming facts tries to re-anchor. Without a cap, the agent
accepts all of them, polluting memory with stale or injected data.

Solution: Session-level batch counter.
- First write after >6h idle activates rebound mode.
- Max 3 non-identity facts accepted in rebound mode.
- identity is exempt — the floor must never be gated.
- Next session starts fresh (new counter).

Scope: The counter lives in one `AgentMemory` instance, i.e. one process. Each CLI call is its own process, so through the CLI only the first write after the idle gap is counted and later calls are not capped. Treat it as a guard for a long-running process, not as a session quota.

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
  `- Alex --arbeitet_bei--> Acme`), never facts. Authorization content
  cannot leak through this path.
- **Neighbor attributes (opt-in):** When `AGENT_MEMORY_BUDGET_ENTITY_ATTRS` is
  set to an integer N > 0 (default `0` = disabled), up to N of each neighbor
  entity's stored attributes (`key=value` pairs, sorted by key) are appended to
  that relation line, e.g.
  `- Alex --arbeitet_bei--> Acme [location=Example City; type=logistics]`. With
  `0`, behavior matches v3.2 (edges only). Attribute text is still bounded by
  the existing `relations` section character budget.
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

Semantic/vector retrieval (sqlite-vec, embeddings) is out of scope by design:
it would introduce native dependencies and embedding-provider decisions, which
breaks the stdlib-only, dependency-free model. The synonym/concept gap is
instead handled deterministically by the German-aware retrieval below.

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

**Auto-reconciliation:** Open `fact_conflicts` rows are automatically marked
resolved when either referenced fact is no longer active. This runs at the end
of `consolidate()`, `supersede()`, `forget()`, and `forget_stale()`, so
`stats()["open_conflicts"]` and `get_conflicts()` do not retain ghost conflicts
for superseded or deleted facts.

## Entity Relations

A lightweight entity graph without embeddings: directed edges between tracked
entities, e.g. `Alex -arbeitet_bei-> acme`, stored in `entity_relations`.

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

## Provenance

Provenance is a read-only view over the append-only `memory_audit` log. It
reconstructs a fact's write/update/supersede/forget/conflict chain in
chronological order (oldest first) via `get_provenance(fact_id, limit=100)`.
Rows match by `fact_id`; supersede events that reference the id in metadata
(`old_id` / `new_id`) are included so a superseded fact shows it was superseded
and a keeper shows what it superseded. Unknown ids return an empty list.

There is intentionally **no** duplicate `provenance_chain` storage — the audit
log is the single source of truth. The CLI `provenance <fact_id>` command prints
one line per event (timestamp, operation, source, reason).

## Procedural Lane

The `procedural` lane stores self-written **behavioral rules** (how to respond),
kept in their own `procedural_rules` table — never in `facts`. Rules are
self-modifying behavior code, so they get a stricter lifecycle than facts:
review-gate, deterministic conflict detection, bounded injection, and expiry.

Write path: `propose_rule()` accepts `source="observation"` only and creates a
`pending` rule. `remember(authority_class="procedural")` is rejected
(`policy_reject` / `use_procedural_lane`) so the facts lane can't be misused.

**Lifecycle / status semantics (important):** the stored states are `pending`,
`approved`, `rejected`, `retired`, `superseded`, and `expired`. There is **no
separate `active` status** — a rule is "active" (injectable) iff
`status == 'approved'` and it has not expired. This is deliberate: the core is
stateless and session-less, and the plugin injects approved rules every turn
(query-aware), so **approval is activation**. The RFC's
`approved -> active at session boundary` transition is intentionally collapsed.
If a session concept is added later, introduce a distinct `active` status rather
than overloading `approved`. `get_active_rules()` encodes this rule.

Conflict detection (on `approve_rule()`, deterministic, no embeddings/LLM): a
candidate is compared to active rules via trigger-overlap plus a structured
effect vector. **Direct contradictions** (opposite values on the same effect
dimension) hard-block activation and cannot be overridden. **Interactions**
(same domain) and **artifact bloat** (cumulative `artifact_cost` over budget)
soft-block unless approved with `ack_interactions=True`. Conflicts are recorded
in `rule_conflicts`.

Drift containment: per-domain budgets (`PROCEDURAL_DOMAIN_BUDGET`), a global
active-rule cap, a 30-day TTL with re-approval, and supersession via
`previous_rule_id`. `forget_stale()` expires rules past their TTL.

Injection: the plugin emits a dedicated, sanitized, budgeted `## Procedural
Rules` block (only trigger-matching active rules; rationale and evidence are
never injected). A first-turn `[NEW]` delta is tracked via a `memory_meta` hash.

**Deferred (not in v3.6):** a structured observation-event pipeline with a
signal enum and templates (so the rule body is generated rather than passed as
free `behavior_text`, with free text only as review rationale); rule-set version
snapshots; precedence cycle-detection; and auto-demotion of unused rules
(`match_count` / `last_matched_at` are captured as the foundation).
