# Failure-observer review and corrections

Reviewed on 2026-11-09 against `fe6e5e7dc3efb702194d27ac0e145ac15437fa2e`.
This patch remains observe-only: no tool blocking, retry, model call, prompt
injection, approval or sandbox change.

## Corrected false positives

1. Arbitrary UUIDs and timestamps in command arguments can select different
   resources or date ranges. They were normalized as volatile metadata, causing
   changed inputs to match. They now remain exact in argv; recognized generated
   temporary roots still normalize, and error metadata retains its existing
   normalization. A three-command regression sequence covers distinct resource
   IDs and date inputs.
2. Missing aggregated output was coerced to an empty string. That inferred equal
   errors from unavailable evidence. Missing output is now an unknown boundary;
   an observed empty output with a nonzero exit remains a valid failure (for
   example, `false`). A regression test distinguishes both.
3. Completed image/search/extension activity and compaction were ignored between
   command failures. They now conservatively break the streak, as do completed
   hook feedback, user input and review transitions. Reasoning, plans and assistant
   prose remain ignored so ordinary commentary cannot erase a real streak. The
   adapter regression covers a completed image observation and compaction after
   two failed commands.

The completed-item hook, bounded history, same-call deduplication, diagnostic
fields and turn-local storage remain in place. No command/error text or
fingerprints are added to logs.

## Remaining uncertainty

Completion order is not retry causality. Three identical concurrently launched
operations can complete consecutively and produce a diagnostic even if no retry
loop occurred. The observer has no start/admission timeline, so a diagnostic
must remain evidence to assess, not permission to block, spawn an agent, or
automatically escalate. Intentional repeated failures and three consecutive
flaky outcomes can also match.

Distinct executors with identical cwd/argv are not distinguished by this event
schema. Generated temporary-root normalization remains heuristic; unrecognized
formats can miss a loop, while recognized placeholders can merge evidence.
Unobserved filesystem changes are not progress signals. A missing diagnostic is
not evidence of progress.

## Validation status and real sandbox gate

The integrated native CLI builds, all 16 policy and four observation tests pass,
and formatting and Clippy pass in Linux CI. The three real-command standalone
policy replays also pass. The built-CLI observer scenarios below remain separate
from the handoff/read-only process suite; see
[the current evidence](harness-acceptance-runbook.md).

Before merging, run focused Rust tests and the integrated CLI in a sandbox:
distinct resource-ID/date commands must stay quiet; three identical failures
must emit one diagnostic; missing output and image/search/compaction boundaries
must break a streak; repeated successful searches must not flag. Capture a
parallel-identical-operation counterexample to label the known ambiguity.
Complete Cargo formatting/lints and the existing full acceptance gate. Keep the
PR draft until those results exist.
