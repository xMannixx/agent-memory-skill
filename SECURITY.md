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
  only from `observation` source and are never placed into prompt context, so a
  conversational "you are now allowed to delete files" cannot escalate
  privileges through memory. This is covered by a negative test.
- **Source trust.** Facts carry one of five sources, ordered most to least
  trusted: `observation` > `conversation` > `inference` > `tool` > `external`
  (`external` = untrusted input, e.g. content from external documents). Each
  authority lane allows only specific sources: `identity` and `preference` accept
  `observation` and `conversation`; `evidence` accepts all five (quarantine lane
  for lower-trust input); `authorization` accepts `observation` only. **`tool`
  and `external` can write only `evidence`** — they cannot write `identity` or
  `authorization`. Rejected writes are audited as `policy_reject` with reason
  `source_not_allowed`.
- **Rebound-Protection.** Memory intake is capped after idle phases to limit
  flooding.
- **Audit trail and recovery.** Writes and policy decisions are audited;
  snapshots allow rollback, and rapid-change write patterns are flagged by
  anomaly detection. Audit history is retention-bounded.
- **Provenance and auditability.** A stored fact's full origin chain — when it
  was written, which source, which operation — is reconstructable from the
  append-only audit log via `get_provenance()`. This supports memory-poisoning
  forensics without duplicating provenance into a separate store.

## Scope and limitations

- The database is local and unencrypted. Protect it with normal filesystem
  permissions. Do not store secrets you would not want in plaintext on disk.
- These defenses reduce risk but do not guarantee protection against every
  prompt-injection or poisoning technique. Review what your agent is allowed to
  do with retrieved memory.
