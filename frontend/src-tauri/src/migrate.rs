//! Carrying an installation over from Dubify Studio, the app's name before
//! Movelo Studio.
//!
//! The app ID changed with the name, and macOS keeps an app's files in
//! folders named after its ID: ~/Library/Application Support/<id> (projects,
//! media, settings) and ~/Library/Logs/<id>. On the first launch after the
//! update the old folders are moved to the new names, and a link is left at
//! each old location so that anything still pointing there keeps working —
//! paths stored in the database (the backend rewrites those, see
//! DUBIFY_PREVIOUS_DATA_DIR) and the old app, if it's still installed.
//!
//! The updater installs a new version over the running copy, so after the
//! update the app still sits in "Dubify Studio.app". It renames itself to
//! "Movelo Studio.app" and starts again from there, so Finder, Launchpad and
//! Spotlight show the new name.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use tauri::{AppHandle, Manager};

/// The app ID before the rename (the old tauri.conf.json "identifier").
const PREVIOUS_ID: &str = "studio.dubify.desktop";
#[cfg(target_os = "macos")]
const PREVIOUS_BUNDLE: &str = "Dubify Studio.app";
#[cfg(target_os = "macos")]
const BUNDLE: &str = "Movelo Studio.app";

/// Why the app kept its old bundle name — logged once logging is up.
static BUNDLE_NOTE: OnceLock<String> = OnceLock::new();

#[derive(Debug, PartialEq)]
enum Move {
    /// Nothing at the old location: a new installation, or moved on an earlier launch.
    Nothing,
    /// Moved, with a link left at the old location.
    Moved,
    /// Both locations hold files: the new one is used and the old one left alone.
    BothExist,
    /// The move didn't work: keep using the old folder, so nothing looks lost.
    Failed(String),
}

/// Move the folder at `old` to `new`, leaving a link to `new` behind.
fn move_folder(old: &Path, new: &Path) -> Move {
    // A real folder only — the link an earlier move left behind doesn't count.
    if !fs::symlink_metadata(old).is_ok_and(|m| m.is_dir()) {
        return Move::Nothing;
    }
    // An empty folder at the new location (made by something that started first) can go.
    if fs::symlink_metadata(new).is_ok() && fs::remove_dir(new).is_err() {
        return Move::BothExist;
    }
    if let Err(e) = fs::rename(old, new) {
        return Move::Failed(e.to_string());
    }
    #[cfg(unix)]
    let _ = std::os::unix::fs::symlink(new, old);
    Move::Moved
}

/// Where this launch keeps its data and logs.
pub struct Folders {
    pub data: PathBuf,
    pub logs: PathBuf,
    /// The data folder's old location while a link there points to `data`:
    /// the backend moves stored paths from it to `data`.
    pub previous_data: Option<PathBuf>,
    /// What happened, for the log (written once logging is up).
    pub notes: Vec<String>,
}

/// This app's data and logs folders, with Dubify Studio's moved into them.
/// Runs before the log plugin, which would otherwise create the new logs folder first.
pub fn folders(app: &AppHandle) -> tauri::Result<Folders> {
    let data = app.path().app_data_dir()?;
    let logs = app.path().app_log_dir()?;
    let mut notes = Vec::new();

    let previous_data = data.with_file_name(PREVIOUS_ID);
    let data = match move_folder(&previous_data, &data) {
        Move::Nothing => data,
        Move::Moved => {
            notes.push(format!("moved the data folder from {} to {}", previous_data.display(), data.display()));
            data
        }
        Move::BothExist => {
            notes.push(format!("{} and {} both hold data — using the second", previous_data.display(), data.display()));
            data
        }
        Move::Failed(e) => {
            notes.push(format!("couldn't move the data folder ({e}) — using {} where it is", previous_data.display()));
            previous_data.clone()
        }
    };

    // Logs are a convenience: if they can't move, the new folder starts empty.
    let previous_logs = logs.with_file_name(PREVIOUS_ID);
    match move_folder(&previous_logs, &logs) {
        Move::Moved => notes.push(format!("moved the logs folder to {}", logs.display())),
        Move::Failed(e) => notes.push(format!(
            "couldn't move the logs folder ({e}) — earlier logs stay in {}",
            previous_logs.display()
        )),
        Move::Nothing | Move::BothExist => {}
    }

    let previous_data = fs::read_link(&previous_data)
        .is_ok_and(|target| target == data)
        .then_some(previous_data);
    Ok(Folders { data, logs, previous_data, notes })
}

/// Log what moving in from Dubify Studio did, once the log plugin is running.
pub fn log_notes(folders: Option<&Folders>) {
    for note in folders.into_iter().flat_map(|f| &f.notes) {
        log::info!("from Dubify Studio: {note}");
    }
    if let Some(note) = BUNDLE_NOTE.get() {
        log::warn!("from Dubify Studio: kept the app's old name — {note}");
    }
}

