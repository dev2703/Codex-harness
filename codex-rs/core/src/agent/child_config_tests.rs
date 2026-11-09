use super::*;

fn preset(name: &str, backend: Option<MultiAgentVersion>) -> ModelPreset {
    ModelPreset {
        id: name.to_string(),
        model: name.to_string(),
        display_name: name.to_string(),
        description: "catalog fixture".to_string(),
        model_specialty: None,
        default_reasoning_effort: ReasoningEffort::Medium,
        supported_reasoning_efforts: Vec::new(),
        supports_personality: false,
        additional_speed_tiers: Vec::new(),
        service_tiers: Vec::new(),
        default_service_tier: None,
        available_access_programs: None,
        is_default: false,
        upgrade: None,
        show_in_picker: true,
        multi_agent_version: backend,
        availability_nux: None,
        supported_in_api: true,
        input_modalities: Vec::new(),
    }
}

#[test]
fn strict_role_catalog_validation_is_opt_in() {
    assert!(!Feature::StrictRoleModelValidation.default_enabled());
    assert!(!should_validate_role_model_override(
        false,
        OPENAI_PROVIDER_ID,
        Some("fixture-primary"),
        Some("fixture-worker"),
    ));
    let features: codex_features::FeaturesToml =
        toml::from_str("strict_role_model_validation = true").expect("feature configuration");
    assert_eq!(
        features.entries().get("strict_role_model_validation"),
        Some(&true),
    );
}

#[test]
fn inherited_models_and_non_openai_providers_keep_existing_behavior() {
    for (provider, previous, selected) in [
        (
            "custom-provider",
            Some("fixture-primary"),
            Some("fixture-worker"),
        ),
        (
            OPENAI_PROVIDER_ID,
            Some("fixture-primary"),
            Some("fixture-primary"),
        ),
        (OPENAI_PROVIDER_ID, Some("fixture-primary"), None),
    ] {
        assert!(!should_validate_role_model_override(
            true, provider, previous, selected,
        ));
    }
    assert!(should_validate_role_model_override(
        true,
        OPENAI_PROVIDER_ID,
        Some("fixture-primary"),
        Some("fixture-worker"),
    ));
}

#[test]
fn configured_role_model_uses_the_existing_catalog_membership_rule() {
    let catalog = vec![preset("fixture-worker", None)];
    assert_eq!(
        find_spawn_agent_model_name(&catalog, "fixture-worker", MultiAgentVersion::V2),
        Ok("fixture-worker".to_string()),
    );
    let error = find_spawn_agent_model_name(&catalog, "unavailable-worker", MultiAgentVersion::V2)
        .expect_err("unknown role model must fail catalog validation");
    assert!(error.contains("Unknown model"));
    assert!(error.contains("fixture-worker"));
}

#[test]
fn role_model_validation_respects_the_captured_multi_agent_backend() {
    let catalog = vec![
        preset("fixture-disabled", Some(MultiAgentVersion::Disabled)),
        preset("fixture-compatible", Some(MultiAgentVersion::V2)),
    ];
    assert!(
        find_spawn_agent_model_name(&catalog, "fixture-disabled", MultiAgentVersion::V2).is_err()
    );
    assert_eq!(
        find_spawn_agent_model_name(&catalog, "fixture-disabled", MultiAgentVersion::V1),
        Ok("fixture-disabled".to_string()),
    );
    assert_eq!(
        find_spawn_agent_model_name(&catalog, "fixture-compatible", MultiAgentVersion::V2),
        Ok("fixture-compatible".to_string()),
    );
}
