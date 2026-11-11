//! Everything that differs between operating systems, in one place.
//!
//! The rest of the program is portable. Each function here has a Unix and a
//! Windows body that do what the Python implementation does on that system
//! (`kernel/session/durable_json.py` branches on `os.name` the same way):
//!
//! * Unix: files are 0600 and folders 0700, names are opened with `O_NOFOLLOW`,
//!   owners and link counts are checked, folders are synced, `flock` locks.
//! * Windows: Python has no `O_NOFOLLOW`, owner check or folder sync there, and
//!   neither does this; permissions are not POSIX modes. The lock is a byte-range
//!   lock on the lock file, which Windows enforces for every reader and writer,
//!   so the file must hold the byte Python's `msvcrt.locking` expects.

use std::fs::OpenOptions;

pub use imp::*;

/// `OpenOptions` for a file only this person may use.
pub fn private_options() -> OpenOptions {
    let mut options = OpenOptions::new();
    imp::make_private(&mut options);
    options
}

/// The file's last-change time, as nanoseconds since the epoch.
#[cfg(windows)]
pub fn modified_ns(info: &std::fs::Metadata) -> i128 {
    info.modified()
        .ok()
        .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
        .map_or(0, |elapsed| i128::try_from(elapsed.as_nanos()).unwrap_or(0))
}

#[cfg(unix)]
mod imp {
    use std::ffi::CStr;
    use std::fs::{self, DirBuilder, File, Metadata, OpenOptions, Permissions};
    use std::io;
    use std::os::fd::{AsRawFd, IntoRawFd};
    use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
    use std::path::{Path, PathBuf};

    use crate::durable::{name_of, Entry};
    use crate::error::{OpError, Result};
    use crate::pytext;

    const PRIVATE_FILE_MODE: u32 = 0o600;
    const PRIVATE_DIRECTORY_MODE: u32 = 0o700;

    pub fn make_private(options: &mut OpenOptions) {
        options.mode(PRIVATE_FILE_MODE);
    }

    /// Open the name itself, never what a symbolic link there points to.
    pub fn no_follow(options: &mut OpenOptions) {
        options.custom_flags(libc::O_NOFOLLOW);
    }

    pub fn open_failed_on_link(error: &io::Error) -> bool {
        error.raw_os_error() == Some(libc::ELOOP)
    }

    fn require_uid(path: &Path, owner: u32) -> Result<()> {
        // SAFETY: geteuid has no preconditions and cannot fail.
        if owner != unsafe { libc::geteuid() } {
            return Err(OpError::Security(format!("Storage component {} is owned by another user", name_of(path))));
        }
        Ok(())
    }

    pub fn check_owner(path: &Path, info: &Metadata) -> Result<()> {
        require_uid(path, info.uid())
    }

    pub fn link_count_of_file(file: &File) -> io::Result<u64> {
        file.metadata().map(|info| info.nlink())
    }

    pub fn link_count_of_path(_path: &Path, info: &Metadata) -> io::Result<u64> {
        Ok(info.nlink())
    }

    pub fn set_private_file(path: &Path) -> io::Result<()> {
        fs::set_permissions(path, Permissions::from_mode(PRIVATE_FILE_MODE))
    }

    pub fn set_private_directory(path: &Path) -> io::Result<()> {
        fs::set_permissions(path, Permissions::from_mode(PRIVATE_DIRECTORY_MODE))
    }

    pub fn set_private_handle(file: &File) -> io::Result<()> {
        file.set_permissions(Permissions::from_mode(PRIVATE_FILE_MODE))
    }

    pub fn create_private_directory(path: &Path) -> io::Result<()> {
        DirBuilder::new().mode(PRIVATE_DIRECTORY_MODE).create(path)
    }

    pub fn sync_directory(path: &Path) -> io::Result<()> {
        let directory = OpenOptions::new().read(true).custom_flags(libc::O_DIRECTORY).open(path)?;
        match directory.sync_all() {
            // Some filesystems cannot sync a directory; the rename is still atomic there.
            Err(error) if [libc::EBADF, libc::EINVAL, libc::ENOTSUP].iter().any(|&code| error.raw_os_error() == Some(code)) => Ok(()),
            outcome => outcome,
        }
    }

