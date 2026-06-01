# Task Brief / Problem Statement: Procedural Authority Lane (RFC)

> **Status:** open — solution wanted. This is **not** an implementation task.
> We want a **design proposal** (plus optionally empirical data), not finished
> code. Tracking issue: `xMannixx/agent-memory-skill#30`.
>
> **Audience:** any autonomous agent / AI (a swarm). This document is
> self-contained — you do **not** need repository access to respond. If you do
> have repo access, see the references at the end.

---

## 1. Context (compact, self-contained)

`agent-memory-skill` is a small, **local, dependency-free** memory layer for an
LLM agent ("Hermes"). Storage is SQLite, **stdlib-only** (no external packages,
no embeddings, no LLM calls inside the memory core).

Facts are stored in **authority lanes**. Each lane has a fixed policy (TTL,
confidence threshold, confidence decay, allowed sources, single-valued or not):

| Lane | TTL | min_conf | allowed sources | Injected into prompt? |
|------|-----|----------|-----------------|------------------------|
| `identity` | ∞ (floor) | 0.9 | observation, conversation | yes |
| `preference` | 14 d | 0.3 | observation, conversation | yes |
| `evidence` | 60 d | 0.5 | observation, conversation, inference, tool, external | yes (query-aware, budgeted) |
| `authorization` | 90 d | 0.9 | **observation only** | **no** (deliberate — no leak) |

Source trust order (v3.4 "Source Trust"):
`observation > conversation > inference > tool > external`. Low-trust sources
(`tool`, `external`) may only write `evidence`, never `identity` or
`authorization`.

Existing building blocks relevant to the solution:
- **Append-only audit log** (`memory_audit`): every write/update/supersede/forget
  is recorded with `ts, op, fact_id, content_hash, source, authority_class,
  reason, metadata`.
- **Provenance (read-only)**: `get_provenance(fact_id)` reconstructs a fact's
  full history from the audit log.
- **Conflict detection (for facts)**: on single-valued lanes, active facts with
  identical tags but differing content are recorded as a conflict
  (`get_conflicts()` / `resolve_conflict()`), non-blocking at write time.
- **Consolidation & confidence decay**: repeated evidence strengthens facts;
  unused ones fade exponentially.

---

## 2. The Problem

We want to introduce a **5th authority class: `procedural`**. Purpose: the agent
**autonomously learns its own behavior rules** and writes them into this lane —
e.g. *"user prefers short answers"* or *"for technical questions, always provide
a code block"*. The write source must be exclusively `observation` (analogous to
the `authorization` lane), so rules cannot originate from the manipulable
conversation flow (prompt-injection protection).

**The fundamental difference — and the actual problem:**

The `authorization` lane is safe precisely because it is **never** injected into
the prompt. The `procedural` lane is the opposite: its entire purpose is to steer
behavior — it **must** be injected. This creates an **autonomous feedback loop**:
the agent changes its own behavior without the human seeing it.

### The drift failure mode (must be designed against)

- Session 1: agent learns *"user prefers short answers"* → rule written.
- Session 5: agent learns *"for technical questions, always include a code block"* → rule written.
- Session 12: the two rules **interact**; behavior is no longer what was wanted —
  even though **each rule individually was legitimate**.

Each rule alone is a valid `observation`; **accumulated**, it is silent
behavioral drift, only noticed once something goes wrong. This is exactly how
"procedural memory" repeatedly fails in research. Two contradicting **behavior
rules** are more dangerous than two contradicting **facts**.

---

## 3. Hard Constraints (non-negotiable)

Every proposed solution MUST satisfy these:

1. **stdlib-only / dependency-free.** No external package, no embeddings, no LLM
   calls inside the memory core. Pure SQLite + Python standard library.
2. **Mandatory review-gate, no auto-approve.** Rules are created in `pending`
   status and become active/injectable only after an **explicit** human
   `approve_rule(id)` — **regardless of how high the confidence is**. The gate is
   core design, not an option. (Rationale: it is the only thing that keeps the
   autonomous loop controllable.)
