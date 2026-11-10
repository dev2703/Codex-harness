use super::*;
use pretty_assertions::assert_eq;

fn prepare_at(root: &Path, args: &[&str]) -> TeamLaunch {
    let mut arguments = vec![
        "team".to_string(),
        "--cwd".to_string(),
        root.display().to_string(),
    ];
    arguments.extend(args.iter().map(|arg| (*arg).to_string()));
    TeamCli::try_parse_from(arguments)
        .expect("parse team flags")
        .prepare(TuiCli::try_parse_from(["codex"]).expect("parse root flags"))
        .expect("prepare team launch")
}

#[test]
fn preview_has_no_filesystem_side_effects_and_roles_round_trip() {
    let root = tempfile::tempdir().expect("temporary workspace");
    let launch = prepare_at(root.path(), &["--dry-run", "--prompt", "Fix the race"]);
    assert!(launch.dry_run);
    assert_eq!(
        std::fs::read_dir(root.path())
            .expect("list workspace")
            .count(),
        0
    );
    assert_eq!(launch.plan.role_files.len(), 3);
    let expected = [
        ("engineer", "gpt-6-sol", "medium", ENGINEER_INSTRUCTIONS),
        ("gatekeeper", "gpt-6-luna", "low", GATEKEEPER_INSTRUCTIONS),
        ("skeptic", "gpt-6-astra", "high", SKEPTIC_INSTRUCTIONS),
    ];
    for (name, model, effort, instructions) in expected {
        let (path, text) = launch
            .plan
            .role_files
            .iter()
            .find(|(path, _)| path.file_stem().expect("role filename") == name)
            .expect("configured role");
        assert!(path.is_absolute());
        let role: toml::Value = toml::from_str(text).expect("valid role TOML");
        assert_eq!(role["name"].as_str(), Some(name));
        assert_eq!(role["model"].as_str(), Some(model));
        assert_eq!(role["model_reasoning_effort"].as_str(), Some(effort));
        assert_eq!(role["developer_instructions"].as_str(), Some(instructions));
        assert!(role.get("approval_policy").is_none());
        assert!(role.get("sandbox_mode").is_none());
    }
    let preview = serde_json::to_value(&launch.plan).expect("serialize preview");
    assert_eq!(preview["argv"][0], "codex");
    assert!(preview.get("status").is_none());
    assert!(launch.plan.prompt.ends_with("Fix the race"));
}

#[test]
fn role_paths_depend_on_configuration_and_not_task_text() {
    let root = tempfile::tempdir().expect("temporary workspace");
    let first = prepare_at(root.path(), &["--prompt", "First task"]);
    let second = prepare_at(root.path(), &["--prompt", "Second task"]);
    let changed = prepare_at(root.path(), &["--engineer-effort", "low"]);
    assert_eq!(first.plan.role_files, second.plan.role_files);
    assert_ne!(first.plan.role_files, changed.plan.role_files);
}

#[test]
fn publication_is_persistent_and_idempotent() {
    let root = tempfile::tempdir().expect("temporary workspace");
    let launch = prepare_at(root.path(), &[]);
    launch.plan.publish().expect("publish roles");
    launch.plan.publish().expect("reuse identical roles");
    for (path, text) in &launch.plan.role_files {
        assert_eq!(std::fs::read_to_string(path).expect("read role"), *text);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(path)
                    .expect("role metadata")
                    .permissions()
                    .mode()
                    & 0o777,
                0o600
            );
        }
    }
}

#[test]
fn concurrent_publication_never_exposes_partial_files() {
    let root = tempfile::tempdir().expect("temporary workspace");
    let launch = prepare_at(root.path(), &[]);
    std::thread::scope(|scope| {
        let workers = (0..12)
            .map(|_| scope.spawn(|| launch.plan.publish()))
            .collect::<Vec<_>>();
        for worker in workers {
            worker
                .join()
                .expect("publication thread")
                .expect("concurrent role publication");
        }
    });
    for (path, text) in &launch.plan.role_files {
        assert_eq!(
            std::fs::read_to_string(path).expect("read complete role"),
            *text
        );
        let parent = path.parent().expect("role directory");
        assert_eq!(
            std::fs::read_dir(parent)
                .expect("list published directory")
                .count(),
            3
        );
    }
}

