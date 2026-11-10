//! Exercise the team entry point through the public Codex argument parser.

use crate::MultitoolCli;
use crate::Subcommand;
use crate::config_override_args::try_parse_from;
use crate::prepend_config_flags;
use crate::profile_v2_for_subcommand;
use crate::reject_root_strict_config_for_subcommand;
use crate::reject_unsupported_worktree_for_subcommand;
use crate::team_cmd::TeamLaunch;
use crate::team_cmd::TeamSession;
use clap::CommandFactory;
use codex_utils_cli::CliConfigOverrides;
use codex_utils_cli::SandboxModeCliArg;
use pretty_assertions::assert_eq;
use std::path::PathBuf;

fn prepare_from_args(args: &[&str]) -> TeamLaunch {
    let mut cli = try_parse_from(args.iter().copied()).expect("parse native team command");
    cli.interactive
        .shared
        .take_auto_review_config_overrides(&mut cli.config_overrides);
    prepend_config_flags(&mut cli.interactive.config_overrides, cli.config_overrides);
    let Some(Subcommand::Team(team)) = cli.subcommand else {
        panic!("expected native team command");
    };
    team.prepare(cli.interactive)
        .expect("prepare native session")
}

fn effective_override(overrides: &CliConfigOverrides, key: &str) -> Option<toml::Value> {
    overrides
        .parse_overrides()
        .expect("valid configuration overrides")
        .into_iter()
        .rev()
        .find_map(|(path, value)| (path == key).then_some(value))
}

#[test]
fn team_is_discoverable_in_help_and_shell_completions() {
    MultitoolCli::command().debug_assert();
    let root_help = MultitoolCli::command().render_long_help().to_string();
    assert!(root_help.contains("team"));
    let help = MultitoolCli::command()
        .try_get_matches_from(["codex", "team", "--help"])
        .expect_err("help should short circuit parsing");
    assert_eq!(help.kind(), clap::error::ErrorKind::DisplayHelp);
    let help = help.to_string();
    for option in [
        "--architect-model",
        "--architect-effort",
        "--engineer-model",
        "--gatekeeper-model",
        "--skeptic-model",
        "--prompt",
        "--exec",
        "--dry-run",
        "--sandbox",
        "--profile",
    ] {
        assert!(help.contains(option), "missing help for {option}");
    }
    let mut completions = Vec::new();
    clap_complete::generate(
        clap_complete::Shell::Bash,
        &mut MultitoolCli::command(),
        "codex",
        &mut completions,
    );
    let completions = String::from_utf8(completions).expect("UTF-8 completion script");
    assert!(completions.contains("codex__team"));
    assert!(completions.contains("--architect-model"));
}

#[test]
fn requested_architect_settings_and_literal_task_reach_the_tui() {
    let task = "Fix spaces, quotes \"like this\", and $(touch never-created)\n-c payload";
    let launch = prepare_from_args(&[
        "codex",
        "team",
        "--dry-run",
        "--architect-model",
        "gpt-6.1-sol",
        "--architect-effort",
        "ultra",
        "--prompt",
        task,
    ]);
    assert!(launch.dry_run);
    let TeamSession::Interactive(interactive) = launch.session else {
        panic!("expected interactive session");
    };
    assert_eq!(interactive.model.as_deref(), Some("gpt-6.1-sol"));
    assert_eq!(
        interactive.prompt.as_deref(),
        Some(launch.plan.prompt.as_str())
    );
    assert!(launch.plan.prompt.ends_with(task));
    assert_eq!(
        effective_override(&interactive.config_overrides, "model_reasoning_effort"),
        Some(toml::Value::String("ultra".into())),
    );
}

#[test]
fn shared_interactive_flags_obey_native_subcommand_precedence() {
    let launch = prepare_from_args(&[
        "codex",
        "--profile",
        "root",
        "--sandbox",
        "read-only",
        "--add-dir",
        "root-write",
        "team",
        "--dry-run",
        "-p",
        "child",
        "--sandbox=workspace-write",
        "--add-dir",
        "child-write",
        "--no-alt-screen",
        "--prompt",
        "Inspect parser boundaries",
    ]);
    let TeamSession::Interactive(interactive) = launch.session else {
        panic!("expected interactive session");
    };
    assert_eq!(
        interactive
            .config_profile_v2
            .as_ref()
            .map(ToString::to_string),
        Some("child".to_string()),
    );
    assert!(matches!(
        interactive.sandbox_mode,
        Some(SandboxModeCliArg::WorkspaceWrite)
    ));
    assert_eq!(
        interactive.add_dir,
        [PathBuf::from("root-write"), PathBuf::from("child-write")],
    );
    assert!(interactive.no_alt_screen);
}