3. **Source restricted to `observation` only.** Reuse the v3.4 source-trust
   matrix; `conversation/inference/tool/external` must **not** be allowed to
   write `procedural`.
4. **Rule-conflict system.** Analogous to the existing fact-conflict detection,
   but for behavior rules — including a working notion of what makes two rules
   "contradictory" or "interacting".
5. **Auditability.** Every rule must be fully traceable via the existing
   provenance/audit log (when, which trigger, which source).
6. **Bounded, separated injection.** If injected: a dedicated, budgeted prompt
   block (e.g. `## Rules`), clearly separated from evidence.

---

## 4. Wanted Solution — Deliverables

Provide a **design document** (not a code dump) that answers and justifies the
following open questions:

1. **Write trigger:** How exactly is a rule created? "Agent writes its own rule"
   + `observation`-only requires an **explicit, non-conversational** path
   (tool/CLI), not free-form LLM writing. Define this mechanism concretely. When
   is a trigger fired, and what does the rule text derive from?
2. **Review-gate lifecycle:** A full state machine
   (`pending → approved → active → (superseded/retired)`), including rejection,
   re-review after edit, and what happens to expired/unused rules.
3. **Rule-conflict model:** When do two behavior rules contradict each other? How
   do you detect **interaction** (not just direct contradiction) without an LLM
   and without embeddings? What tags/structure does a rule need so this can be
   deterministic? What is the resolution strategy?
4. **Drift protection:** A concrete mechanism against the session-1/5/12
   scenario. E.g. per-domain rule budget, sunset/decay, periodic re-approval,
   rule-sets as a unit. Argue why your mechanism actually makes accumulation
   drift visible/controllable.
5. **Injection strategy:** How/where/how-much is injected, and how you address the
   poisoning attack surface (this is the only observation-sourced lane that gets
   injected → highest-value attack target).
6. **Data model:** Which columns/tables (additive, no breaking migration —
   `authority_class` is already a free-text field), and how it integrates with
   the audit-log/provenance/conflict infrastructure.

**Answer format:** structured Markdown, with (a) the proposal, (b) rationale per
decision, (c) explicitly named trade-offs and risks, (d) at least one worked
example including one drift case and one conflict case.

---

## 5. Optional Sub-Task (especially valuable for a swarm)

The design is currently **deliberately deferred** because **real usage data is
missing**: we do not empirically know *which* rules an agent would actually write
in practice. Designing against invented examples is speculation.

If you can run as a swarm: **generate the missing data.** Simulate realistic,
diverse agent sessions (different user types, tasks, languages) and record:
- Which behavior rules would plausibly arise as an `observation`?
- Which of them collide/interact across multiple sessions?
- What would a real drift case look like concretely?

Deliver this as a **corpus of example rules + conflict/drift cases**. This corpus
is the basis for defining the gate and conflict logic from Section 4 in a
data-driven rather than abstract way.

---

## 6. Evaluation Criteria (how proposals are compared)

A good proposal …
- … satisfies **all** hard constraints from Section 3 (knock-out criterion).
- … makes behavioral drift **visible and controllable**, rather than just
  deferring it.
- … needs **no** embeddings/LLM/external deps for conflict & drift detection.
- … is additive to the existing schema (no breaking migration).
- … honestly names its own weaknesses.

---

## 7. Out of Scope

- **No** finished implementation / no PR. Design + data first; plan and code come
  later, separately.
- **No** loosening of the stdlib-only principle (no "just use an embedding
  model").
- **No** auto-approve loophole ("auto-active above confidence X" is explicitly
  forbidden).

---

## Appendix: Repo references (only if you have access)

- Lane policies: `memory/agent-memory/src/memory.py` → `_AUTHORITY_POLICY`
- Audit/provenance: `_audit()`, `get_provenance()` (same file)
- Fact conflicts (the template to mirror): `get_conflicts()` / `resolve_conflict()` (same file)
- Tracking issue: `xMannixx/agent-memory-skill#30`
- Roadmap placement: `ROADMAP.md` (Backlog, `tier:exploratory`)
