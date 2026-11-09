# Harness V1 audit

## Pinned baseline

Repository: `dev2703/Codex-harness`. Base: `25f4940d4affcf8c60f96ffec5e3752e1f590b8a`
(2026-11-09), before harness changes. Working branch: `feat/harness-policy-v1`.
The user supplied this repository in place of the handoff's proposed `dev2703/codex`.
This is an independently maintained fork; any PR targets this repository, not upstream.
The current session cannot select or verify a model picker setting.

## Existing control flow

References below refer to the pinned baseline, not moving upstream main.

| Mechanism                      | Source                                                | Observed control flow                                                                                                                                                                                                                                                                   |
| ------------------------------ | ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tool dispatch and hooks        | `codex-rs/core/src/tools/registry.rs:729`             | `dispatch_any_with_state` prepares the invocation; pre-tool hooks may rewrite or reject it. Native tool review runs on rewritten arguments at line 894. Post-tool hooks run at line 965. A post-tool block rejects the returned result, not the already completed operation (line 987). |
| Approval and sandbox execution | `codex-rs/core/src/tools/orchestrator.rs:125`         | `ToolOrchestrator::run` owns execution attempts and existing approval/sandbox handling. A detector must not convert denial into success or initiate a retry here.                                                                                                                       |
| Command completion             | `codex-rs/core/src/tools/events.rs:515`               | `emit_exec_stage` maps completed execution output into status/exit code and calls `emit_exec_end` (line 562). A stage named `Success` can still carry a nonzero process exit code. Rejections use `Declined`, distinct from execution failure.                                          |
| Delayed unified execution      | `codex-rs/core/src/unified_exec/async_watcher.rs:331` | `emit_exec_end_for_unified_exec` resolves the final transcript and reuses `ToolEmitter`. Poll responses are not independent terminal failures.                                                                                                                                          |
| Shared completion events       | `codex-rs/core/src/session/mod.rs:3083`               | `emit_turn_item_completed_with_metadata` notifies lifecycle contributors and emits the completed item. Command and patch completion pass through this method.                                                                                                                           |
| Turn-local data                | `codex-rs/ext/extension-api/src/state.rs:101`         | `ExtensionData::get_or_init` provides typed, synchronized lazy attachment. Each turn has its own attachment map; review creates a fresh map in `session/turn_context.rs:445`.                                                                                                           |
| Tool tracing                   | `codex-rs/core/src/tools/call_trace.rs:1`             | Existing milestones intentionally contain identifiers and names, never arguments or output. New diagnostics should preserve this privacy boundary.                                                                                                                                      |
| Subagents                      | `codex-rs/core/src/agent/control/spawn.rs:198`        | Existing spawn control and child configuration already exist. `agent/child_config.rs:54` prepares config; line 222 applies requested model overrides; line 311 applies roles. `agent/role.rs:48` applies bounded role configuration. No new agent runtime is needed.                    |
| Review                         | `codex-rs/core/src/tasks/review.rs:44`                | `ReviewTask` uses existing session task machinery; `start_review_conversation` (line 98) and `process_review_events` (line 144) manage the review conversation. V1 observation does not invoke it.                                                                                      |
| Compaction                     | `codex-rs/core/src/session/turn.rs:1776`              | The turn loop calls existing inline automatic compaction. `compact.rs:118` implements that entry point; line 85 builds replacement history. V1 does not alter compaction.                                                                                                               |

Searches of `core/src` for repeated-failure, loop-detection, non-progress and
failure-fingerprint concepts found no general command failure observer. Guardian
repeated-denial handling is a separate security mechanism and must remain intact.

## Proposed narrow observation boundary

Observe terminal command items and use successful patch completion as a progress
boundary. Scope state to the originating turn, including late background process
completion. Match command identity including cwd, exit code and normalized error
signature. Distinguish changed commands, changed errors, success and declines.
Keep at most 20 observations and emit a diagnostic on the third consecutive
matching failure. Do not block, retry, modify prompts, change approvals or invoke
models. Do not emit command text, output text or secret-derived fingerprints.

## Evaluation protocol

Before source edits, run `cargo build` and a focused stock test. Use the pinned
Rust 1.95.0 toolchain from `codex-rs/rust-toolchain.toml`. Record actual failures
and missing prerequisites; a failed dependency fetch is not a passing baseline.

Replay at least these labeled cases with identical inputs against the baseline
and the observer: successful command repeated; identical failed command repeated
three times; failed test, successful repair, then test success. Include changed
command and changed error counterexamples, progress between failures, bounded
history eviction and normalization/privacy tests.

The ship gate requires focused tests, formatting and lint checks; a repeat failure
must be detected and at least two non-loop cases must stay quiet. Compare task
outcome and execution evidence. Model calls, all-agent token usage, billed cost
and accuracy gains require separate model-backed experiments; synthetic replay
does not establish those metrics. Full workspace tests are optional initially.
