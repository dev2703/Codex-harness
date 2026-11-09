# Code-mode output evidence

Base: `fe6e5e7dc3efb702194d27ac0e145ac15437fa2e` on `feat/harness-policy-v1`.

## Verified source gap

Unified execution already uses a bounded head/tail collector and carries its
omitted-byte count independently of retained text. `CapturedStream::render`
restores the omission marker if a collector supplies that count without a marker
in its text. Existing `tools/context_tests.rs` covers this state for normal/log
outputs.

However, `ExecCommandToolOutput::code_mode_result` cloned `raw_output.text`
directly when `max_output_tokens` was absent. That branch could hide collection
loss even though its metadata still recorded the omitted bytes.

## Change

The no-extra-limit code-mode branch now reuses
`CapturedStream::render(None, original_token_count)`, as the native log path does.
It adds no summaries, model calls, output storage, policy decisions or new output
budget. It preserves all retained text and makes existing collection loss
explicit. Exit status, original token count, chunk/session identifiers, sandbox
and approval behavior are unchanged.

A regression test covers omission metadata supplied without a marker, agreement
with the explicit-limit path, no duplicate marker, unchanged complete text, and
unchanged execution metadata.

## Validation gate

Source audit only at preparation: Rust compilation, focused tests and real CLI
sandbox execution have not been observed for this branch while the execution
environment is unavailable/starting. Do not describe them as passed. No token,
latency or accuracy gain is claimed.

After runtime recovery, run the focused `codex-core` context tests, formatter and
Cargo Clippy. Then exercise the built CLI's code-mode unified-exec path in a
sandbox with output exceeding the collection cap: verify the model-facing result
retains its omission notice and exit status both with omitted and explicit
`max_output_tokens`. Exercise a small complete result as a counterexample and
confirm sandbox denials remain denials. A synthetic formatter test alone does
not satisfy that real CLI gate.
