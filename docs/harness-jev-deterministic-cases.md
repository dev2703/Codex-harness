# Jev engineering: deterministic reference cases

Requested by the user on 2026-11-09. Reviewed source: [eugeniughelbur/jev-engineering](https://github.com/eugeniughelbur/jev-engineering), pinned at `412adce16f9326de70b9bae469328c21e26ca498`.

Sources reviewed: `jev_gate.py` (`hard_rule`, `fast_path`, `decide`), `tests/test_order.py`, README and MIT license. Upstream's benchmark numbers are its reported results, not measurements of this fork.

## Adopted scope

Use Jev's deterministic rule-ordering and command-composition cases as offline reference checks. The Jev model call is probabilistic and uses TypeSafe/OpenRouter; it is not a deterministic oracle and is not being added to this OpenAI-native runtime. Do not introduce another model call for known deterministic cases.

Hard denials precede fast-path classification in the reference. Classify complete commands rather than assuming that an innocuous first command makes a compound command safe. Exercise quoting, substitutions, pipes, redirects and multiline input explicitly. A missing key or network failure must never be interpreted as a new approval in Codex.

This is an evaluation reference, not a change to Codex permissions. A Jev label does not establish what native Codex should permit or deny under a user's particular policy. In particular, a command named `test` or `npm run test` can execute arbitrary project scripts; do not turn upstream's broad fast path into a native auto-approval rule.

## Deterministic evaluation cases

The companion JSON fixtures contain command strings for classification only. They must not execute those command strings. Pin upstream policy/config, isolate HOME, remove TypeSafe/OpenRouter keys, disable network, and make any model function invocation fail the offline run.

- Verify deny-before-fast-path precedence using secret-path-shaped _strings_, without reading actual credentials.
- Verify that changed command arguments and shell structure do not inherit a safe prefix's classification.
- Verify that a chain of read-only steps has the expected reference classification, while redirects to real files and substitutions do not receive a shortcut.
- Keep shell-classification checks distinct from repeated-failure detection: repeated successful reads are not loops, changing errors and repaired tests break failure streaks.

For durable handoff, use direct filesystem/CLI assertions for checksums, versioning, atomic publication, pending work and failed verification status. Jev classification is not a substitute for those checks or for fresh-session model continuity.

## Actual sandbox acceptance remains required

After offline classification checks, run the built fork in isolated fixture workspaces. Verify actual tool exit codes, filesystem changes and permission enforcement, then run the two independent real-model handoff sessions specified in `harness-handoff-acceptance.md`. Label model decisions and latency as empirical observations, not deterministic guarantees.

**Execution status:** all 11 pinned cases and nine guard tests pass inside the
integrated offline Bubblewrap suite, with zero model calls and zero command
strings executed. Native handoff process checks and fixed read-only enforcement
also pass. Live behavior of the fork remains a separate gate; see
[the current evidence](harness-acceptance-runbook.md).

## Attribution

The reference project is MIT-licensed, copyright (c) 2026 Eugeniu Ghelbur. This document references its approach and test concepts. If its code or substantial test content is vendored later, include the complete upstream copyright and MIT permission notice with that copy. No upstream code is vendored by this change.
