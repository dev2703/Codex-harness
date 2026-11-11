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

#[test]
fn task_files_preserve_literal_text_in_both_modes() {
    let root = tempfile::tempdir().expect("temporary workspace");
    let path = root.path().join("task with spaces.txt");
    let task = "Inspect $(touch never-created) and `printf surprise`\r\n--config=payload\n";
    std::fs::write(&path, task).expect("write task file");
    for mode in [vec![], vec!["--exec"]] {
        let mut args = vec![
            "--dry-run",
            "--prompt-file",
            path.to_str().expect("UTF-8 path"),
        ];
        args.extend(mode);
        let launch = prepare_at(root.path(), &args);
        assert!(launch.plan.prompt.ends_with(task));
        assert!(!launch.plan.prompt.contains("No task supplied yet"));
        assert!(!root.path().join(".codex-harness").exists());
        assert!(!root.path().join("never-created").exists());
    }
}

#[test]
fn stdin_tasks_are_resolved_before_the_team_briefing() {
    let root = tempfile::tempdir().expect("temporary workspace");
    let task = "Inspect literal $HOME and `touch never-created`\n";
    for args in [
        vec!["--exec"],
        vec!["--exec", "-"],
        vec!["--prompt", "-"],
        vec!["--exec", "--prompt-file", "-"],
        vec!["--prompt-file", "-"],
    ] {
        let command = TeamCli::try_parse_from(["team", "--dry-run"].into_iter().chain(args))
            .expect("stdin task options");
        let mut root_cli = TuiCli::try_parse_from(["codex"]).expect("root options");
        root_cli.shared.cwd = Some(root.path().to_owned());
        let launch = command
            .prepare_with_input(root_cli, &mut task.as_bytes(), false)
            .expect("read piped task");
        assert!(launch.plan.prompt.ends_with(task));
        assert!(!launch.plan.prompt.contains("No task supplied yet"));
        assert!(!root.path().join(".codex-harness").exists());
    }
}

#[test]
fn missing_invalid_and_blank_task_sources_fail_before_publication() {
    let root = tempfile::tempdir().expect("temporary workspace");
    let path = root.path().join("task.txt");
    assert!(read_task(Some(&path), None, false, &mut &b""[..], false).is_err());
    for bytes in [&b" \r\n\t"[..], &b"\xff"[..], &b"Inspect\0task"[..]] {
        std::fs::write(&path, bytes).expect("write invalid task");
        let command =
            TeamCli::try_parse_from(["team", "--prompt-file", path.to_str().expect("UTF-8 path")])
                .expect("task file flag");
        let mut root_cli = TuiCli::try_parse_from(["codex"]).expect("root options");
        root_cli.shared.cwd = Some(root.path().to_owned());
        assert!(command.prepare(root_cli).is_err());
        assert!(!root.path().join(".codex-harness").exists());
        assert!(read_task(None, None, true, &mut &bytes[..], false).is_err());
    }
    for prompt in [None, Some("-".to_string())] {
        assert!(read_task(None, prompt, true, &mut &b"task"[..], true).is_err());
    }
    assert!(read_task(None, Some(" ".into()), false, &mut &b""[..], false).is_err());
    assert!(
        read_task(
            None,
            Some("Inspect\0task".into()),
            false,
            &mut &b""[..],
            false
        )
        .is_err()
    );
}

#[test]
fn interactive_startup_and_explicit_tasks_leave_stdin_for_the_native_runtime() {
    let bytes = b"piped context";
    for (prompt, exec_mode) in [(None, false), (Some("Inspect".to_string()), true)] {
        let mut input = &bytes[..];
        assert_eq!(
            read_task(None, prompt.clone(), exec_mode, &mut input, false).expect("optional input"),
            prompt
        );
        assert_eq!(input, bytes);
    }
}

#[test]
fn task_sources_are_mutually_exclusive() {
    for args in [
        vec!["team", "--prompt-file", "task.txt", "positional task"],
        vec!["team", "--prompt-file", "task.txt", "--prompt", "task"],
    ] {
        assert!(TeamCli::try_parse_from(args).is_err());
    }
    let command =
        TeamCli::try_parse_from(["team", "--prompt-file", "task.txt"]).expect("task file option");
    let root = TuiCli::try_parse_from(["codex", "root task"]).expect("root task option");
    assert!(command.prepare(root).is_err());
}

#[test]
fn normalized_dash_task_paths_are_files_and_leave_stdin_unread() {
    for source in ["-/", "-/."] {
        let mut input = &b"Task from piped stdin"[..];
        let error = read_task(Some(Path::new(source)), None, true, &mut input, false)
            .expect_err("a trailing separator selects a file path");
        assert!(error.to_string().contains("could not read UTF-8 task file"));
        assert_eq!(input, b"Task from piped stdin");
    }
}
