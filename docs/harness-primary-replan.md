# Opt-in primary replanning advisory

Set `harness_replan_after_repeated_failure = true` to place at most one bounded
advisory into the existing primary model's next normal sampling request after a
three-matching-failure diagnostic. The default is false; the detector remains
observe-only unless explicitly configured.

The advisory is a contextual user fragment, not a new user goal or an approval.
It includes only the fixed tool family, exit status and matching count. It asks
the primary to assess parallel/intentional operations before inferring a loop
and to reconsider inputs/evidence/repair plans when appropriate. No command or
output text is copied, no tool is blocked, no agent/reviewer is spawned, and no
separate inference is launched. The primary's normal continuation still incurs
its ordinary model usage; no cost gain is claimed.

Inherited child and native review/Guardian sessions are excluded. State belongs to the originating turn, including late completions. Any later relevant completion conservatively cancels an undelivered notice, including a fourth identical result. Concurrent batches may therefore suppress an advisory rather than imply a serial retry loop. Observer and notice updates share one synchronous lock; no lock is held across history-recording awaits.
One queued
notice and a delivered bit bound retries across later failure streaks. Successful
delivery consumes the notice before that turn's next sampling request. A dropped
turn does not carry its notice into another turn. Lock poisoning suppresses this
optional advisory without authorizing anything.

The integrated native CLI builds, and its focused policy/observation tests,
Clippy, formatting and schema checks pass. End-to-end advisory delivery after
serial native failures remains unverified; the offline process suite covers
handoff and read-only enforcement. See [the acceptance runbook](harness-acceptance-runbook.md).
The added policy tests check empty state, coalescing, one notice per turn,
progress cancellation, changed errors, and fresh state in the next turn. Before enabling in production, run focused Rust
tests, configuration/schema checks, formatting/Clippy, and sandboxed native CLI
cases: default false produces no notice; true delivers one notice on three
identical failures; changed inputs/progress stays quiet; a second streak cannot
spam; distinct turns are isolated; parallel identical failures are framed as
ambiguous; no reviewer/model/sandbox/approval setting changes.

Automatic diagnostic review after further retries is deliberately not inferred
from completion ordering. Use the native independently configured review path;
an automatic escalation requires separate evidence, explicit cost configuration
and its own sandbox evaluation.
