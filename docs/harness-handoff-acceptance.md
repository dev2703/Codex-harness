# Durable context handoff: real CLI acceptance gate

Current integration, reproducible CI commands and verification evidence are in
[the acceptance runbook](harness-acceptance-runbook.md). The checkpoint below
records the earlier disconnected managed environment; its HTTP 401 observation
has since been superseded by a successful stock-CLI authentication probe.

The user selected durable context handoff as the next feature on 2026-11-09 and explicitly required real sandboxed functionality tests, beyond regression, unit or integration tests. No new feature should be described as working merely because its tests compile or pass.

## Intended functionality

Use the existing native runtime, model selection, approval policy and sandbox. Native compaction/resume already exists, including `model_post_turn_compact_threshold_percent`; do not duplicate those mechanisms or describe explicit notes as a full native session snapshot.

The new portable handoff is explicit user context:

- `codex handoff save --notes notes.json --output handoff.json` saves the goal, constraints, decisions, completed work, pending steps and verification evidence. It records repository provenance and an optional source-session reference. Saving makes no model calls.
- `codex handoff inspect handoff.json` verifies format/integrity and shows provenance/counts without printing the notes.
- `codex handoff continue handoff.json ...` starts a fresh native exec session, passing notes as user context. It uses the current model and permissions. It must not import approvals, sandbox settings, tools, secrets, or privileged instructions from a packet.

Packets are bounded and versioned. Publication is atomic and refuses to overwrite an existing path. A checksum detects accidental corruption; it is not authentication. Notes remain unverified claims until the new session rechecks their evidence. Preserve meaningful failed/unverified test status and unfinished work. A different repository HEAD must require explicit acknowledgement. Native resume remains the way to continue full existing thread history; this packet is a portable condensed handoff, not automatic memory or automatic 60% rollover.

## Required real-world acceptance

| Scenario | Observable evidence required | Current result |
| --- | --- | --- |
| Save, exit process, load in a fresh process | Original notes input removed; packet still loads; goal, pending steps and failed verification survive; file has owner-only permissions on Unix | Not run |
| Interrupted/concurrent publication and overwrite | Original packet remains intact; only one concurrent publisher succeeds; no truncated published packet | Not run |
| Corrupt, future-version, oversized and symlink inputs | Actual CLI refuses input before starting a model session | Not run |
| Repository mismatch | Actual CLI refuses continuation against changed Git HEAD unless explicitly acknowledged | Not run |
| No authority import | Unknown permission/model fields are rejected; notes remain user-level context | Not run |
| Real model diagnoses and hands off | In a temporary Python fixture repository, first real session runs failing tests, records actual failure and pending repair, saves a packet, then stops without repairing | Blocked by runtime/auth |
| Fresh real model completes the pending task | Second independent session receives only the packet plus current request, repairs the fixture, reruns real tests, and preserves the public API, sentinel and original packet; distinct thread IDs recorded | Blocked by runtime/auth |
| Sandbox remains effective | A fresh handoff session explicitly run read-only attempts one benign fixture write, receives an observable tool failure, and leaves no file; no escalation or bypass | Blocked by runtime/auth |
| Existing failure observer in a built CLI | Real command successes stay quiet, repeated identical terminal failures emit one diagnostic at the threshold, repair/changed errors break the streak; normal command evidence remains available | Not run in the modified CLI |

Offline packet/error cases should run in a fresh Bubblewrap filesystem with networking disabled and only fixture data writable. Live-model sessions must use native workspace-write or read-only sandboxes, temporary projects, current user-selected/default available models, bounded runtime, and real OpenAI authentication. Mock Responses output does not satisfy the live-model gate.

Record CLI source revision, binary version/hash, exact commands, sandbox settings, filesystem/test outcomes, distinct session IDs, elapsed time, and missing usage metrics. Keep raw fixture logs local unless reviewed for publication. Do not invent token/cost or model-performance gains.

## Work checkpoint and actual observations

Before the environment went offline, local edits were written in `/workspace/harness-work` to:

- `codex-rs/cli/src/handoff.rs` (bounded packet codec, checksummed atomic publication, user-context rendering)
- `codex-rs/cli/src/handoff_tests.rs` (filesystem/error coverage; not executed)
- `codex-rs/cli/src/handoff_cmd.rs` (save/inspect/continue adapter)
- `codex-rs/cli/src/main.rs`, `src/lib.rs`, and `Cargo.toml` (native CLI wiring)

These implementation edits were not committed or pushed before the runtime became unavailable. A request to write `scripts/harness_handoff_live.py` was in flight; completion is unconfirmed. Inspect the recovered worktree before assuming any file exists. This document preserves the intended scope and acceptance gate on GitHub, not the unverified implementation itself.

Build-space recovery trimmed downloaded dependency history while retaining the pinned V8/ICU trees, then removed unused V8-host build caches to prioritize the CLI-only target. A stock `cargo build --offline -p codex-cli --bin codex` was started in `/workspace/Codex-harness/codex-rs` with debug info/incremental compilation disabled and two build jobs. The last observed state was dependency compilation; no success or final exit was observed. Log: `/tmp/harness-stock-cli-build.log`.

The installed stock CLI (`0.167.0-alpha.4`) reported an existing ChatGPT login, but two actual read-only, no-tool probes reached OpenAI and failed with HTTP 401 / an expired access-token error, including a retry after the user reported refreshing login. A device-login flow was then started in this runtime; completion has not been verified. An expired device code must not be reused.

The attached cloud environment subsequently became unavailable. Status reported `connectivity: offline`, despite desired/observed phase `running`. Retained diagnostic metadata returned no records; this does not establish a cause. No restart/request-changes tool is available in this thread.

**Gate: blocked, not passed.** Restore/reconnect the execution environment, inspect the uncommitted worktree and build logs, verify login with a real no-tool probe, finish the native build, and execute every scenario above. Keep PR #1 draft until its existing and new feature gates pass.
