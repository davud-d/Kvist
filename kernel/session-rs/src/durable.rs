//! Private, recoverable JSON-object storage: `kernel/session/durable_json.py`
//! for the operations this program performs.
//!
//! Both implementations work on the same files at the same time, so the rules
//! are shared, not merely similar: the same `.<name>.lock` file under the same
//! lock, the same backup written before the primary, the same refusal of
//! symbolic links, extra hard links, foreign owners and paths outside the
//! boundary, and the same error texts. What depends on the operating system
//! lives in `sys.rs`.

use std::fs::{self, File, OpenOptions};
use std::hash::{BuildHasher, Hasher};
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};

use crate::error::{OpError, Result};
use crate::json::{self, Object, Value};
use crate::pytext;
use crate::sys;

pub const DEFAULT_MAX_BYTES: u64 = 32 * 1024 * 1024;

/// The quoted last component of `path`, as the error texts name files.
pub fn name_of(path: &Path) -> String {
    pytext::repr(&path.file_name().map(|name| name.to_string_lossy()).unwrap_or_default())
}

fn plain_name(path: &Path) -> String {
    path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default()
}

/// `os.path.abspath`: absolute and free of `.` and `..`, without touching the disk.
pub fn absolute(path: &Path) -> Result<PathBuf> {
    let joined = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().map_err(|error| OpError::os(&error, path))?.join(path)
    };
    let mut normalized = PathBuf::new();
    for component in joined.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                normalized.pop();
            }
            other => normalized.push(other),
        }
    }
    Ok(normalized)
}

/// `Path.resolve(strict=True)`: the real path of something that exists.
pub fn canonical(path: &Path) -> Result<PathBuf> {
    sys::canonical(path).map_err(|error| OpError::os(&error, path))
}

// --- Path validation ----------------------------------------------------------

fn validate_existing_components(root: &Path, relative: &Path, expect_directory: bool) -> Result<()> {
    let parts: Vec<Component> = relative.components().collect();
    let mut current = root.to_path_buf();
    for (index, part) in parts.iter().enumerate() {
        current.push(part);
        let info = match fs::symlink_metadata(&current) {
            Ok(info) => info,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(_) => {
                return Err(OpError::Security(format!("Cannot inspect storage component {}", name_of(&current))));
            }
        };
        let name = name_of(&current);
        if info.file_type().is_symlink() {
            return Err(OpError::Security(format!("Storage path contains a symbolic link at {name}")));
        }
        let is_last = index == parts.len() - 1;
        let is_file = is_last && !expect_directory;
        if !is_file && !info.is_dir() {
            return Err(OpError::Security(format!("Storage component {name} is not a directory")));
        }
        if is_file && !info.is_file() {
            return Err(OpError::Security(format!("Storage target {name} is not a regular file")));
        }
        sys::check_owner(&current, &info)?;
        // Only *extra* names are an aliasing risk; a durable replace leaves the
        // outgoing inode with zero links while its name still resolves.
        if is_file {
            let links = sys::link_count_of_path(&current, &info).map_err(|error| OpError::os(&error, &current))?;
            if links > 1 {
                return Err(OpError::Security(format!("Storage target {name} has unsafe hard links")));
            }
        }
    }
    Ok(())
}

/// An absolute file path, after rejecting boundary and link escapes.
pub fn validate_storage_path(path: &Path, boundary: &Path) -> Result<PathBuf> {
    let root = canonical(boundary)?;
    let candidate = absolute(path)?;
    let Ok(relative) = candidate.strip_prefix(&root) else {
        return Err(OpError::Security(format!(
            "Storage path {} is outside its allowed boundary",
            name_of(&candidate)
        )));
    };
    if relative.as_os_str().is_empty() {
        return Err(OpError::Security("Storage path must name a file".to_string()));
    }
    validate_existing_components(&root, relative, false)?;
    Ok(candidate)
}

/// An absolute directory path, after rejecting boundary and link escapes.
pub fn validate_storage_directory(path: &Path, boundary: &Path) -> Result<PathBuf> {
    let root = canonical(boundary)?;
    let candidate = absolute(path)?;
    let Ok(relative) = candidate.strip_prefix(&root) else {
        return Err(OpError::Security(format!(
            "Storage directory {} is outside its allowed boundary",
            name_of(&candidate)
        )));
    };
    if relative.as_os_str().is_empty() {
        return Ok(root);
    }
    validate_existing_components(&root, relative, true)?;
    Ok(candidate)
}

