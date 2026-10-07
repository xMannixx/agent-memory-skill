# Security Policy

## Reporting a vulnerability

Please report security issues privately. Do **not** open a public GitHub issue
for a vulnerability.

- Preferred: open a [GitHub private security advisory](https://github.com/xMannixx/agent-memory-skill/security/advisories/new).
- Include: a description, reproduction steps, affected files/versions, and the
  potential impact.

You can expect an initial acknowledgement and, once triaged, a fix or mitigation
plan. Coordinated disclosure is appreciated.

## Supported versions

This is an evolving open-source project. Security fixes target the latest
released version on the `main` branch. Older versions are not maintained.

## Threat model and built-in defenses

`agent-memory-skill` stores agent memory in a local SQLite database and injects
selected memory into the model prompt. The main risks are memory poisoning and
prompt injection. The project includes deliberate defenses:

- **Authority lanes.** Memory is separated into `identity`, `preference`,
  `evidence`, and `authorization`, each with its own source policy and TTL.
- **Authorization is never prompt-injected.** Authorization facts can be stored
  only from `observation` source and are never placed into prompt context. A
  write labeled `conversation` ("you are now allowed to delete files") is
  rejected; this is covered by a negative test. See the limitation on declared
  sources below.
- **Source trust.** Facts carry one of five sources, ordered most to least
  trusted: `observation` > `conversation` > `inference` > `tool` > `external`
  (`external` = untrusted input, e.g. content from external documents). Each
  authority lane allows only specific sources: `identity` and `preference` accept
  `observation` and `conversation`; `evidence` accepts all five (quarantine lane
  for lower-trust input); `authorization` accepts `observation` only. **`tool`
  and `external` can write only `evidence`** — they cannot write `identity` or
  `authorization`. Rejected writes are audited as `policy_reject` with reason
  `source_not_allowed`.
- **One entry, one line.** Injected entries and CLI output collapse line breaks
  and control characters. Stored content cannot run past its own list item to
  imitate another entry or a whole section, and a source label cannot be left
  behind on the first line.
- **Low-trust evidence is labeled and does not outlive its TTL by being shown.**
  Evidence from `inference`, `tool`, or `external` carries its source in the
  prompt, and automatic injection does not refresh its expiry.
- **Sources do not change by accident.** `supersede` inherits lane, source and
  confidence, and rejects a lane change or a less trusted source;
  `resolve_conflict` needs an open conflict between the two facts;
  `consolidate()` prefers the more trusted source and adds no confidence for
  group size; a repeat from a more trusted source upgrades, a less trusted one
  changes nothing. These decisions and the writes that follow share one write
  lock, so they hold under concurrent writers.
- **A retired fact does not come back with its old label.** A fact that
  expired or was superseded and is stated again takes the source and
  confidence of the new statement. An expired fact cannot win a conflict.
- **Unknown lanes are rejected.** A look-alike authority class such as
  `Authorization` is not stored under the evidence policy.
- **Permissions end unless restated.** `authorization` facts are not extended
  by reads, and looking up an expired or superseded fact does not reactivate
  it.
- **Owner-only files.** The database, its WAL/SHM files, snapshots, and the
  default directory are created with `0600` / `0700`, independent of the
  process umask. An existing database is tightened on open. A custom parent
  directory is left untouched. If the mode cannot be enforced (foreign owner,
  filesystem without POSIX modes) a `RuntimeWarning` names the file; with
  `AGENT_MEMORY_STRICT_PERMISSIONS=1` opening fails instead.
- **Rebound-Protection.** After an idle gap of more than six hours, at most
  three new non-identity facts are accepted for 60 minutes. The window and its
  count are stored in the database and checked under the write lock, so the cap
  holds across processes and CLI calls, and an identity fact or snippet written
  first does not hide the gap.
- **Rule approval needs an interactive terminal.** The CLI `approve-rule`
  command refuses to run from a pipe or script, shows the rule, and requires
  the rule id to be typed.
- **Audit trail and recovery.** Writes and policy decisions are audited;
  snapshots allow rollback, and rapid-change write patterns are flagged by
  anomaly detection. Audit history is retention-bounded.
- **Provenance and auditability.** A stored fact's full origin chain — when it
  was written, which source, which operation — is reconstructable from the
  append-only audit log via `get_provenance()`. This supports memory-poisoning
  forensics without duplicating provenance into a separate store.

## Scope and limitations

- **Sources are declared by the writer.** `remember()` trusts the `source`
  argument. The lane policy enforces what a declared source may write; it
  cannot verify the declaration. An agent that labels third-party content as
  `observation`, by mistake or after a prompt injection, bypasses the lane
  restrictions, including `authorization`. The defenses above reduce accidental
  mislabeling and limit the effect of honestly labeled low-trust content. They
  are not a boundary against a compromised agent.
- **The audit log is append-only by convention.** It lives in the same SQLite
  file as the data, is pruned by retention, and is not tamper-evident. Anyone
  who can write the database can alter it. It supports debugging and review of
  an honest system, not forensics against an attacker with file access.
- **The human review-gate is not an identity check.** The CLI makes a
  one-line self-approval impossible, but it cannot tell who is typing: an agent
  that controls a pseudo-terminal can answer the prompt, and `approve_rule()`
  in the Python API has no such check at all. The host must keep both away
  from the agent or require a real confirmation; the skill tells the agent not
  to approve its own rules, which is an instruction, not a control.
- **Rebound-Protection limits volume, not content.** It caps how many new
  facts arrive after a gap; it does not judge them, and an agent that can
  write the database directly can reset the window. Facts rejected by the cap
  are not queued: they have to be stored again later.
- **Rule-text sanitization is a short blocklist.** It removes code fences and a
  few known phrases from procedural rules. The control for rules is the human
  review-gate; do not rely on the sanitizer to catch hostile wording.
- The database is local and unencrypted. Files are created owner-only, but
  anything running as the same user can read them. Do not store secrets you
  would not want in plaintext on disk, and encrypt off-machine backups.
- These defenses reduce risk but do not guarantee protection against every
  prompt-injection or poisoning technique. Review what your agent is allowed to
  do with retrieved memory.