#[test]
fn exec_preserves_root_options_and_native_output_controls() {
    let launch = prepare_from_args(&[
        "codex",
        "-i",
        "root.png",
        "-p",
        "root",
        "-s",
        "read-only",
        "team",
        "--exec",
        "--dry-run",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--output-schema",
        "schema.json",
        "-o",
        "result.txt",
        "--image",
        "child.png",
        "--prompt",
        "Report the findings",
    ]);
    let TeamSession::Exec(exec) = launch.session else {
        panic!("expected non-interactive session");
    };
    assert_eq!(
        exec.config_profile_v2.as_ref().map(ToString::to_string),
        Some("root".to_string()),
    );
    assert!(matches!(
        exec.sandbox_mode,
        Some(SandboxModeCliArg::ReadOnly)
    ));
    assert_eq!(
        exec.images,
        [PathBuf::from("root.png"), PathBuf::from("child.png")]
    );
    assert!(exec.json);
    assert!(exec.ephemeral);
    assert!(exec.skip_git_repo_check);
    assert_eq!(exec.output_schema, Some(PathBuf::from("schema.json")));
    assert_eq!(exec.last_message_file, Some(PathBuf::from("result.txt")));
    assert!(
        exec.prompt
            .expect("team briefing")
            .ends_with("Report the findings")
    );
}

#[test]
fn configuration_overrides_survive_the_team_boundary_in_order() {
    let launch = prepare_from_args(&[
        "codex",
        "-c",
        "sandbox_mode=read-only",
        "-c",
        "model_reasoning_effort=low",
        "team",
        "--dry-run",
        "-c",
        "sandbox_mode=workspace-write",
        "--architect-effort",
        "ultra",
        "--prompt",
        "Inspect the configuration",
    ]);
    let TeamSession::Interactive(interactive) = launch.session else {
        panic!("expected interactive session");
    };
    assert_eq!(
        effective_override(&interactive.config_overrides, "sandbox_mode"),
        Some(toml::Value::String("workspace-write".into())),
    );
    assert_eq!(
        effective_override(&interactive.config_overrides, "model_reasoning_effort"),
        Some(toml::Value::String("ultra".into())),
    );
    assert_eq!(
        effective_override(&interactive.config_overrides, "agents.enabled"),
        Some(toml::Value::Boolean(true)),
    );
    for (path, _) in launch.plan.role_files {
        let role = path
            .file_stem()
            .expect("role filename")
            .to_str()
            .expect("UTF-8");
        assert_eq!(
            effective_override(
                &interactive.config_overrides,
                &format!("agents.{role}.config_file")
            ),
            Some(toml::Value::String(path.to_string_lossy().into_owned())),
        );
    }
}

#[test]
fn root_runtime_policies_allow_team_sessions() {
    for mode in [vec![], vec!["--exec"]] {
        let mut args = vec![
            "codex",
            "--profile",
            "work",
            "--strict-config",
            "--worktree",
            "team",
            "--dry-run",
        ];
        args.extend(mode);
        let cli = try_parse_from(args.iter().copied()).expect("parse runtime options");
        let subcommand = cli.subcommand.as_ref().expect("team subcommand");
        assert_eq!(
            profile_v2_for_subcommand(&cli.interactive, subcommand)
                .expect("root profile is supported")
                .map(ToString::to_string),
            Some("work".to_string()),
        );
        reject_root_strict_config_for_subcommand(cli.interactive.strict_config, &cli.subcommand)
            .expect("root strict config is supported");
        reject_unsupported_worktree_for_subcommand(
            cli.interactive.shared.worktree,
            &cli.subcommand,
        )
        .expect("new team sessions support managed worktrees");
        match prepare_from_args(&args).session {
            TeamSession::Interactive(interactive) => {
                assert!(interactive.strict_config);
                assert!(interactive.worktree);
            }
            TeamSession::Exec(exec) => {
                assert!(exec.strict_config);
                assert!(exec.worktree);
            }
        }
    }
}

#[test]
fn positional_prompt_is_supported_and_ambiguous_prompts_are_rejected() {
    let launch = prepare_from_args(&["codex", "team", "--dry-run", "Inspect the parser"]);
    assert!(launch.plan.prompt.ends_with("Inspect the parser"));
    assert!(try_parse_from(["codex", "team", "--prompt", "first task", "second task",]).is_err());
}
