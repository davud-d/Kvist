// One Kvist core as a child process speaking `--frontend stdio-multi`
// (docs/UI_API.md §8, §8c). Every line it prints is forwarded to the webview
// as a `core` event tagged with the agent's id, so any number of cores can run
// side by side and the page tells them apart.

use std::fs::File;
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::Sender;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use tauri::{AppHandle, Emitter};

/// A checkout's root, for running from the source tree
/// (`frontends/gui/tauri/src-tauri` is four levels below it).
const CHECKOUT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../..");

/// The command that starts the core: `KVIST_CORE_CMD` (a JSON array of
/// strings, as the launcher passes it to the other interfaces), the installed
/// `kvist-core` (see `installed_core`) or the checkout's
/// `.venv/bin/python kernel/__main__.py`.
pub fn command(env_value: Option<&str>) -> Result<Vec<String>, String> {
    let Some(raw) = env_value else {
        if let Some(core) = installed_core(&Lookup::from_environment()) {
            return Ok(vec![core.display().to_string()]);
        }
        let root = Path::new(CHECKOUT);
        return Ok(vec![
            root.join(".venv/bin/python").display().to_string(),
            root.join("kernel/__main__.py").display().to_string(),
        ]);
    };
    match serde_json::from_str::<Vec<String>>(raw) {
        Ok(argv) if !argv.is_empty() && argv.iter().all(|a| !a.is_empty()) => Ok(argv),
        _ => Err(format!("KVIST_CORE_CMD must be a non-empty JSON array of strings, got {raw}")),
    }
}

const SESSION_NAME: &str = if cfg!(windows) { "kvist-session.exe" } else { "kvist-session" };

/// The command for one-shot session operations (`--session-op`, see `ops`): the native
/// session program when there is one, which answers them without starting the core, else
/// the core itself. The program is `KVIST_SESSION_CMD` (a JSON array, as the launcher
/// names it), the one installed beside an installed core, or the one built in the crate
/// of the checkout the core comes from. It is looked for next to the core it belongs to
/// and nowhere else, so it is never a build of another version.
pub fn session_command(env_value: Option<&str>, core: &[String], checkout: &Path) -> Vec<String> {
    if let Some(Ok(argv)) = env_value.map(serde_json::from_str::<Vec<String>>)
        && !argv.is_empty()
        && argv.iter().all(|a| !a.is_empty())
    {
        return argv;
    }
    let candidate = match core {
        [installed] => Path::new(installed).parent().map(|dir| dir.join(SESSION_NAME)),
        _ => Some(checkout.join("kernel/session-rs/target/release").join(SESSION_NAME)),
    };
    match candidate {
        Some(program) if is_file(&program) => vec![program.display().to_string()],
        _ => core.to_vec(),
    }
}

/// `session_command` for this process: its environment and this checkout.
pub fn session_command_for(core: &[String]) -> Vec<String> {
    session_command(std::env::var("KVIST_SESSION_CMD").ok().as_deref(), core, Path::new(CHECKOUT))
}

/// Where an installed app looks for the agent. A struct so a test can say
/// what the machine looks like.
pub struct Lookup {
    pub exe: Option<PathBuf>,
    pub home: Option<PathBuf>,
    pub local_app_data: Option<PathBuf>,
    pub path: Option<std::ffi::OsString>,
}

impl Lookup {
    fn from_environment() -> Lookup {
        Lookup {
            exe: std::env::current_exe().ok(),
            home: std::env::var_os("HOME").map(PathBuf::from),
            local_app_data: std::env::var_os("LOCALAPPDATA").map(PathBuf::from),
            path: std::env::var_os("PATH"),
        }
    }
}

const CORE_NAME: &str = if cfg!(windows) { "kvist-core.exe" } else { "kvist-core" };

fn is_file(path: &Path) -> bool {
    path.is_file()
}

