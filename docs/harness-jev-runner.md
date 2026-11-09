# Offline Jev reference evaluator

The runnable evaluator is `scripts/harness_jev_reference.py`. It tests the pinned Jev default deterministic functions on command **strings**; it does not execute those strings, install a permission hook, or change native Codex approvals.

Fetch the reference separately, then run from this repository:

```sh
git clone https://github.com/eugeniughelbur/jev-engineering.git /tmp/jev-reference
git -C /tmp/jev-reference fetch origin 412adce16f9326de70b9bae469328c21e26ca498
python3 scripts/test_harness_jev_reference.py
python3 scripts/harness_jev_reference.py --reference /tmp/jev-reference

```


The evaluator itself never fetches a branch or reaches a provider. It reads `jev_gate.py` and `policy.json` from the exact committed objects, not from potentially edited working-tree files. The source revision is fixed in code and must agree with the fixture.

## Isolation and scope

Linux Bubblewrap is mandatory. A fresh user/network namespace has only loopback, an empty HOME and a cleared environment with no provider keys. The worker checks that its network namespace differs from the parent. Read-only mounts contain the Python runtime and runner; writable storage is ephemeral. Sandbox setup failure exits nonzero; there is no unisolated fallback.

The worker does not import the Jev package or run its configuration loader. It compiles only `hard_rule`, `_one`, `fast_path`, their four literal regex constants, and the validated first two branches of `decide`. This tests the actual deterministic prefix and deny-before-fast-path order without loading any model branch, environment lookup, hook, log writer or network client. A changed source shape fails rather than silently expanding the allowed scope.

JSON reports include the pinned revision, source hash, per-case verdicts, observed namespace isolation and zero model/command executions. Ambiguous cases remain no deterministic answer. A reference `allow` is not a native Codex approval. Upstream's broad fast paths, including names of test/build scripts, must not become automatic permission grants in this fork.

Timeouts, missing dependencies, inaccessible Git objects, malformed fixtures and isolation failure are evaluation errors. None count as passing cases. The runner is bounded to 1,000 cases and 16 KiB per command string.

## Verification status

Source prepared while the managed runtime is unavailable/starting. No execution result is claimed in this document. Run the two commands above in the recovered environment and record exact results before changing that status. These offline reference checks remain separate from real built-Codex sandbox and live-model handoff acceptance.

## Attribution

Reference project: eugeniughelbur/jev-engineering, commit 412adce16f9326de70b9bae469328c21e26ca498, MIT license, copyright (c) 2026 Eugeniu Ghelbur. No upstream source is vendored here; it is read from a separately obtained pinned checkout. The evaluator's tests use a small independently written synthetic source fixture only to check extraction guards and never-call-model behavior.
