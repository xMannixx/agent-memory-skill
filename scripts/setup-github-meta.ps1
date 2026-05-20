param(
    [string]$Repo = "xMannixx/agent-memory-skill"
)

$ErrorActionPreference = "Stop"

function Invoke-GhJson {
    param([string[]]$Arguments)
    $output = & gh @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "gh command failed: gh $($Arguments -join ' ')"
    }
    if ([string]::IsNullOrWhiteSpace($output)) {
        return $null
    }
    return $output | ConvertFrom-Json
}

function Ensure-Label {
    param(
        [string]$Name,
        [string]$Color,
        [string]$Description,
        [object[]]$ExistingLabels
    )

    if ($ExistingLabels | Where-Object { $_.name -eq $Name }) {
        Write-Host "LABEL exists: $Name"
        return
    }

    & gh label create $Name --repo $Repo --color $Color --description $Description | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create label: $Name"
    }
    Write-Host "LABEL created: $Name"
}

function Ensure-Milestone {
    param(
        [string]$Title,
        [string]$Description,
        [object[]]$ExistingMilestones
    )

    $existing = $ExistingMilestones | Where-Object { $_.title -eq $Title } | Select-Object -First 1
    if ($existing) {
        Write-Host "MILESTONE exists: $Title"
        Write-Host "MILESTONE|$Title|$($existing.html_url)"
        return
    }

    $created = Invoke-GhJson @(
        "api",
        "-X", "POST",
        "repos/$Repo/milestones",
        "-f", "title=$Title",
        "-f", "description=$Description"
    )
    Write-Host "MILESTONE created: $Title"
    Write-Host "MILESTONE|$Title|$($created.html_url)"
}

function Ensure-Issue {
    param(
        [hashtable]$Issue
    )

    $query = "$($Issue.Title) in:title repo:$Repo"
    $existingIssues = Invoke-GhJson @(
        "issue", "list",
        "--repo", $Repo,
        "--state", "all",
        "--search", $query,
        "--json", "number,title,url",
        "--limit", "100"
    )
    $existing = $existingIssues | Where-Object { $_.title -eq $Issue.Title } | Select-Object -First 1
    if ($existing) {
        Write-Host "ISSUE exists: #$($existing.number) $($Issue.Title)"
        Write-Host "ISSUE|$($existing.number)|$($existing.url)|$($Issue.Title)"
        return
    }

    $args = @(
        "issue", "create",
        "--repo", $Repo,
        "--title", $Issue.Title,
        "--body", $Issue.Body,
        "--label", ($Issue.Labels -join ",")
    )

    if ($Issue.Milestone) {
        $args += @("--milestone", $Issue.Milestone)
    }

    $url = & gh @args
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create issue: $($Issue.Title)"
    }

    $number = ($url -split "/")[-1]
    Write-Host "ISSUE created: #$number $($Issue.Title)"
    Write-Host "ISSUE|$number|$url|$($Issue.Title)"
}

& gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "gh is not authenticated. Run: gh auth login"
}

$labels = @(
    @{ Name = "tier:must-do"; Color = "d73a4a"; Description = "Correctness, data integrity, or security work that should happen early." },
    @{ Name = "tier:high-value"; Color = "fbca04"; Description = "Important product or architecture improvements with strong payoff." },
    @{ Name = "tier:exploratory"; Color = "a2eeef"; Description = "Larger design work that needs careful evaluation." },
    @{ Name = "tier:polish"; Color = "cfd3d7"; Description = "Maintainability, scale, testing, or usability improvements." },
    @{ Name = "area:core"; Color = "0e8a16"; Description = "Core AgentMemory implementation." },
    @{ Name = "area:plugin"; Color = "5319e7"; Description = "Hermes plugin behavior and injection strategy." },
    @{ Name = "area:cli"; Color = "006b75"; Description = "Command-line interface." },
    @{ Name = "area:schema"; Color = "1d76db"; Description = "SQLite schema, migrations, indexes, and persistence." },
    @{ Name = "area:search"; Color = "0052cc"; Description = "Full-text, semantic, or hybrid retrieval." },
    @{ Name = "area:security"; Color = "b60205"; Description = "Memory poisoning, prompt injection, audit, and recovery." },
    @{ Name = "area:tests"; Color = "bfdadc"; Description = "Tests, fixtures, and verification infrastructure." },
    @{ Name = "area:docs"; Color = "0075ca"; Description = "Documentation and roadmap work." },
    @{ Name = "type:feature"; Color = "84b6eb"; Description = "New capability." },
    @{ Name = "type:bug"; Color = "d73a4a"; Description = "Incorrect behavior or regression." },
    @{ Name = "type:perf"; Color = "f9d0c4"; Description = "Performance or scalability work." },
    @{ Name = "type:refactor"; Color = "c5def5"; Description = "Internal structure change without intended behavior change." },
    @{ Name = "type:test"; Color = "e4e669"; Description = "Test coverage or verification work." },
    @{ Name = "type:breaking"; Color = "000000"; Description = "Potentially changes compatibility or migration expectations." }
)

