// How the window is drawn. Transparency is decided when the window is created,
// and on some Linux graphics stacks a transparent web view paints nothing at
// all (measured with WebKitGTK: transparent + DMABUF renderer off paints
// nothing, opaque + DMABUF off paints everything, and NVIDIA needs the DMABUF
// renderer off). So Linux starts opaque and transparency is opt in, while macOS
// and Windows start transparent. Either way the person can change it without
// seeing the page: `--transparent` / `--opaque`, `KVIST_TRANSPARENT=1` /
// `KVIST_OPAQUE=1`, `--safe` / `KVIST_SAFE_RENDER=1` (opaque and the software
// web view), or the settings, which save the choice in `<config dir>/desktop.json`.

use std::io;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Mode {
    pub transparent: bool,
    /// The software web view: slower, but it paints where the accelerated one does not.
    pub safe: bool,
}

/// Where the web view cannot be trusted with transparency, the window starts opaque.
pub const TRANSPARENT_BY_DEFAULT: bool = !cfg!(target_os = "linux");

/// Safe mode and `--opaque` first, then `--transparent`, then the saved choice, then the platform's default.
pub fn decide(args: &[String], env: &dyn Fn(&str) -> Option<String>, saved: Option<bool>, default: bool) -> Mode {
    let flag = |name: &str, variable: &str| args.iter().any(|a| a == name) || env(variable).is_some_and(|v| v == "1");
    let safe = flag("--safe", "KVIST_SAFE_RENDER");
    if safe || flag("--opaque", "KVIST_OPAQUE") {
        return Mode { transparent: false, safe };
    }
    let transparent = flag("--transparent", "KVIST_TRANSPARENT") || saved.unwrap_or(default);
    Mode { transparent, safe }
}

/// `<KVIST_CONFIG_DIR or ~/.kvist>/desktop.json`, beside the core's own config.
pub fn settings_path(env: &dyn Fn(&str) -> Option<String>) -> Option<PathBuf> {
    let dir = env("KVIST_CONFIG_DIR")
        .map(PathBuf::from)
        .or_else(|| env("HOME").or_else(|| env("USERPROFILE")).map(|home| Path::new(&home).join(".kvist")))?;
    Some(dir.join("desktop.json"))
}

pub fn load_saved(path: &Path) -> Option<bool> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str::<Value>(&text).ok()?["transparent"].as_bool()
}

pub fn save(path: &Path, transparent: bool) -> io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(path, json!({"transparent": transparent}).to_string())
}

/// The variables to set for the web view, given the mode and what the person already set.
/// The DMABUF renderer leaves an opaque window blank on several NVIDIA setups, so it is off
/// there; with it off a transparent window paints nothing, so a transparent one keeps the
/// system's choice. Safe mode also turns off accelerated compositing.
pub fn renderer_variables(mode: Mode, env: &dyn Fn(&str) -> Option<String>) -> Vec<&'static str> {
    let mut wanted = Vec::new();
    if !mode.transparent {
        wanted.push("WEBKIT_DISABLE_DMABUF_RENDERER");
    }
    if mode.safe {
        wanted.push("WEBKIT_DISABLE_COMPOSITING_MODE");
    }
    wanted.retain(|name| env(name).is_none());
    wanted
}

#[cfg(target_os = "linux")]
pub fn prepare_renderer(mode: Mode, env: &dyn Fn(&str) -> Option<String>) {
    for name in renderer_variables(mode, env) {
        // Called first thing in main, before any other thread exists.
        unsafe { std::env::set_var(name, "1") };
    }
}

#[cfg(not(target_os = "linux"))]
pub fn prepare_renderer(_mode: Mode, _env: &dyn Fn(&str) -> Option<String>) {}

#[cfg(test)]
mod tests {
    use super::*;

    fn none(_: &str) -> Option<String> {
        None
    }