#[test]
fn conflicting_existing_role_is_preserved() {
    let root = tempfile::tempdir().expect("temporary workspace");
    let launch = prepare_at(root.path(), &[]);
    launch.plan.publish().expect("publish roles");
    let path = launch.plan.role_files.keys().next().expect("role path");
    std::fs::write(path, "unexpected contents").expect("change role");
    let error = launch
        .plan
        .publish()
        .expect_err("conflicting role must fail");
    assert!(error.to_string().contains("existing role file differs"));
    assert_eq!(
        std::fs::read_to_string(path).expect("read conflicting role"),
        "unexpected contents"
    );
}

#[cfg(unix)]
#[test]
fn symlinked_files_and_role_directories_are_rejected() {
    use std::os::unix::fs::symlink;
    for symlink_directory in [false, true] {
        let root = tempfile::tempdir().expect("temporary workspace");
        let launch = prepare_at(root.path(), &[]);
        let path = launch.plan.role_files.keys().next().expect("role path");
        let parent = path.parent().expect("role directory");
        let outside = root.path().join("outside");
        if symlink_directory {
            std::fs::create_dir_all(parent.parent().expect("state directory"))
                .expect("create state root");
            std::fs::create_dir(&outside).expect("outside directory");
            symlink(&outside, parent).expect("symlink directory");
        } else {
            std::fs::create_dir_all(parent).expect("create role directory");
            std::fs::write(&outside, "keep this").expect("outside file");
            symlink(&outside, path).expect("symlink role");
        }
        let error = launch.plan.publish().expect_err("symlink must fail");
        assert!(error.to_string().contains("state path contains a symlink"));
        if symlink_directory {
            assert_eq!(
                std::fs::read_dir(outside)
                    .expect("list outside directory")
                    .count(),
                0
            );
        } else {
            assert_eq!(
                std::fs::read_to_string(outside).expect("read outside file"),
                "keep this"
            );
        }
    }
}

#[test]
fn quoted_model_slugs_round_trip_as_toml() {
    let root = tempfile::tempdir().expect("temporary workspace");
    let model = "custom-\"quote\"-\\path-é";
    let launch = prepare_at(root.path(), &["--engineer-model", model]);
    let (_, text) = launch
        .plan
        .role_files
        .iter()
        .find(|(path, _)| path.file_stem().expect("filename") == "engineer")
        .expect("engineer role");
    let role: toml::Value = toml::from_str(text).expect("valid TOML");
    assert_eq!(role["model"].as_str(), Some(model));
}

#[test]
fn invalid_models_fail_before_publication() {
    let root = tempfile::tempdir().expect("temporary workspace");
    for model in [
        "",
        "  ",
        "bad model",
        "bad\nmodel",
        "bad\u{7f}model",
        "-bad",
    ] {
        let parsed = TeamCli::try_parse_from(["team", "--engineer-model", model]);
        if let Ok(mut parsed) = parsed {
            parsed.interactive.shared.cwd = Some(root.path().to_owned());
            assert!(
                parsed
                    .prepare(TuiCli::try_parse_from(["codex"]).expect("root flags"))
                    .is_err(),
                "model {model:?} accepted"
            );
        }
        assert_eq!(
            std::fs::read_dir(root.path())
                .expect("list workspace")
                .count(),
            0
        );
    }
}

#[test]
fn relative_state_directory_is_resolved_against_session_cwd() {
    let root = tempfile::tempdir().expect("temporary workspace");
    let launch = prepare_at(root.path(), &["--state-dir", "team state"]);
    let expected = root
        .path()
        .canonicalize()
        .expect("canonical workspace")
        .join("team state");
    assert!(
        launch
            .plan
            .role_files
            .keys()
            .all(|path| path.starts_with(&expected))
    );
    assert!(!expected.exists());
}