$milestones = @(
    @{ Title = "v1.2 - Stability & Hygiene"; Description = "Make the current SQLite memory core predictable, indexed, testable, and easier to maintain." },
    @{ Title = "v1.3 - Audit & Recovery"; Description = "Add auditability, rollback, anomaly detection, and operational stats." },
    @{ Title = "v1.4 - Consolidation & Decay"; Description = "Move from simple retention windows toward consolidation and confidence decay." },
    @{ Title = "v2.0 - Smart Retrieval"; Description = "Add recall memory, hybrid search, and smarter plugin injection." }
)

$issues = @(
    @{
        Title = "feat(core): introduce recall lane for raw conversation snippets"
        Milestone = "v2.0 - Smart Retrieval"
        Labels = @("tier:exploratory", "area:core", "area:schema", "type:feature")
        Body = @'
## Why

Facts are useful, but some context should remain as raw conversation recall before it is distilled into semantic memory. This follows the memory hierarchy used by systems like Letta/MemGPT: core memory, archival memory, and recall memory.

## Scope

- [ ] Add a `recall` or `conversation_snippets` storage lane.
- [ ] Preserve timestamp, source, and optional session metadata.
- [ ] Add a read API that searches snippets without auto-injecting them by default.
- [ ] Add focused tests for write, search, and cleanup behavior.

## Out of scope

- Full transcript storage for every turn.
- Vector search or hybrid retrieval.
- Plugin auto-injection changes.

## Acceptance criteria

- [ ] Raw snippets can be stored and searched separately from facts.
- [ ] Existing facts, lessons, and entities remain compatible.
- [ ] The default plugin context stays compact.

## References

- https://agentmarketcap.ai/blog/2026/04/08/agent-long-term-memory-architecture-letta-memgpt-langmem-zep
'@
    },
    @{
        Title = "feat(cli): consolidate command to merge related facts"
        Milestone = "v1.4 - Consolidation & Decay"
        Labels = @("tier:high-value", "area:core", "area:cli", "type:feature")
        Body = @'
## Why

Memory will accumulate repeated or near-duplicate observations over time. A consolidation pass can turn repeated evidence into stronger, cleaner facts.

## Scope

- [ ] Add `fact.py consolidate`.
- [ ] Group candidate facts by authority lane, tags, and entity links.
- [ ] Create consolidated facts while superseding old ones.
- [ ] Report what changed before/after the pass.

## Out of scope

- Semantic embedding-based clustering.
- Fully autonomous background consolidation.

## Acceptance criteria

- [ ] The command is deterministic and test-covered.
- [ ] Original facts are not hard-deleted.
- [ ] Superseded facts remain inspectable.

## References

- https://arxiv.org/abs/2604.16839
'@
    },
    @{
        Title = "feat(search): hybrid FTS5 + sqlite-vec retrieval with RRF"
        Milestone = "v2.0 - Smart Retrieval"
        Labels = @("tier:exploratory", "area:search", "type:feature")
        Body = @'
## Why

FTS5 is strong for exact terms, but it misses synonyms and conceptual matches. Hybrid retrieval can combine keyword ranking with semantic similarity while keeping the local-first SQLite model.

## Scope

- [ ] Evaluate sqlite-vec as an optional dependency.
- [ ] Store embeddings alongside facts using shared rowids.
- [ ] Merge FTS5 and vector results with reciprocal rank fusion.
- [ ] Provide a fallback path when vector search is unavailable.

## Out of scope

- Hosted vector databases.
- Mandatory cloud embedding providers.

## Acceptance criteria

- [ ] Existing FTS-only behavior still works.
- [ ] Hybrid search is opt-in or gracefully feature-detected.
- [ ] Ranking tests cover keyword-only, semantic-only, and mixed queries.

## References

- https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html
- https://simonwillison.net/2024/Oct/4/hybrid-full-text-search-and-vector-search-with-sqlite
'@
    },
    @{
        Title = "feat(security): audit log, snapshots and rapid-change anomaly detection"
        Milestone = "v1.3 - Audit & Recovery"
        Labels = @("tier:must-do", "area:security", "area:schema", "type:feature")
        Body = @'
## Why

Persistent agent memory needs a forensic trail. If memory poisoning happens, users need to know what changed, when it changed, and how to roll back.

## Scope

- [ ] Add append-only memory audit entries for writes, deletes, supersedes, and policy rejects.
- [ ] Add content hashes for audit integrity.
- [ ] Add snapshot and restore helpers using SQLite backup support.
- [ ] Add simple rapid-change anomaly detection.

## Out of scope

- ML-based anomaly detection.
- External SIEM integrations.

## Acceptance criteria

- [ ] Every memory mutation creates an audit entry.
- [ ] Snapshots can be created and restored in tests.
- [ ] A burst of writes can be detected and reported.

## References

- https://owasp.org/www-project-agent-memory-guard/
- https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html
'@
    },
    @{
        Title = "feat(plugin): query-aware retrieval at second turn onward"
        Milestone = "v2.0 - Smart Retrieval"
        Labels = @("tier:exploratory", "area:plugin", "type:feature")
        Body = @'
## Why

The plugin currently injects fixed top-N memory blocks. Query-aware retrieval can keep the first turn compact while pulling relevant evidence later in the session.

## Scope

- [ ] Keep identity facts in first-turn context.
- [ ] Use the current user message to retrieve relevant evidence on later turns.
- [ ] Add configurable limits per lane.
- [ ] Preserve the existing simple first-turn behavior as a safe baseline.

## Out of scope

- Full tool-based autonomous retrieval.
- Hybrid vector search.

## Acceptance criteria

- [ ] Evidence injection is relevant to the user message.
- [ ] Token budget stays bounded.
- [ ] Tests cover first-turn and later-turn behavior.

## References

- https://mem0.ai/blog/ai-memory-management-for-llms-and-agents
'@
    },
    @{
        Title = "feat(core): exponential confidence decay per authority lane"
        Milestone = "v1.4 - Consolidation & Decay"
        Labels = @("tier:high-value", "area:core", "area:schema", "type:feature", "type:breaking")
        Body = @'
## Why

Hard TTLs create cliffs. Confidence decay makes memory fade gradually and lets frequently used facts remain useful without relying only on expiration timestamps.

## Scope

- [ ] Define half-life settings per authority lane.
- [ ] Add effective confidence calculation.
- [ ] Update recall filters to consider effective confidence.
- [ ] Decide how decay interacts with `forget_stale()`.

## Out of scope

- Semantic deduplication.
- Automatic contradiction resolution.

## Acceptance criteria

- [ ] Active facts retain useful effective confidence.
- [ ] Unused facts fade below recall thresholds over time.
- [ ] Migration behavior is documented.

## References

- https://arxiv.org/abs/2603.07670v1
'@
    },
    @{
        Title = "fix(core): make remember idempotent via content-hash IDs"
        Milestone = "v1.2 - Stability & Hygiene"
        Labels = @("tier:must-do", "area:core", "type:bug", "type:breaking")
        Body = @'
## Why

The same fact can currently be stored multiple times because IDs include timestamps. Idempotent writes reduce duplicate memory and make repeated observations strengthen existing facts.

## Scope

- [ ] Generate stable IDs from normalized content and authority metadata.
- [ ] Update existing rows on duplicate content instead of inserting duplicates.
- [ ] Increment access/write counters on repeat writes.
- [ ] Document migration and compatibility behavior.

## Out of scope

- Fuzzy semantic deduplication.
- Entity-level conflict detection.

## Acceptance criteria

- [ ] Rewriting the same fact does not create duplicates.
- [ ] Existing tests still pass.
- [ ] A migration path is documented.
'@
    },
    @{
        Title = "feat(schema): namespaces for multi-user and multi-agent memory"
        Milestone = $null
        Labels = @("tier:polish", "area:schema", "type:feature")
        Body = @'
## Why

The current database is flat. Namespaces make the skill safer for multiple users, agents, projects, or personas without requiring separate database files.

## Scope

- [ ] Add namespace support to facts, lessons, and entities.
- [ ] Add `AgentMemory(namespace=...)`.
- [ ] Ensure reads and writes filter by namespace by default.
- [ ] Define migration behavior for existing data.

## Out of scope

- User authentication.
- Cross-machine sync.

## Acceptance criteria

- [ ] Existing data is assigned a default namespace.
- [ ] Namespaced reads do not leak other namespaces.
- [ ] CLI supports namespace selection.
'@
    },
    @{
        Title = "feat(core): self-observability stats and latency counters"
        Milestone = "v1.3 - Audit & Recovery"
        Labels = @("tier:polish", "area:core", "area:cli", "type:feature")
        Body = @'
## Why

Memory quality and performance are hard to manage without basic counters. Stats should show how often memory is used, how expensive recall is, and whether policies are balanced.

## Scope

- [ ] Track recall and write counts.
- [ ] Track simple recall latency stats.
- [ ] Add stale, superseded, and by-lane ratios to `stats()`.
- [ ] Print the new fields in the CLI.

## Out of scope

- External metrics exporters.
- Full tracing.

## Acceptance criteria

- [ ] Stats are cheap to collect.
- [ ] CLI output remains readable.
- [ ] Tests cover counter updates.
'@
    },
    @{
        Title = "perf(schema): add facts and lessons indexes and enable WAL"
        Milestone = "v1.2 - Stability & Hygiene"
        Labels = @("tier:must-do", "area:schema", "type:perf")
        Body = @'
## Why

The schema filters and sorts by authority class, expiry, access time, supersession, and lesson outcome. Indexes and WAL mode are low-risk improvements before the database grows.

## Scope

- [ ] Add indexes for common fact filters.
- [ ] Add lesson indexes for outcome and created time.
- [ ] Enable `PRAGMA journal_mode=WAL` for file-backed databases.
- [ ] Avoid changing `:memory:` test behavior.

## Out of scope

- Schema redesign.
- Vector search.

## Acceptance criteria

- [ ] Index creation is idempotent.
- [ ] Tests pass for `:memory:` and file-backed databases.
- [ ] WAL does not break CLI usage.
'@
    },
    @{
        Title = "feat(core): lesson and entity lifecycle with decay on use"
        Milestone = "v1.2 - Stability & Hygiene"
        Labels = @("tier:polish", "area:core", "area:schema", "type:feature")
        Body = @'
## Why

Lessons and entities currently grow without lifecycle rules. They need retention, access tracking, and predictable cleanup similar to facts.

## Scope

- [ ] Add last-accessed metadata for lessons and entities.
- [ ] Refresh lessons when `apply_lesson()` is called.
- [ ] Define default retention behavior.
- [ ] Add cleanup tests.

## Out of scope

- Full historical attribute versioning.
- Semantic entity merge.

## Acceptance criteria

- [ ] Lessons/entities do not grow unbounded by default.
- [ ] Active lessons/entities survive cleanup.
- [ ] Existing data migrates safely.
'@
    },
    @{
        Title = "feat(core): finer source trust graduation and promotion rules"
        Milestone = $null
        Labels = @("tier:polish", "area:core", "type:feature")
        Body = @'
## Why

The current source set is useful but broad. Finer source categories can distinguish user conversation, agent inference, tool output, and untrusted external documents.

## Scope

- [ ] Split conversation and observation sources into more precise categories.
- [ ] Define which sources may write to each authority lane.
- [ ] Add promotion rules for repeated verification.
- [ ] Document source trust semantics.

## Out of scope

- Full reputation scoring.
- External trust registries.

## Acceptance criteria

- [ ] Authorization remains observation-only or stronger.
- [ ] External documents cannot write identity or authorization facts.
- [ ] Tests cover source-policy rejects.
'@
    },
    @{
        Title = "feat(plugin): token budget, lazy tool retrieval, authorization lane decision"
        Milestone = "v2.0 - Smart Retrieval"
        Labels = @("tier:exploratory", "area:plugin", "type:feature")
        Body = @'
## Why

The plugin needs explicit token budgeting and a documented decision about whether authorization memory should ever be injected into prompts.

## Scope

- [ ] Add configurable per-lane token budgets.
- [ ] Decide and document authorization-lane injection behavior.
- [ ] Explore lazy retrieval as a tool path instead of prompt injection.
- [ ] Add tests for budget clipping.

## Out of scope

- Hybrid vector retrieval.
- Multi-agent namespaces.

## Acceptance criteria

- [ ] Plugin context size is bounded.
- [ ] Authorization behavior is explicit and tested.
- [ ] Existing first-turn injection still works.
'@
    },
    @{
        Title = "test(infra): file-DB coverage, property tests, frozen-time helpers"
        Milestone = "v1.2 - Stability & Hygiene"
        Labels = @("tier:polish", "area:tests", "type:test")
        Body = @'
## Why

Most tests use `:memory:`. File-backed behavior, migrations, time-sensitive retention, and policy invariants need stronger coverage.

## Scope

- [ ] Add file-backed database fixtures.
- [ ] Add property tests for policy accept/reject behavior.
- [ ] Add frozen-time helpers for TTL and rebound tests.
- [ ] Cover migrations on existing SQLite files.

## Out of scope

- End-to-end Hermes plugin tests.
- Performance benchmarking.

## Acceptance criteria

- [ ] Both in-memory and file-backed paths are tested.
- [ ] Time-based tests do not rely on manual SQL patching where avoidable.
- [ ] Migration tests cover old schema shapes.
'@
    },
    @{
        Title = "refactor(core): small hygiene umbrella"
        Milestone = "v1.2 - Stability & Hygiene"
        Labels = @("tier:polish", "area:core", "type:refactor")
        Body = @'
## Why

Several small correctness and maintainability issues should be cleaned up before larger features land.

## Scope

- [ ] Escape `%` and `_` in lesson LIKE queries.
- [ ] Merge entity attributes instead of replacing them in `track_entity()`.
- [ ] Avoid extra connections in entity update paths.
- [ ] Freeze authority policy or guard against accidental mutation.
- [ ] Consider modern type syntax if the minimum Python version allows it.

## Out of scope

- Major API redesign.
- Confidence decay.
- Namespace support.

## Acceptance criteria

- [ ] Behavior remains compatible unless explicitly documented.
- [ ] Tests cover the touched edge cases.
- [ ] No unrelated refactors are included.
'@
    }
)

Write-Host "Using repo: $Repo"

$existingLabels = Invoke-GhJson @("label", "list", "--repo", $Repo, "--json", "name", "--limit", "200")
foreach ($label in $labels) {
    Ensure-Label -Name $label.Name -Color $label.Color -Description $label.Description -ExistingLabels $existingLabels
}

$existingMilestones = Invoke-GhJson @("api", "repos/$Repo/milestones?state=all")
foreach ($milestone in $milestones) {
    Ensure-Milestone -Title $milestone.Title -Description $milestone.Description -ExistingMilestones $existingMilestones
}

foreach ($issue in $issues) {
    Ensure-Issue -Issue $issue
}

Write-Host "GitHub metadata bootstrap complete."
