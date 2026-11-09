# Opt-in validation for role-selected subagent models

Source base: `fe6e5e7dc3efb702194d27ac0e145ac15437fa2e`.

## Problem and behavior

Codex already supports selecting primary, worker and quality-review models through
`model`, `agents.default_subagent_model`, role config files, and `review_model`.
This change does not add a second routing system or hardcode a model name.

Explicit `spawn_agent` model overrides already call `find_spawn_agent_model_name`
to validate catalog membership and compatibility with the captured multi-agent
backend. A role can replace the selected model afterward. Previously,
`apply_spawn_agent_role` skipped catalog validation, returned immediately when
reasoning effort was absent, and accepted synthetic fallback model metadata.

Enable the new check explicitly:

```toml
[features]
strict_role_model_validation = true
```

With the flag enabled, when an applied role changes the model under the built-in
OpenAI provider, the same catalog/backend validator used by explicit spawn
overrides runs before reasoning-effort handling and before the child is started.
An unavailable or incompatible role model returns the existing actionable model
selection error. Validation reads the existing offline model catalog; it does
not issue a new model request.

The flag is disabled by default. Non-OpenAI/custom providers, inherited unchanged
models, existing role precedence, sandbox/approval inheritance, service tiers and
reasoning-effort validation retain their existing behavior. This does not
validate the primary model or change `review_model`; it addresses only the
verified inconsistency in role-selected worker models.

The available catalog can be stale or incomplete. Enable the flag only when
catalog membership is the intended policy; a valid but undiscovered model can be
rejected. No attempt is made to infer provider identity from a model-name prefix.

## Source anchors

- `core/src/agent/child_config.rs`: `prepare_agent_spawn_config`,
  `apply_spawn_agent_role`, `find_spawn_agent_model_name`.
- `core/src/agent/role.rs`: bounded role application, provider and parent
  authority preservation.
- `features/src/lib.rs`: disabled-by-default `StrictRoleModelValidation`.
- `core/config.schema.json`: feature key added to both feature schema locations.

## Required validation

Run from `codex-rs` after restoring a usable build environment:

```sh
cargo test -p codex-core --lib agent::child_config::tests
cargo test -p codex-features
cargo run -p codex-config-schema --bin codex-write-config-schema
just fmt
just fix -p codex-core
just test -p codex-core
```

Confirm that schema generation produces no unexpected diff. The focused tests
cover the feature's default/parse contract, provider/inheritance compatibility,
known and unknown catalog entries, and multi-agent backend eligibility.

## Real sandbox acceptance matrix

Use a temporary fixture project, native workspace-write/read-only sandboxes,
current user-selected available OpenAI models, real authentication, and recorded
CLI source revision and actual child lifecycle events. Do not use a synthetic
model response as evidence for these cases.

| Case | Required observation | Status |
| --- | --- | --- |
| Enabled flag; role selects available compatible model | Child starts using the role-selected catalog slug; runtime records it | Not run |
| Enabled flag; role selects unavailable model with no reasoning override | Spawn fails before a child/model request; error lists available alternatives | Not run |
| Enabled flag; role selects catalog model incompatible with V2 | Spawn fails before child start | Not run |
| Flag omitted or false | Existing role behavior is preserved; no new validation rejection is introduced | Not run |
| Enabled flag under a custom provider | Existing custom-provider selection behavior is preserved | Not run |
| Enabled flag; role inherits the parent model unchanged | No additional catalog check is imposed on inherited selection | Not run |
| Parent sandbox read-only; role requests another model | Model selection cannot widen permissions; a benign fixture write remains denied | Not run |
| Malformed role file or unsupported reasoning effort | Existing role/reasoning validation still fails appropriately | Not run |

**Execution status:** implementation and tests prepared through GitHub tooling
while the execution environment was offline. Rust compilation, formatter,
schema regeneration, focused tests and real-model sandbox cases have not run.
Keep this change draft and unmerged until the checks above pass. No performance,
token or accuracy improvement is claimed.
