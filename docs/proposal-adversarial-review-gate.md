# Proposal: adversarial review gate

The current team roles support a skeptic role, but review is workflow guidance rather than a deterministic promotion gate.

## Proposed behavior

Before promotion, run an isolated review against the candidate diff and task contract. The reviewer must inspect:

- contract invariant violations;
- unauthorized path changes;
- test gaps and tests weakened to pass;
- race, state-leak, and cleanup hazards;
- receipt/evidence inconsistencies.

The reviewer receives the diff, contract, and bounded verification evidence—not the full private session transcript. Self-approval is disallowed.

## Acceptance

A candidate cannot be marked verified when review is missing, stale, or requests changes. Review comments must be bounded and attributable to a candidate revision. A failed or unavailable reviewer remains an explicit unverified state; it must not silently become approval.

This proposal does not claim model-backed review accuracy.