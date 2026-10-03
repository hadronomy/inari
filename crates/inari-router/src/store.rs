use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use ed25519_dalek::VerifyingKey;
use tempfile::NamedTempFile;

use crate::{RouterError, RouterResult, SignedPolicy, VerifiedPolicy};

const MAX_POLICY_BYTES: u64 = 2 * 1024 * 1024;

/// Blocking, single-owner policy storage. Run storage operations outside async workers.
#[derive(Debug)]
pub struct PolicyStore {
    directory: PathBuf,
    _lock: File,
}

impl PolicyStore {
    pub fn open(directory: &Path) -> RouterResult<Self> {
        std::fs::create_dir_all(directory)?;
        let metadata = std::fs::symlink_metadata(directory)?;
        if !metadata.is_dir() {
            return Err(RouterError::InvalidPolicy(
                "policy storage must be a real directory".into(),
            ));
        }
        #[cfg(unix)]
        std::fs::set_permissions(directory, std::os::unix::fs::PermissionsExt::from_mode(0o700))?;
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(directory.join("supervisor.lock"))?;
        lock.try_lock()
            .map_err(|error| RouterError::Io(error.into()))?;
        Ok(Self { directory: directory.to_owned(), _lock: lock })
    }

    pub fn load(&self, key: &VerifyingKey, fleet_id: &str) -> RouterResult<Option<VerifiedPolicy>> {
        let path = self.directory.join("policy.json");
        let file = match File::open(path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        if file.metadata()?.len() > MAX_POLICY_BYTES {
            return Err(RouterError::InvalidPolicy("stored policy exceeds the size limit".into()));
        }
        let mut bytes = Vec::new();
        file.take(MAX_POLICY_BYTES + 1)
            .read_to_end(&mut bytes)?;
        if bytes.len() as u64 > MAX_POLICY_BYTES {
            return Err(RouterError::InvalidPolicy("stored policy exceeds the size limit".into()));
        }
        let signed: SignedPolicy = serde_json::from_slice(&bytes)?;
        signed.verify(key, fleet_id).map(Some)
    }

    pub(crate) fn persist(&self, policy: &VerifiedPolicy) -> RouterResult<()> {
        self.write("policy.json", &serde_json_canonicalizer::to_vec(policy.signed())?)
    }

    pub(crate) fn write(&self, filename: &str, bytes: &[u8]) -> RouterResult<()> {
        let mut temporary = NamedTempFile::new_in(&self.directory)?;
        temporary.write_all(bytes)?;
        temporary.as_file().sync_all()?;
        temporary
            .persist(self.directory.join(filename))
            .map_err(|error| error.error)?;
        // The directory sync makes the rename durable before Router activation.
        File::open(&self.directory)?.sync_all()?;
        Ok(())
    }

    pub(crate) fn config_path(&self) -> PathBuf {
        self.directory.join("router.json")
    }
}