    /// Nothing to prepare: `flock` locks any file, empty or not.
    pub fn prepare_lock(_file: &File) -> io::Result<()> {
        Ok(())
    }

    pub fn canonical(path: &Path) -> io::Result<PathBuf> {
        fs::canonicalize(path)
    }

    /// The person's home folder: `HOME`, or the account's own when it is unset.
    pub fn home_directory() -> Option<PathBuf> {
        if let Some(home) = std::env::var_os("HOME") {
            return Some(PathBuf::from(home));
        }
        // SAFETY: getpwuid returns null or a record that stays valid until the next
        // lookup; this single-threaded program copies the folder out at once.
        let record = unsafe { libc::getpwuid(libc::getuid()) };
        if record.is_null() {
            return None;
        }
        // SAFETY: a non-null record carries a NUL-terminated home folder.
        let folder = unsafe { CStr::from_ptr((*record).pw_dir) };
        Some(PathBuf::from(folder.to_string_lossy().into_owned()))
    }

    pub fn local_timestamp() -> String {
        let seconds = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |elapsed| elapsed.as_secs());
        let seconds = libc::time_t::try_from(seconds).unwrap_or(0);
        // SAFETY: an all-zero `tm` is a valid value for localtime_r to overwrite.
        let mut local: libc::tm = unsafe { std::mem::zeroed() };
        // SAFETY: both pointers refer to live values of the expected types.
        unsafe { libc::localtime_r(&seconds, &mut local) };
        format!(
            "{:04}{:02}{:02}-{:02}{:02}{:02}",
            local.tm_year + 1900,
            local.tm_mon + 1,
            local.tm_mday,
            local.tm_hour,
            local.tm_min,
            local.tm_sec
        )
    }

    /// An open directory stream, closed when dropped.
    struct DirectoryStream(*mut libc::DIR);

    impl Drop for DirectoryStream {
        fn drop(&mut self) {
            // SAFETY: the stream came from a successful fdopendir and is closed once.
            unsafe { libc::closedir(self.0) };
        }
    }

    fn open_stream(directory: &File, path: &Path) -> Result<DirectoryStream> {
        let unreadable = |error: io::Error| OpError::os(&error, path);
        // The stream owns its descriptor; `directory` keeps the one entries are inspected through.
        let descriptor = directory.try_clone().map_err(unreadable)?.into_raw_fd();
        // SAFETY: `descriptor` is an open directory this function owns; on failure it is closed below.
        let stream = unsafe { libc::fdopendir(descriptor) };
        if stream.is_null() {
            let error = io::Error::last_os_error();
            // SAFETY: fdopendir failed, so the descriptor is still this function's to close.
            unsafe { libc::close(descriptor) };
            return Err(unreadable(error));
        }
        Ok(DirectoryStream(stream))
    }

    /// The change time of a `stat` record in nanoseconds. NetBSD spells the field differently.
    fn changed_ns(info: &libc::stat) -> i128 {
        #[cfg(target_os = "netbsd")]
        let nanoseconds = info.st_mtimensec;
        #[cfg(not(target_os = "netbsd"))]
        let nanoseconds = info.st_mtime_nsec;
        i128::from(info.st_mtime) * 1_000_000_000 + i128::from(nanoseconds)
    }

    fn inspect_entry(directory: &File, parent: &Path, raw_name: &CStr) -> Result<Option<Entry>> {
        let name = String::from_utf8_lossy(raw_name.to_bytes()).into_owned();
        let quoted = pytext::repr(&name);
        // SAFETY: an all-zero `stat` is a valid value for fstatat to overwrite.
        let mut info: libc::stat = unsafe { std::mem::zeroed() };
        // SAFETY: the descriptor is open, the name is NUL-terminated, `info` is writable.
        let status =
            unsafe { libc::fstatat(directory.as_raw_fd(), raw_name.as_ptr(), &mut info, libc::AT_SYMLINK_NOFOLLOW) };
        if status != 0 {
            return match io::Error::last_os_error() {
                error if error.raw_os_error() == Some(libc::ENOENT) => Ok(None),
                _ => Err(OpError::Storage(format!("Cannot inspect JSON storage {quoted}"))),
            };
        }
        let kind = info.st_mode & libc::S_IFMT;
        if kind == libc::S_IFLNK {
            return Err(OpError::Security(format!("Storage path contains a symbolic link at {quoted}")));
        }
        if kind != libc::S_IFREG {
            return Err(OpError::Security(format!("Storage target {quoted} is not a regular file")));
        }
        require_uid(&parent.join(&name), info.st_uid)?;
        if info.st_nlink > 1 {
            return Err(OpError::Security(format!("Storage target {quoted} has unsafe hard links")));
        }
        Ok(Some(Entry { name, size: u64::try_from(info.st_size).unwrap_or(0), modified_ns: changed_ns(&info) }))
    }

    /// The validated `*.json` files of a directory, found in one secure traversal:
    /// every entry is inspected through the descriptor the directory was checked on.
    pub fn list_json_entries(target: &Path) -> Result<Vec<Entry>> {
        let name = name_of(target);
        let opened = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
            .open(target)
            .map_err(|error| {
                if open_failed_on_link(&error) {
                    OpError::Security(format!("Storage directory {name} is a symbolic link"))
                } else {
                    OpError::Storage(format!("Cannot open storage directory {name}"))
                }
            })?;
        let info = opened.metadata().map_err(|error| OpError::os(&error, target))?;
        if !info.is_dir() {
            return Err(OpError::Security(format!("Storage directory {name} is not a directory")));
        }
        check_owner(target, &info)?;

        let stream = open_stream(&opened, target)?;
        let mut entries = Vec::new();
        loop {
            // SAFETY: the stream is open and used by this thread only.
            let raw = unsafe { libc::readdir(stream.0) };
            if raw.is_null() {
                return Ok(entries);
            }
            // SAFETY: readdir returned a valid entry whose name is NUL-terminated;
            // it is copied before the next readdir call can overwrite it.
            let raw_name = unsafe { CStr::from_ptr((*raw).d_name.as_ptr()) }.to_owned();
            if !raw_name.to_bytes().ends_with(b".json") {
                continue;
            }
            if let Some(entry) = inspect_entry(&opened, target, &raw_name)? {
                entries.push(entry);
            }
        }
    }
}

