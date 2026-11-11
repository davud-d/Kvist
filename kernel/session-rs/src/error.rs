//! The failures of a session operation, kept apart the way the Python core
//! keeps its exception classes apart: callers decide by kind what to skip.

use std::io;
use std::path::Path;

use crate::pytext;

#[derive(Debug)]
pub enum OpError {
    /// A request that cannot be served (Python's `ValueError`).
    Invalid(String),
    /// `JsonStorageError`: the storage could not be read or written.
    Storage(String),
    /// `JsonStorageCorruptError`: the document and its recovery copy are unusable.
    Corrupt(String),
    /// `JsonStorageSecurityError`: a path or file identity breaks the storage boundary.
    Security(String),
    /// An operating-system error outside the storage layer (`OSError`).
    Os(String),
}

pub type Result<T> = std::result::Result<T, OpError>;

impl OpError {
    pub fn message(&self) -> &str {
        match self {
            OpError::Invalid(text)
            | OpError::Storage(text)
            | OpError::Corrupt(text)
            | OpError::Security(text)
            | OpError::Os(text) => text,
        }
    }

    pub fn is_corrupt(&self) -> bool {
        matches!(self, OpError::Corrupt(_))
    }

    /// Any `JsonStorageError`, its subclasses included.
    pub fn is_storage(&self) -> bool {
        matches!(self, OpError::Storage(_) | OpError::Corrupt(_) | OpError::Security(_))
    }

    /// An `OSError` worded the way Python words it, so a frontend shows the
    /// same text whichever implementation answered.
    pub fn os(error: &io::Error, path: &Path) -> OpError {
        let Some(code) = error.raw_os_error() else {
            return OpError::Os(error.to_string());
        };
        let reason = error.to_string();
        let reason = reason.split(" (os error").next().unwrap_or(&reason);
        OpError::Os(format!("[Errno {code}] {reason}: {}", pytext::repr(&path.to_string_lossy())))
    }
}