fn secure_file(path: &Path) -> Result<()> {
    sys::set_private_file(path)
        .map_err(|_| OpError::Storage(format!("Cannot secure storage permissions for {}", name_of(path))))
}

fn ensure_private_parents(path: &Path, boundary: &Path) -> Result<()> {
    let root = canonical(boundary)?;
    let parent = path.parent().unwrap_or(path);
    let Ok(relative) = parent.strip_prefix(&root) else {
        return Err(OpError::Security(format!("Storage path {} is outside its allowed boundary", name_of(path))));
    };
    let mut current = root.clone();
    for part in relative.components() {
        current.push(part);
        let name = name_of(&current);
        match sys::create_private_directory(&current) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(_) => return Err(OpError::Storage(format!("Cannot create private storage directory {name}"))),
        }
        let info = fs::symlink_metadata(&current)
            .map_err(|_| OpError::Storage(format!("Cannot inspect private storage directory {name}")))?;
        if !info.is_dir() {
            return Err(OpError::Security(format!("Private storage directory {name} is not a real directory")));
        }
        sys::check_owner(&current, &info)?;
        sys::set_private_directory(&current)
            .map_err(|_| OpError::Storage(format!("Cannot secure storage permissions for {name}")))?;
    }
    Ok(())
}

fn validate_open_file(file: &File, path: &Path) -> Result<()> {
    let info = file.metadata().map_err(|error| OpError::os(&error, path))?;
    if !info.is_file() {
        return Err(OpError::Security(format!("Storage file {} is not regular", name_of(path))));
    }
    sys::check_owner(path, &info)?;
    if sys::link_count_of_file(file).map_err(|error| OpError::os(&error, path))? > 1 {
        return Err(OpError::Security(format!("Storage file {} has unsafe hard links", name_of(path))));
    }
    Ok(())
}

// --- Locking ------------------------------------------------------------------

/// The cross-process lock of one storage file, released when dropped (closing
/// the file releases it).
pub struct PathLock {
    _file: File,
}

fn lock_name(path: &Path) -> PathBuf {
    path.with_file_name(format!(".{}.lock", plain_name(path)))
}

/// Hold the lock every writer of `path` takes, in either implementation.
pub fn lock(path: &Path, boundary: &Path) -> Result<PathLock> {
    ensure_private_parents(path, boundary)?;
    validate_storage_path(path, boundary)?;
    let lock_path = validate_storage_path(&lock_name(path), boundary)?;
    let name = name_of(&lock_path);
    let mut options = sys::private_options();
    options.read(true).write(true).create(true);
    sys::no_follow(&mut options);
    let file = options.open(&lock_path).map_err(|error| {
        if sys::open_failed_on_link(&error) {
            OpError::Security(format!("Storage lock {name} is a symbolic link"))
        } else {
            OpError::Storage(format!("Cannot open storage lock {name}"))
        }
    })?;
    validate_open_file(&file, &lock_path)?;
    sys::set_private_handle(&file).map_err(|error| OpError::os(&error, &lock_path))?;
    sys::prepare_lock(&file).map_err(|error| OpError::os(&error, &lock_path))?;
    file.lock().map_err(|error| OpError::os(&error, &lock_path))?;
    Ok(PathLock { _file: file })
}

// --- Reading ------------------------------------------------------------------

fn too_large(path: &Path, max_bytes: u64) -> OpError {
    OpError::Storage(format!("JSON storage {} exceeds {max_bytes} bytes", name_of(path)))
}

fn read_regular_bytes(path: &Path, max_bytes: u64) -> Result<Option<Vec<u8>>> {
    let unreadable = || OpError::Storage(format!("Cannot read JSON storage {}", name_of(path)));
    let mut options = OpenOptions::new();
    options.read(true);
    sys::no_follow(&mut options);
    let file = match options.open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) if sys::open_failed_on_link(&error) => {
            return Err(OpError::Security(format!("Storage file {} is a symbolic link", name_of(path))));
        }
        Err(_) => return Err(unreadable()),
    };
    validate_open_file(&file, path)?;
    if file.metadata().map_err(|_| unreadable())?.len() > max_bytes {
        return Err(too_large(path, max_bytes));
    }
    let mut raw = Vec::new();
    file.take(max_bytes + 1).read_to_end(&mut raw).map_err(|_| unreadable())?;
    if raw.len() as u64 > max_bytes {
        return Err(too_large(path, max_bytes));
    }
    Ok(Some(raw))
}

/// A private file's content, or `None` when it does not exist.
pub fn read_private_bytes(path: &Path, boundary: &Path) -> Result<Option<Vec<u8>>> {
    read_regular_bytes(&validate_storage_path(path, boundary)?, DEFAULT_MAX_BYTES)
}