/// The `kvist-core` the installers put on the machine, nearest first: beside
/// the app or one folder up (`~/.local/share/kvist/app/kvist-app` sits beside
/// `kvist-core`'s folder), the installers' own folder (a macOS `.app` or a
/// Windows shortcut starts with no shell PATH), then PATH.
pub fn installed_core(lookup: &Lookup) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(dir) = lookup.exe.as_deref().and_then(Path::parent) {
        candidates.push(dir.join(CORE_NAME));
        if let Some(up) = dir.parent() {
            candidates.push(up.join(CORE_NAME));
        }
    }
    if let Some(local) = &lookup.local_app_data {
        candidates.push(local.join("Kvist").join(CORE_NAME));
    }
    if let Some(home) = &lookup.home {
        candidates.push(home.join(".local/share/kvist").join(CORE_NAME));
    }
    if let Some(path) = &lookup.path {
        candidates.extend(std::env::split_paths(path).map(|dir| dir.join(CORE_NAME)));
    }
    candidates.into_iter().find(|candidate| is_file(candidate))
}

pub struct Core {
    child: Child,
    /// `None` once closed, which tells the core to finish.
    stdin: Option<ChildStdin>,
    log: PathBuf,
}

impl Core {
    /// Starts the core in `workspace`. `ready` gets one message when the
    /// handshake (`hello`) has been read or the stream ended without it.
    pub fn spawn(app: AppHandle, id: &str, argv: &[String], workspace: &Path, ready: Sender<()>) -> io::Result<Core> {
        let log = std::env::temp_dir().join(format!("kvist-tauri-core-{}-{id}.log", std::process::id()));
        let mut child = Command::new(&argv[0])
            .args(&argv[1..])
            // `--wake`: this interface shows turns the core starts by itself,
            // so helpers and background jobs may outlive an answer.
            .args(["--workspace", &workspace.display().to_string(), "--frontend", "stdio-multi", "--wake"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(File::create(&log)?)
            .spawn()?;
        let stdin = child.stdin.take().ok_or_else(|| io::Error::other("the core has no stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| io::Error::other("the core has no stdout"))?;
        let agent = id.to_string();
        thread::spawn(move || forward(app, &agent, stdout, ready));
        Ok(Core { child, stdin: Some(stdin), log })
    }

    pub fn send(&mut self, message: &Value) -> io::Result<()> {
        let stdin = self.stdin.as_mut().ok_or_else(|| io::Error::other("the core's input is closed"))?;
        let mut line = serde_json::to_vec(message)?;
        line.push(b'\n');
        stdin.write_all(&line)?;
        stdin.flush()
    }

    /// Stops a core that has not finished on its own; used when a start fails.
    pub fn kill(&mut self) {
        if let Err(error) = self.child.kill() {
            eprintln!("kvist-tauri: could not stop the core: {error}");
        }
    }
}

/// Reads the core's stdout until it closes, forwarding each event. The page
/// learns how the stream ended from a synthetic `gone` event.
fn forward(app: AppHandle, agent: &str, stdout: impl io::Read, ready: Sender<()>) {
    let mut ended = "the core exited".to_string();
    for line in BufReader::new(stdout).lines() {
        let line = match line {
            Ok(line) if line.trim().is_empty() => continue,
            Ok(line) => line,
            Err(error) => {
                ended = format!("reading the core failed ({error})");
                break;
            }
        };
        let Ok(event) = serde_json::from_str::<Value>(&line) else {
            ended = "the core printed a line that is not JSON".to_string();
            break;
        };
        let handshake = event["type"] == "hello";
        emit(&app, agent, event);
        if handshake {
            // The opener may have given up already; nothing to tell then.
            let _ = ready.send(());
        }
    }
    emit(&app, agent, json!({"type": "gone", "message": ended}));
    let _ = ready.send(());
}

fn emit(app: &AppHandle, agent: &str, event: Value) {
    if let Err(error) = app.emit("core", json!({"agent": agent, "event": event})) {
        eprintln!("kvist-tauri: could not deliver an event: {error}");
    }
}

impl Drop for Core {
    /// Closing stdin is the core's signal to finish (it cancels an open dialog
    /// and exits); a core that does not is killed. The log is removed after a
    /// clean exit and kept after any other, for whoever looks into it.
    fn drop(&mut self) {
        drop(self.stdin.take());
        let deadline = Instant::now() + Duration::from_millis(1500);
        while Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(status)) => {
                    if status.success()
                        && let Err(error) = std::fs::remove_file(&self.log)
                    {
                        eprintln!("kvist-tauri: could not remove {}: {error}", self.log.display());
                    }
                    return;
                }
                Ok(None) => thread::sleep(Duration::from_millis(30)),
                Err(error) => {
                    eprintln!("kvist-tauri: could not wait for the core: {error}");
                    break;
                }
            }
        }
        self.kill();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_core_command_comes_from_the_launcher_or_the_checkout() {
        assert_eq!(command(Some(r#"["kvist-core"]"#)), Ok(vec!["kvist-core".to_string()]));
        assert!(command(Some("[]")).is_err());
        assert!(command(Some(r#"[""]"#)).is_err());
        assert!(command(Some("kvist-core")).is_err());
    }

    fn touch(path: &Path) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, b"core").unwrap();
    }

    fn nowhere() -> Lookup {
        Lookup { exe: None, home: None, local_app_data: None, path: None }
    }

    #[test]
    fn session_operations_use_the_program_named_installed_or_built_beside_their_core() {
        let scratch = std::env::temp_dir().join(format!("kvist-session-cmd-{}", std::process::id()));
        let installed_core = vec![scratch.join("lib").join(CORE_NAME).display().to_string()];
        let checkout_core = vec!["python".to_string(), "kernel/__main__.py".to_string()];
        // Nothing next to the core: the core serves them.
        assert_eq!(session_command(None, &installed_core, &scratch), installed_core);
        assert_eq!(session_command(None, &checkout_core, &scratch), checkout_core);

        touch(&scratch.join("lib").join(SESSION_NAME));
        touch(&scratch.join("kernel/session-rs/target/release").join(SESSION_NAME));
        let beside = vec![scratch.join("lib").join(SESSION_NAME).display().to_string()];
        let built = vec![scratch.join("kernel/session-rs/target/release").join(SESSION_NAME).display().to_string()];
        assert_eq!(session_command(None, &installed_core, &scratch), beside);
        assert_eq!(session_command(None, &checkout_core, &scratch), built);
        // An installed core is never paired with the checkout's build, which may be another version.
        std::fs::remove_file(scratch.join("lib").join(SESSION_NAME)).unwrap();
        assert_eq!(session_command(None, &installed_core, &scratch), installed_core);

        // The launcher's choice wins; an unusable one is ignored.
        assert_eq!(session_command(Some(r#"["/opt/kvist-session"]"#), &checkout_core, &scratch), ["/opt/kvist-session"]);
        for unusable in ["[]", r#"[""]"#, "/opt/kvist-session"] {
            assert_eq!(session_command(Some(unusable), &checkout_core, &scratch), built);
        }
        std::fs::remove_dir_all(&scratch).unwrap();
    }

    #[test]
    fn an_installed_core_is_found_beside_the_app_or_one_folder_up() {
        let scratch = std::env::temp_dir().join(format!("kvist-core-lookup-{}", std::process::id()));
        let app = scratch.join("kvist/app/kvist-app");
        touch(&scratch.join("kvist").join(CORE_NAME));
        let found = installed_core(&Lookup { exe: Some(app), ..nowhere() });
        assert_eq!(found, Some(scratch.join("kvist").join(CORE_NAME)));
        std::fs::remove_dir_all(&scratch).unwrap();
    }

    #[test]
    fn an_app_started_without_a_shell_finds_the_installers_folder() {
        let scratch = std::env::temp_dir().join(format!("kvist-core-home-{}", std::process::id()));
        touch(&scratch.join(".local/share/kvist").join(CORE_NAME));
        let found = installed_core(&Lookup { home: Some(scratch.clone()), ..nowhere() });
        assert_eq!(found, Some(scratch.join(".local/share/kvist").join(CORE_NAME)));
        std::fs::remove_dir_all(&scratch).unwrap();
    }

    #[test]
    fn path_is_the_last_place_looked_at_and_nothing_found_is_none() {
        let scratch = std::env::temp_dir().join(format!("kvist-core-path-{}", std::process::id()));
        touch(&scratch.join("bin").join(CORE_NAME));
        let path = std::env::join_paths([scratch.join("bin")]).unwrap();
        assert_eq!(installed_core(&Lookup { path: Some(path), ..nowhere() }), Some(scratch.join("bin").join(CORE_NAME)));
        assert_eq!(installed_core(&nowhere()), None);
        std::fs::remove_dir_all(&scratch).unwrap();
    }
}
