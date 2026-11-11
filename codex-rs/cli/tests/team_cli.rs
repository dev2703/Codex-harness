//! Process smoke coverage for the native team command, without model requests.

use anyhow::Result;
use pretty_assertions::assert_eq;
use serde_json::Value;
use std::path::PathBuf;
use std::process::Output;
use tempfile::TempDir;

struct TeamFixture {
    _root: TempDir,
    project: PathBuf,
    home: PathBuf,
    state: PathBuf,
}

impl TeamFixture {
    fn new() -> Result<Self> {
        let root = tempfile::tempdir()?;
        let project = root.path().join("project with spaces");
        let home = root.path().join("home");
        let state = project.join("team state");
        std::fs::create_dir(&project)?;
        std::fs::create_dir(&home)?;
        Ok(Self {
            _root: root,
            project,
            home,
            state,
        })
    }

    async fn run(&self, args: &[&str]) -> Result<Output> {
        let mut command = tokio::process::Command::new(codex_utils_cargo_bin::cargo_bin("codex")?);
        command
            .current_dir(&self.project)
            .env("CODEX_HOME", &self.home)
            .env_remove("CODEX_API_KEY")
            .env_remove("OPENAI_API_KEY")
            .env_remove("CODEX_ACCESS_TOKEN")
            .args(args)
            .kill_on_drop(true);
        Ok(tokio::time::timeout(std::time::Duration::from_secs(20), command.output()).await??)
    }
}

#[tokio::test]
async fn requested_team_command_produces_a_json_preview_without_role_files() -> Result<()> {
    let fixture = TeamFixture::new()?;
    let output = fixture
        .run(&[
            "team",
            "--architect-model",
            "gpt-6.1-sol",
            "--architect-effort",
            "ultra",
            "--prompt",
            "your task",
            "--state-dir",
            fixture.state.to_str().expect("UTF-8 fixture path"),
            "--dry-run",
        ])
        .await?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let preview: Value = serde_json::from_slice(&output.stdout)?;
    assert!(
        preview["prompt"]
            .as_str()
            .expect("prompt string")
            .ends_with("your task")
    );
    assert_eq!(
        preview["role_files"]
            .as_object()
            .expect("role mapping")
            .len(),
        3
    );
    assert!(!fixture.state.exists());
    assert!(
        !String::from_utf8_lossy(&output.stderr).contains("Team roles configured:"),
        "dry-run must not print the launch banner",
    );
    Ok(())
}

#[tokio::test]
async fn exec_preview_preserves_codex_flags_and_literal_task_text() -> Result<()> {
    let fixture = TeamFixture::new()?;
    let task = "Inspect $(touch never-created) and `printf surprise`\n--config=payload";
    let output = fixture
        .run(&[
            "--profile",
            "work",
            "team",
            "--exec",
            "--dry-run",
            "--sandbox=read-only",
            "--json",
            "--skip-git-repo-check",
            "--prompt",
            task,
        ])
        .await?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let preview: Value = serde_json::from_slice(&output.stdout)?;
    let argv = preview["argv"].as_array().expect("argument array");
    for expected in ["exec", "--json", "--skip-git-repo-check"] {
        assert!(
            argv.iter().any(|arg| arg.as_str() == Some(expected)),
            "missing {expected}"
        );
    }
    assert!(
        preview["prompt"]
            .as_str()
            .expect("prompt string")
            .ends_with(task)
    );
    assert!(!fixture.project.join("never-created").exists());
    assert!(!fixture.project.join(".codex-harness").exists());
    Ok(())
}

#[tokio::test]
async fn invalid_and_remote_team_commands_fail_before_publication() -> Result<()> {
    let fixture = TeamFixture::new()?;
    for args in [
        vec!["team", "--architect-effort", "unsupported"],
        vec!["team", "--architect-model", "invalid model"],
        vec![
            "--remote",
            "ws://127.0.0.1:1",
            "team",
            "--prompt",
            "Inspect",
        ],
    ] {
        let output = fixture.run(&args).await?;
        assert!(!output.status.success(), "unexpected success for {args:?}");
        assert!(output.stdout.is_empty(), "failure emitted a launch preview");
        assert!(!fixture.project.join(".codex-harness").exists());
    }
    Ok(())
}