fn read_candidate(path: &Path, empty_as_missing: bool) -> Result<Option<Object>> {
    let Some(raw) = read_regular_bytes(path, DEFAULT_MAX_BYTES)? else {
        return Ok(None);
    };
    if raw.is_empty() && empty_as_missing {
        return Ok(None);
    }
    let corrupt = || OpError::Corrupt(format!("JSON storage {} is corrupt", name_of(path)));
    let text = String::from_utf8(raw).map_err(|_| corrupt())?;
    match json::parse(&text).ok_or_else(corrupt)? {
        Value::Object(object) => Ok(Some(object)),
        _ => Err(OpError::Corrupt(format!("JSON storage {} must contain an object", name_of(path)))),
    }
}

fn backup_path(path: &Path) -> PathBuf {
    let mut name = path.as_os_str().to_os_string();
    name.push(".bak");
    PathBuf::from(name)
}

fn no_valid_backup(path: &Path) -> OpError {
    OpError::Corrupt(format!("JSON storage {} is corrupt and has no valid backup", name_of(path)))
}

/// The primary document, or what its backup holds when the primary is missing
/// or corrupt; `None` when neither exists.
fn read_with_backup(target: &Path, boundary: &Path, empty_as_missing: bool) -> Result<(Option<Object>, bool)> {
    let (primary, primary_corrupt) = match read_candidate(target, empty_as_missing) {
        Ok(primary) => (primary, false),
        Err(error) if error.is_corrupt() => (None, true),
        Err(error) => return Err(error),
    };
    if primary.is_some() {
        return Ok((primary, false));
    }
    let backup = validate_storage_path(&backup_path(target), boundary)?;
    let recovered = match read_candidate(&backup, empty_as_missing) {
        Ok(recovered) => recovered,
        Err(error) if error.is_corrupt() && primary_corrupt => return Err(no_valid_backup(target)),
        Err(error) => return Err(error),
    };
    if recovered.is_none() && primary_corrupt {
        return Err(no_valid_backup(target));
    }
    if recovered.is_some() {
        secure_file(&backup)?;
    }
    Ok((recovered, true))
}

/// Read one JSON object, restoring a valid backup after corruption. A missing
/// document reads as an empty object.
pub fn read_json_object(path: &Path, boundary: &Path, empty_as_missing: bool) -> Result<Object> {
    let target = validate_storage_path(path, boundary)?;
    let (found, from_backup) = read_with_backup(&target, boundary, empty_as_missing)?;
    let Some(document) = found else {
        return Ok(Object::new());
    };
    if !from_backup {
        ensure_private_parents(&target, boundary)?;
        secure_file(&target)?;
        return Ok(document);
    }
    let _lock = lock(&target, boundary)?;
    // Another process may have repaired the primary while this one waited.
    let current = match read_candidate(&target, empty_as_missing) {
        Err(error) if error.is_corrupt() => None,
        outcome => outcome?,
    };
    if let Some(current) = current {
        secure_file(&target)?;
        return Ok(current);
    }
    write_unlocked(&target, &document, boundary, false)?;
    Ok(document)
}

// --- Writing ------------------------------------------------------------------

