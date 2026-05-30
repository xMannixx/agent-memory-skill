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
- The plugin filters broad FTS candidates by query-term overlap to avoid
  injecting weakly related evidence.

Hybrid vector retrieval with sqlite-vec is intentionally left as a separate
design step because it introduces optional native dependencies and embedding
provider decisions.