#[cfg(windows)]
mod imp {
    use std::fs::{self, File, Metadata, OpenOptions};
    use std::io::{self, Write};
    use std::os::windows::fs::OpenOptionsExt;
    use std::os::windows::io::AsRawHandle;
    use std::path::{Path, PathBuf};

    use windows_sys::Win32::Foundation::{HANDLE, SYSTEMTIME};
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION, FILE_FLAG_BACKUP_SEMANTICS,
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE,
    };
    use windows_sys::Win32::System::SystemInformation::GetLocalTime;

    use crate::durable::{name_of, Entry};
    use crate::error::{OpError, Result};
    use crate::pytext;

    // Python's `os.open` has no O_NOFOLLOW on Windows either: links are refused by
    // looking at every path component first, which `durable` does on every system.
    pub fn make_private(_options: &mut OpenOptions) {}

    pub fn no_follow(_options: &mut OpenOptions) {}

    pub fn open_failed_on_link(_error: &io::Error) -> bool {
        false
    }

    pub fn check_owner(_path: &Path, _info: &Metadata) -> Result<()> {
        Ok(())
    }

    pub fn link_count_of_file(file: &File) -> io::Result<u64> {
        // SAFETY: an all-zero record is a valid value for the call to overwrite.
        let mut record: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
        // SAFETY: the handle belongs to the open `file`; `record` is writable.
        let status = unsafe { GetFileInformationByHandle(file.as_raw_handle() as HANDLE, &mut record) };
        if status == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(u64::from(record.nNumberOfLinks))
    }

    /// The count for a file reached by name; a link itself is opened, not its target.
    pub fn link_count_of_path(path: &Path, info: &Metadata) -> io::Result<u64> {
        if !info.is_file() {
            return Ok(1);
        }
        let file = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
            .open(path)?;
        link_count_of_file(&file)
    }

    // Python's `os.chmod` only toggles the read-only attribute here, and a private
    // file is writable: there is nothing to set.
    pub fn set_private_file(_path: &Path) -> io::Result<()> {
        Ok(())
    }

    pub fn set_private_directory(_path: &Path) -> io::Result<()> {
        Ok(())
    }

    pub fn set_private_handle(_file: &File) -> io::Result<()> {
        Ok(())
    }

    pub fn create_private_directory(path: &Path) -> io::Result<()> {
        fs::create_dir(path)
    }

    pub fn sync_directory(_path: &Path) -> io::Result<()> {
        Ok(())
    }

    /// Python locks one byte at offset 0 (`msvcrt.locking`), and Windows refuses to lock
    /// a byte the file does not have: an empty lock file gets a NUL first, as there.
    pub fn prepare_lock(file: &File) -> io::Result<()> {
        if file.metadata()?.len() == 0 {
            let mut handle = file;
            handle.write_all(&[0])?;
            file.sync_all()?;
        }
        Ok(())
    }

    /// `fs::canonicalize` answers with `\\?\` paths; Python's `resolve()` does not.
    pub fn canonical(path: &Path) -> io::Result<PathBuf> {
        let resolved = fs::canonicalize(path)?;
        let text = resolved.to_string_lossy();
        if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
            return Ok(PathBuf::from(format!(r"\\{rest}")));
        }
        match text.strip_prefix(r"\\?\") {
            Some(rest) if rest.as_bytes().get(1) == Some(&b':') => Ok(PathBuf::from(rest)),
            _ => Ok(resolved),
        }
    }

    /// The person's home folder, where Python's `Path.home()` looks on Windows.
    pub fn home_directory() -> Option<PathBuf> {
        if let Some(profile) = std::env::var_os("USERPROFILE") {
            return Some(PathBuf::from(profile));
        }
        let (drive, path) = (std::env::var_os("HOMEDRIVE")?, std::env::var_os("HOMEPATH")?);
        let mut home = PathBuf::from(drive);
        home.push(path);
        Some(home)
    }

    pub fn local_timestamp() -> String {
        // SAFETY: an all-zero SYSTEMTIME is a valid value for GetLocalTime to overwrite.
        let mut local: SYSTEMTIME = unsafe { std::mem::zeroed() };
        // SAFETY: the pointer refers to a live SYSTEMTIME.
        unsafe { GetLocalTime(&mut local) };
        format!(
            "{:04}{:02}{:02}-{:02}{:02}{:02}",
            local.wYear, local.wMonth, local.wDay, local.wHour, local.wMinute, local.wSecond
        )
    }

    /// The `*.json` files of a folder, each looked at without following links.
    pub fn list_json_entries(target: &Path) -> Result<Vec<Entry>> {
        let name = name_of(target);
        let info = fs::symlink_metadata(target)
            .map_err(|_| OpError::Storage(format!("Cannot open storage directory {name}")))?;
        if info.file_type().is_symlink() {
            return Err(OpError::Security(format!("Storage directory {name} is a symbolic link")));
        }
        if !info.is_dir() {
            return Err(OpError::Security(format!("Storage directory {name} is not a directory")));
        }
        let folder = fs::read_dir(target).map_err(|error| OpError::os(&error, target))?;
        let mut entries = Vec::new();
        for item in folder {
            let item = item.map_err(|error| OpError::os(&error, target))?;
            let file_name = item.file_name().to_string_lossy().into_owned();
            if !file_name.ends_with(".json") {
                continue;
            }
            let quoted = pytext::repr(&file_name);
            let path = target.join(&file_name);
            let info = match fs::symlink_metadata(&path) {
                Ok(info) => info,
                Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
                Err(_) => return Err(OpError::Storage(format!("Cannot inspect JSON storage {quoted}"))),
            };
            if info.file_type().is_symlink() {
                return Err(OpError::Security(format!("Storage path contains a symbolic link at {quoted}")));
            }
            if !info.is_file() {
                return Err(OpError::Security(format!("Storage target {quoted} is not a regular file")));
            }
            let links = link_count_of_path(&path, &info).map_err(|error| OpError::os(&error, &path))?;
            if links > 1 {
                return Err(OpError::Security(format!("Storage target {quoted} has unsafe hard links")));
            }
            entries.push(Entry { name: file_name, size: info.len(), modified_ns: super::modified_ns(&info) });
        }
        Ok(entries)
    }
}