/// If this copy still sits in "Dubify Studio.app", rename it to "Movelo
/// Studio.app" and start it again from there (this process then exits).
/// Runs before anything else: a running app's files mustn't move under it.
#[cfg(target_os = "macos")]
pub fn adopt_new_bundle_name() {
    let Ok(exe) = std::env::current_exe() else { return };
    // <bundle>/Contents/MacOS/<executable>
    let (Some(name), Some(bundle)) = (exe.file_name(), exe.ancestors().nth(3)) else { return };
    if bundle.file_name() != Some(PREVIOUS_BUNDLE.as_ref()) || !exe.ends_with(Path::new("Contents/MacOS").join(name)) {
        return;
    }
    let renamed = bundle.with_file_name(BUNDLE);
    if let Err(note) = rename_bundle(bundle, &renamed) {
        let _ = BUNDLE_NOTE.set(note);
        return;
    }
    // Started directly rather than through `open`, the same way Tauri restarts
    // an app after an update: the new process keeps this one's environment.
    let started = std::process::Command::new(renamed.join("Contents/MacOS").join(name))
        .args(std::env::args_os().skip(1))
        .spawn();
    match started {
        Ok(_) => std::process::exit(0),
        Err(e) => {
            // Put it back: this process's own files must stay where it expects them.
            let restored = fs::rename(&renamed, bundle).is_ok();
            let _ = BUNDLE_NOTE.set(format!("couldn't start it as {BUNDLE} ({e}); renamed back: {restored}"));
        }
    }
}

#[cfg(target_os = "macos")]
fn rename_bundle(bundle: &Path, renamed: &Path) -> Result<(), String> {
    if bundle.components().any(|c| c.as_os_str() == "AppTranslocation") {
        return Err("macOS runs it from a temporary read-only copy until it's moved to Applications".into());
    }
    if fs::symlink_metadata(renamed).is_ok() {
        return Err(format!("{} already exists", renamed.display()));
    }
    fs::rename(bundle, renamed).map_err(|e| format!("couldn't rename {}: {e}", bundle.display()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn scratch() -> PathBuf {
        static N: AtomicUsize = AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!(
            "movelo-migrate-test-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn with_projects(dir: &Path) {
        fs::create_dir_all(dir.join("uploads/p1")).unwrap();
        fs::write(dir.join("dubify.db"), "projects").unwrap();
        fs::write(dir.join("uploads/p1/source.mp4"), "video").unwrap();
    }

    #[test]
    fn moves_the_old_folder_and_leaves_a_link() {
        let root = scratch();
        let (old, new) = (root.join(PREVIOUS_ID), root.join("com.movelo.studio"));
        with_projects(&old);

        assert_eq!(move_folder(&old, &new), Move::Moved);
        assert_eq!(fs::read_to_string(new.join("uploads/p1/source.mp4")).unwrap(), "video");
        assert_eq!(fs::read_link(&old).unwrap(), new);
        // Old paths still lead to the files.
        assert_eq!(fs::read_to_string(old.join("dubify.db")).unwrap(), "projects");
        // The next launch has nothing to do.
        assert_eq!(move_folder(&old, &new), Move::Nothing);
    }

    #[test]
    fn a_new_installation_has_nothing_to_move() {
        let root = scratch();
        assert_eq!(move_folder(&root.join(PREVIOUS_ID), &root.join("new")), Move::Nothing);
        assert!(!root.join("new").exists());
    }

    #[test]
    fn an_empty_new_folder_is_replaced() {
        let root = scratch();
        let (old, new) = (root.join(PREVIOUS_ID), root.join("new"));
        with_projects(&old);
        fs::create_dir(&new).unwrap();
        assert_eq!(move_folder(&old, &new), Move::Moved);
        assert!(new.join("dubify.db").is_file());
    }

    #[test]
    fn existing_data_in_the_new_folder_is_never_overwritten() {
        let root = scratch();
        let (old, new) = (root.join(PREVIOUS_ID), root.join("new"));
        with_projects(&old);
        fs::create_dir(&new).unwrap();
        fs::write(new.join("dubify.db"), "newer").unwrap();

        assert_eq!(move_folder(&old, &new), Move::BothExist);
        assert_eq!(fs::read_to_string(new.join("dubify.db")).unwrap(), "newer");
        assert_eq!(fs::read_to_string(old.join("dubify.db")).unwrap(), "projects");
    }

    #[cfg(unix)]
    #[test]
    fn a_failed_move_keeps_everything_where_it_was() {
        use std::os::unix::fs::PermissionsExt;
        let root = scratch();
        let old = root.join(PREVIOUS_ID);
        with_projects(&old);
        let locked = root.join("locked");
        fs::create_dir(&locked).unwrap();
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o555)).unwrap();

        assert!(matches!(move_folder(&old, &locked.join("new")), Move::Failed(_)));
        assert!(old.join("dubify.db").is_file());
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o755)).unwrap();
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn the_bundle_is_renamed_unless_the_new_name_is_taken() {
        let root = scratch();
        let (bundle, renamed) = (root.join(PREVIOUS_BUNDLE), root.join(BUNDLE));
        fs::create_dir_all(bundle.join("Contents/MacOS")).unwrap();
        assert_eq!(rename_bundle(&bundle, &renamed), Ok(()));
        assert!(renamed.join("Contents/MacOS").is_dir() && !bundle.exists());

        fs::create_dir_all(bundle.join("Contents/MacOS")).unwrap();
        assert!(rename_bundle(&bundle, &renamed).unwrap_err().contains("already exists"));
        assert!(bundle.exists());

        let translocated = root.join("AppTranslocation/X").join(PREVIOUS_BUNDLE);
        fs::create_dir_all(&translocated).unwrap();
        assert!(rename_bundle(&translocated, &root.join("other")).is_err());
        assert!(translocated.exists());
    }
}