    fn with<'a>(pairs: &'a [(&'a str, &'a str)]) -> impl Fn(&str) -> Option<String> + 'a {
        move |name| pairs.iter().find(|(k, _)| *k == name).map(|(_, v)| v.to_string())
    }

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn the_platform_default_applies_until_something_says_otherwise() {
        assert_eq!(decide(&[], &none, None, true), Mode { transparent: true, safe: false });
        assert_eq!(decide(&[], &none, None, false), Mode { transparent: false, safe: false });
        assert_eq!(TRANSPARENT_BY_DEFAULT, !cfg!(target_os = "linux"), "Linux starts opaque, the others transparent");
    }

    #[test]
    fn a_saved_choice_beats_the_default_either_way() {
        assert!(decide(&[], &none, Some(true), false).transparent);
        assert!(!decide(&[], &none, Some(false), true).transparent);
    }

    #[test]
    fn the_person_can_ask_for_either_from_outside_the_page() {
        assert!(decide(&args(&["--transparent"]), &none, Some(false), false).transparent);
        assert!(decide(&[], &with(&[("KVIST_TRANSPARENT", "1")]), None, false).transparent);
        assert!(!decide(&args(&["--opaque"]), &none, Some(true), true).transparent);
        assert!(!decide(&[], &with(&[("KVIST_OPAQUE", "1")]), Some(true), true).transparent);
    }

    #[test]
    fn opaque_beats_transparent_when_both_are_asked() {
        assert!(!decide(&args(&["--transparent", "--opaque"]), &none, None, true).transparent);
    }

    #[test]
    fn safe_mode_is_opaque_and_software_whatever_was_saved() {
        let mode = decide(&args(&["--safe"]), &none, Some(true), true);
        assert_eq!(mode, Mode { transparent: false, safe: true });
        assert!(decide(&[], &with(&[("KVIST_SAFE_RENDER", "1")]), None, true).safe);
        assert!(!decide(&[], &with(&[("KVIST_SAFE_RENDER", "0")]), None, true).safe);
    }

    #[test]
    fn an_opaque_window_turns_the_dmabuf_renderer_off_and_a_transparent_one_does_not() {
        let opaque = Mode { transparent: false, safe: false };
        let clear = Mode { transparent: true, safe: false };
        let safe = Mode { transparent: false, safe: true };
        assert_eq!(renderer_variables(opaque, &none), ["WEBKIT_DISABLE_DMABUF_RENDERER"]);
        assert!(renderer_variables(clear, &none).is_empty(), "transparent + DMABUF off paints nothing");
        assert_eq!(renderer_variables(safe, &none), ["WEBKIT_DISABLE_DMABUF_RENDERER", "WEBKIT_DISABLE_COMPOSITING_MODE"]);
    }

    #[test]
    fn variables_the_person_set_are_left_alone() {
        let opaque = Mode { transparent: false, safe: true };
        let theirs = with(&[("WEBKIT_DISABLE_DMABUF_RENDERER", "0")]);
        assert_eq!(renderer_variables(opaque, &theirs), ["WEBKIT_DISABLE_COMPOSITING_MODE"]);
    }

    #[test]
    fn the_choice_is_saved_beside_the_cores_config() {
        let scratch = std::env::temp_dir().join(format!("kvist-window-{}", std::process::id()));
        let by_dir = settings_path(&|name| (name == "KVIST_CONFIG_DIR").then(|| scratch.display().to_string())).unwrap();
        assert_eq!(by_dir, scratch.join("desktop.json"));
        assert_eq!(settings_path(&|name| (name == "HOME").then(|| "/home/x".to_string())), Some(PathBuf::from("/home/x/.kvist/desktop.json")));
        assert!(settings_path(&|name| (name == "USERPROFILE").then(|| "C:\\Users\\x".to_string())).is_some_and(|p| p.ends_with("desktop.json")));
        assert_eq!(settings_path(&none), None);

        assert_eq!(load_saved(&by_dir), None, "nothing saved yet");
        save(&by_dir, false).unwrap();
        assert_eq!(load_saved(&by_dir), Some(false));
        save(&by_dir, true).unwrap();
        assert_eq!(load_saved(&by_dir), Some(true));
        std::fs::write(&by_dir, "not json").unwrap();
        assert_eq!(load_saved(&by_dir), None, "a damaged file means the default");
        std::fs::remove_dir_all(&scratch).unwrap();
    }
}
