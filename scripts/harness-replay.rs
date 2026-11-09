//! Model-free command replay. Build with `rustc --edition 2024 scripts/harness-replay.rs`.
//! Exercises the production policy directly; does not replace a codex-core build.
#[path = "../codex-rs/core/src/tools/failure_policy.rs"]
mod failure_policy;

use failure_policy::FailureFingerprint;
use failure_policy::FailureObserver;
use std::path::Path;
use std::process::Command;
use std::time::Instant;

fn command(observer: &mut FailureObserver, id: usize, script: &str, cwd: &Path) -> (i32, bool) {
    let output = Command::new("sh")
        .args(["-c", script])
        .current_dir(cwd)
        .output()
        .expect("run deterministic fixture command");
    let code = output.status.code().expect("fixture exited normally");
    let error = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let fingerprint = FailureFingerprint::new(
        "shell",
        &["sh".into(), "-c".into(), script.into()],
        &cwd.to_string_lossy(),
        code,
        &error,
    );
    (
        code,
        observer.observe(&id.to_string(), fingerprint).is_some(),
    )
}

fn main() {
    let root = std::env::temp_dir().join(format!("codex-harness-replay-{}", std::process::id()));
    std::fs::create_dir(&root).expect("create fresh fixture directory");
    let started = Instant::now();
    let mut success = FailureObserver::default();
    for id in 0..3 {
        assert_eq!(
            command(&mut success, id, "printf 'ok\\n'", &root),
            (0, false)
        );
    }
    println!(
        "{{\"scenario\":\"successful_command\",\"commands\":3,\"diagnostics\":0,\"pass\":true}}"
    );

    let mut repeated = FailureObserver::default();
    for id in 0..3 {
        assert_eq!(
            command(
                &mut repeated,
                id,
                "printf 'fixture failure\\n' >&2; exit 7",
                &root
            ),
            (7, id == 2)
        );
    }
    println!(
        "{{\"scenario\":\"repeated_failure\",\"commands\":3,\"diagnostics\":1,\"pass\":true}}"
    );

    let mut repair = FailureObserver::default();
    std::fs::write(root.join("value"), "broken").expect("write failing fixture");
    let test = "test \"$(cat value)\" = repaired || { printf 'assertion failed\\n' >&2; exit 1; }";
    assert_eq!(command(&mut repair, 0, test, &root), (1, false));
    assert_eq!(
        command(&mut repair, 1, "printf repaired > value", &root),
        (0, false)
    );
    assert_eq!(command(&mut repair, 2, test, &root), (0, false));
    println!(
        "{{\"scenario\":\"test_failure_then_repair\",\"commands\":3,\"diagnostics\":0,\"pass\":true}}"
    );
    println!(
        "{{\"scope\":\"model_free_policy_replay\",\"elapsed_ms\":{},\"model_calls\":0,\"model_tokens\":null}}",
        started.elapsed().as_millis()
    );
    std::fs::remove_dir_all(root).expect("remove owned fixture directory");
}
