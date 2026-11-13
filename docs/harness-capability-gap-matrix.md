# Harness capability gap matrix

This audit compares the current repository with the capabilities described in the supplied posts:

- [choopyplug1](https://x.com/choopyplug1/status/2119833611499127166)
- [marfinxx](https://x.com/marfinxx/status/2118313633381745064)

The X pages were not directly fetchable in this environment; the comparison uses indexed post excerpts and the repository's current source/docs. The posts' performance claims are treated as unverified.

## Current coverage

| Capability | Current repository | Assessment |
| --- | --- | --- |
| Durable context handoff | `codex handoff save/inspect/continue`, bounded and checksummed packets, atomic publication, provenance checks | Present, with live-model gates still documented as pending |
| Failure observation | Turn-local bounded observer, repeated-failure diagnostics, changed-input/progress boundaries | Present, intentionally observe-only |
| Bounded primary replanning | Opt-in one-advisory policy after repeated failure | Present |
| Role-based multi-agent workflow | Native `codex team` roles and model/effort configuration | Present; scheduling remains model/workflow driven |
| Sandbox and approval enforcement | Existing native sandbox, approval, hook, and role inheritance paths | Present |
| Deterministic routing/evaluation | Pinned offline JEV reference runner and acceptance cases | Present for deterministic fixtures; not a general multi-model rollout engine |
| Immutable task contract | No task contract persisted before the first model/tool call | Gap |
| Ephemeral Git worktree per task | No harness-owned worktree lifecycle tied to a task | Gap |
| Circuit breaker and automatic rollback | Failure diagnostics do not block, retry, or roll back | Gap by design |
| Cryptographically authenticated receipt | Checksums detect corruption but are not signatures/authentication | Gap |
| Verified-commit/promotion gate | No receipt-backed commit gate | Gap |
| Unified context-budget telemetry | Native compaction exists, but no bounded cross-feature budget/omission policy | Gap |
| Compression-layer collision detection | No explicit single-owner policy for external compression plugins | Gap |

## Proposed tracking slices

Issues are disabled on this repository, so these are recorded here as proposed tracking items:

1. **Isolated worktree execution with immutable task contracts**
   - Versioned bounded contract with repository provenance, owned paths, invariants, checks, and acceptance evidence.
   - Ephemeral worktree creation/cleanup, interruption handling, and path-boundary acceptance tests.
   - Do not import approvals, secrets, models, or privileged instructions from the contract.

2. **Signed execution receipts and verified-commit gating**
   - Receipt fields: source/HEAD hashes, contract ID, bounded tool-event summary, test commands and exit codes, final diff hash, verification status.
   - A real signature/trust model, not a checksum mislabeled as authentication.
   - Tamper, replay, stale-HEAD, missing-receipt, and failed-test gates.
   - Keep raw prompts, command output, and secrets out of receipts.

3. **Context-budget telemetry and optional compression policy**
   - Per-turn bounded size/compaction/omission metadata without raw prompt retention.
   - Soft/hard thresholds and one compression owner.
   - Preservation of evidence pointers and omission markers across compaction and handoff.
   - No unverified claims about token, latency, or cost savings.

## Acceptance principle

Diagnostics are evidence, not authorization. A detector must not retroactively make a completed tool operation safe, and model-backed quality or cost claims require separate reproducible experiments.