fn create_temporary(path: &Path) -> io::Result<(File, PathBuf)> {
    let name = plain_name(path);
    loop {
        let random = std::collections::hash_map::RandomState::new().build_hasher().finish();
        let temporary = path.with_file_name(format!(".{name}.{random:016x}.tmp"));
        let mut options = sys::private_options();
        options.write(true).create_new(true);
        sys::no_follow(&mut options);
        match options.open(&temporary) {
            Ok(file) => return Ok((file, temporary)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(error),
        }
    }
}

fn replace_from_temporary(mut file: File, temporary: &Path, path: &Path, content: &[u8]) -> io::Result<()> {
    sys::set_private_handle(&file)?;
    file.write_all(content)?;
    file.sync_all()?;
    drop(file);
    fs::rename(temporary, path)
}

fn atomic_replace_bytes(path: &Path, content: &[u8]) -> Result<()> {
    let name = name_of(path);
    let (file, temporary) = create_temporary(path)
        .map_err(|_| OpError::Storage(format!("Cannot create a temporary file for JSON storage {name}")))?;
    let failed = || OpError::Storage(format!("Cannot atomically replace JSON storage {name}"));
    if replace_from_temporary(file, &temporary, path, content).is_err() {
        // Nothing reads a temporary file; one that cannot be removed is only litter.
        let _ = fs::remove_file(&temporary);
        return Err(failed());
    }
    secure_file(path)?;
    sys::sync_directory(path.parent().unwrap_or(path)).map_err(|_| failed())
}

fn write_unlocked(path: &Path, data: &Object, boundary: &Path, backup_existing: bool) -> Result<()> {
    let mut encoded = Value::Object(data.clone()).to_indented();
    encoded.push('\n');
    if encoded.len() as u64 > DEFAULT_MAX_BYTES {
        return Err(too_large(path, DEFAULT_MAX_BYTES));
    }
    if backup_existing {
        // The new generation is mirrored before it replaces the primary: an
        // interruption leaves readers the old intact primary, and after
        // success recovery restores the latest state, not a superseded one.
        let backup = validate_storage_path(&backup_path(path), boundary)?;
        atomic_replace_bytes(&backup, encoded.as_bytes())?;
    }
    atomic_replace_bytes(path, encoded.as_bytes())
}

/// Atomically replace one private JSON object with a durable complete file.
pub fn write_json_object(path: &Path, data: &Object, boundary: &Path) -> Result<()> {
    let target = validate_storage_path(path, boundary)?;
    let _lock = lock(&target, boundary)?;
    write_unlocked(&target, data, boundary, true)
}

/// Atomically read, transform, and replace one JSON object.
pub fn update_json_object(
    path: &Path,
    boundary: &Path,
    empty_as_missing: bool,
    backup_existing: bool,
    update: impl FnOnce(Object) -> Object,
) -> Result<()> {
    let target = validate_storage_path(path, boundary)?;
    let _lock = lock(&target, boundary)?;
    let (found, from_backup) = read_with_backup(&target, boundary, empty_as_missing)?;
    if found.is_some() && !from_backup {
        secure_file(&target)?;
    }
    write_unlocked(&target, &update(found.unwrap_or_default()), boundary, backup_existing)
}

/// Create a JSON object exactly once, never replacing existing state.
pub fn create_json_object(path: &Path, data: &Object, boundary: &Path) -> Result<bool> {
    let target = validate_storage_path(path, boundary)?;
    let _lock = lock(&target, boundary)?;
    for candidate in [target.clone(), backup_path(&target)] {
        let validated = validate_storage_path(&candidate, boundary)?;
        match fs::symlink_metadata(&validated) {
            Ok(_) => return Ok(false),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(_) => return Err(OpError::Storage(format!("Cannot inspect JSON storage {}", name_of(&target)))),
        }
    }
    write_unlocked(&target, data, boundary, false)?;
    Ok(true)
}

/// Durably remove a JSON object and its recovery copy.
///
/// The backup goes first: an interruption can leave the primary, but never a
/// lone backup that brings a deliberately deleted object back on the next read.
pub fn delete_json_object(path: &Path, boundary: &Path) -> Result<bool> {
    let target = validate_storage_path(path, boundary)?;
    let name = name_of(&target);
    let _lock = lock(&target, boundary)?;
    let mut removed = false;
    for candidate in [backup_path(&target), target.clone()] {
        let validated = validate_storage_path(&candidate, boundary)?;
        match fs::remove_file(&validated) {
            Ok(()) => removed = true,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(_) => return Err(OpError::Storage(format!("Cannot remove JSON storage {name}"))),
        }
    }
    if removed {
        sys::sync_directory(target.parent().unwrap_or(&target))
            .map_err(|_| OpError::Storage(format!("Cannot sync deletion of JSON storage {name}")))?;
    }
    Ok(removed)
}

/// Durably remove a private file; `false` when there was nothing to remove.
pub fn delete_private_file(path: &Path, boundary: &Path) -> Result<bool> {
    let target = validate_storage_path(path, boundary)?;
    let name = name_of(&target);
    let _lock = lock(&target, boundary)?;
    match fs::remove_file(&target) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(_) => return Err(OpError::Storage(format!("Cannot remove storage file {name}"))),
    }
    sys::sync_directory(target.parent().unwrap_or(&target))
        .map_err(|_| OpError::Storage(format!("Cannot sync deletion of storage file {name}")))?;
    Ok(true)
}

// --- Listing ------------------------------------------------------------------

/// One `*.json` file of a storage directory.
pub struct Entry {
    pub name: String,
    pub size: u64,
    pub modified_ns: i128,
}

/// The validated `*.json` files of a directory, found in one secure traversal.
pub fn list_json_object_entries(directory: &Path, boundary: &Path) -> Result<Vec<Entry>> {
    sys::list_json_entries(&validate_storage_directory(directory, boundary)?)
}
