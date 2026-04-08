"""
Agent Life Space — Secrets Manager (Vault)

Secure storage for API keys and sensitive configuration.
Uses encrypted file storage with Fernet symmetric encryption.

Security principles:
    - Secrets NEVER appear in logs (structlog processor strips them)
    - Secrets NEVER in git (vault/ is gitignored)
    - Encryption at rest (Fernet/AES-128-CBC)
    - Master key derived from environment variable or keyfile
    - No hardcoded secrets anywhere in the codebase
    - Access audit trail
"""

from __future__ import annotations

import base64
import os
import secrets as secrets_module
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, cast

import structlog
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = structlog.get_logger(__name__)


class VaultDecryptionError(RuntimeError):
    """Raised when an existing secrets.enc cannot be decrypted with the
    current master key. The vault MUST refuse to write in this state —
    a write would silently overwrite the legacy blob and destroy any
    secrets the operator could still recover with the correct key."""


class SecretsManager:
    """
    Encrypted secrets storage.

    Secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256).
    The encryption key is derived from a master password/key.
    """

    def __init__(
        self,
        vault_dir: str = "agent/vault",
        master_key: str | None = None,
    ) -> None:
        self._vault_dir = Path(vault_dir)
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        self._secrets_file = self._vault_dir / "secrets.enc"
        self._salt_file = self._vault_dir / "salt.bin"
        self._audit_log: list[dict[str, str]] = []
        self._max_audit_entries = 1000
        # _current_salt is the salt used to derive _fernet. After init
        # it always reflects the salt that is (or will be) embedded in
        # the on-disk v2 header, so _save() can rebuild the file
        # without re-reading anything.
        self._current_salt: bytes = b""

        # Derive encryption key
        if master_key is None:
            master_key = os.environ.get("AGENT_VAULT_KEY", "")

        if not master_key:
            # If secrets file exists, refuse to start without key (data at risk)
            if self._secrets_file.exists():
                msg = (
                    "AGENT_VAULT_KEY not set but encrypted secrets exist. "
                    "Set the environment variable to decrypt, or delete "
                    f"{self._secrets_file} to start fresh."
                )
                raise RuntimeError(msg)
            logger.warning(
                "vault_no_master_key",
                hint="Set AGENT_VAULT_KEY environment variable for encryption. "
                     "Vault is empty — will require key before storing secrets.",
            )
            self._fernet = None
        else:
            self._fernet = self._open_or_init_vault(master_key)

        # In-memory decrypted cache (cleared on close)
        self._cache: dict[str, str] = {}

    # ── Vault on-disk format ───────────────────────────────────────
    #
    # v2 (current): single file ``secrets.enc`` whose first bytes are:
    #
    #     b"ALSv2\n"   (6 bytes magic)
    #     salt         (16 bytes random per-vault)
    #     fernet_token (rest of the file — Fernet/AES-128-CBC + HMAC)
    #
    # v1 (legacy):   ``secrets.enc`` is a raw fernet token with no
    #                header. The salt lives either in a separate
    #                ``salt.bin`` (post-1.34 random salt era) or is
    #                ``_LEGACY_SALT`` (pre-1.34 static salt era).
    #
    # The v2 format eliminates the multi-file crash window: every
    # write is a single atomic ``os.replace`` of ``secrets.enc.tmp``,
    # so the salt and the encrypted blob can never get out of sync.
    # ``salt.bin`` becomes obsolete — we delete it after a successful
    # v1→v2 migration.

    _V2_HEADER: ClassVar[bytes] = b"ALSv2\n"
    _V2_SALT_LEN: ClassVar[int] = 16
    _LEGACY_SALT: ClassVar[bytes] = b"agent-life-space-vault-salt-v1"

    def _open_or_init_vault(self, master_key: str) -> Fernet:
        """Open the existing vault or initialise a fresh one.

        After this returns, ``self._current_salt`` holds the salt that
        is (or will be) embedded in the v2 header on disk, and the
        returned Fernet is derived from it. The on-disk format is
        guaranteed to be v2 in two cases:

            * fresh install (we wrote the empty v2 file)
            * legacy install where the master key was correct (we
              migrated atomically — see ``_migrate_to_v2``)

        If the vault is legacy AND the master key is wrong, we set up
        a Fernet anyway so write callers can fail-fast on the next
        ``_load()``. We do NOT touch ``secrets.enc`` in that case —
        the operator can fix ``.env`` and recover.
        """
        # Brand-new vault: nothing on disk, generate a fresh salt and
        # persist an empty v2 file so the first write does not need a
        # special path.
        if not self._secrets_file.exists():
            new_salt = secrets_module.token_bytes(self._V2_SALT_LEN)
            self._current_salt = new_salt
            fernet = self._derive_fernet(master_key, new_salt)
            # Don't write an empty v2 file — set_secret() will create
            # it on first write. This avoids touching disk when the
            # operator is just probing the vault.
            self._cleanup_legacy_salt_file()
            return fernet

        try:
            raw = self._secrets_file.read_bytes()
        except OSError as e:
            logger.error("vault_read_error", error=str(e))
            # Treat as fresh install — caller will get errors on
            # subsequent reads/writes via the normal _load path.
            new_salt = secrets_module.token_bytes(self._V2_SALT_LEN)
            self._current_salt = new_salt
            return self._derive_fernet(master_key, new_salt)

        # v2 path: header present → extract embedded salt.
        if raw.startswith(self._V2_HEADER):
            header_len = len(self._V2_HEADER)
            if len(raw) < header_len + self._V2_SALT_LEN:
                logger.error(
                    "vault_v2_truncated",
                    bytes=len(raw),
                    hint="secrets.enc is shorter than the v2 header — refusing to touch it",
                )
                # Use a synthetic salt so write paths fail-fast.
                self._current_salt = secrets_module.token_bytes(self._V2_SALT_LEN)
                return self._derive_fernet(master_key, self._current_salt)
            salt = raw[header_len:header_len + self._V2_SALT_LEN]
            self._current_salt = salt
            self._cleanup_legacy_salt_file()
            return self._derive_fernet(master_key, salt)

        # v1 path: no header. Locate the salt and try to decrypt.
        legacy_salt = self._locate_legacy_salt()
        legacy_fernet = self._derive_fernet(master_key, legacy_salt)
        try:
            plaintext = legacy_fernet.decrypt(raw)
        except InvalidToken:
            # Wrong master key. Set _current_salt so subsequent reads
            # can still try the same legacy fernet (and the read path
            # will surface a clean VaultDecryptionError); critically,
            # we do NOT touch the on-disk file in this branch.
            logger.error(
                "vault_legacy_decrypt_failed",
                hint="Wrong master key — leaving legacy secrets.enc untouched",
            )
            self._current_salt = legacy_salt
            return legacy_fernet
        except Exception as e:  # noqa: BLE001 - last-resort safety
            logger.error("vault_legacy_decrypt_unexpected_error", error=str(e))
            self._current_salt = legacy_salt
            return legacy_fernet

        # Successful legacy decrypt → migrate to v2 atomically.
        return self._migrate_to_v2(master_key, plaintext)

    def _locate_legacy_salt(self) -> bytes:
        """Pick the salt for a v1 vault: prefer ``salt.bin`` (post-1.34
        installs), fall back to the static legacy salt (pre-1.34)."""
        if self._salt_file.exists():
            try:
                data = self._salt_file.read_bytes()
                if len(data) >= self._V2_SALT_LEN:
                    return data
                logger.warning(
                    "vault_salt_too_short",
                    bytes=len(data),
                    hint="Falling back to legacy static salt",
                )
            except OSError as e:
                logger.warning("vault_salt_read_error", error=str(e))
        return self._LEGACY_SALT

    def _migrate_to_v2(self, master_key: str, plaintext: bytes) -> Fernet:
        """Re-encrypt the legacy plaintext into v2 format.

        Single-file atomic operation:

            1. Generate a fresh random salt.
            2. Derive a fresh Fernet from (master_key, salt).
            3. Build the v2 blob: header + salt + fernet.encrypt(plaintext).
            4. Write to ``secrets.enc.tmp``.
            5. fsync the temp file.
            6. ``os.replace`` the temp file over ``secrets.enc``.
            7. fsync the parent directory.
            8. Delete ``salt.bin`` (best effort) — obsolete after v2.

        If any of steps 4-8 fails, the legacy ``secrets.enc`` is still
        intact because we never overwrite it directly. We log the
        error and return the *legacy* Fernet so the agent can keep
        running (read-only) until the operator investigates.
        """
        new_salt = secrets_module.token_bytes(self._V2_SALT_LEN)
        new_fernet = self._derive_fernet(master_key, new_salt)
        try:
            token = new_fernet.encrypt(plaintext)
        except Exception as e:  # noqa: BLE001
            logger.error("vault_v2_migration_encrypt_failed", error=str(e))
            self._current_salt = self._locate_legacy_salt()
            return self._derive_fernet(master_key, self._current_salt)

        v2_blob = self._V2_HEADER + new_salt + token
        try:
            self._atomic_write(self._secrets_file, v2_blob)
        except OSError as e:
            logger.error(
                "vault_v2_migration_write_failed",
                error=str(e),
                hint="Legacy secrets.enc is untouched; agent continues with legacy fernet",
            )
            self._current_salt = self._locate_legacy_salt()
            return self._derive_fernet(master_key, self._current_salt)

        # On-disk format is now v2. Drop the obsolete salt.bin so the
        # next boot does not see a confusing mix of v1 and v2 markers.
        self._cleanup_legacy_salt_file()
        self._current_salt = new_salt
        logger.info(
            "vault_migrated_to_v2_single_file_format",
            hint="Vault re-encrypted with random salt embedded in secrets.enc header.",
        )
        return new_fernet

    def _cleanup_legacy_salt_file(self) -> None:
        """Best-effort removal of ``salt.bin`` after a v2 migration.

        We do this so future boots don't see a stale salt.bin and try
        to interpret it. Failure is non-fatal — the v2 reader does not
        consult salt.bin at all.
        """
        if not self._salt_file.exists():
            return
        try:
            self._salt_file.unlink()
            logger.info("vault_legacy_salt_file_removed")
        except OSError as e:
            logger.warning("vault_legacy_salt_file_cleanup_failed", error=str(e))

    def _atomic_write(self, target: Path, data: bytes) -> None:
        """Write ``data`` to ``target`` atomically.

        Steps:
            1. Open ``target.tmp`` with O_WRONLY|O_CREAT|O_TRUNC mode 0600.
            2. ``os.write`` the data.
            3. ``os.fsync`` the file descriptor (durability of contents).
            4. Close.
            5. ``os.replace(tmp, target)`` — POSIX atomic rename.
            6. Best-effort fsync of the parent directory so the rename
               itself is durable across power loss.

        On any failure we attempt to remove the temp file and re-raise.
        """
        tmp = target.with_suffix(target.suffix + ".tmp")
        # Ensure no stale temp from a prior crash.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(tmp, flags, 0o600)
        try:
            written = 0
            view = memoryview(data)
            while written < len(view):
                n = os.write(fd, view[written:])
                if n <= 0:
                    msg = "os.write returned 0 — disk full?"
                    raise OSError(msg)
                written += n
            os.fsync(fd)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        else:
            os.close(fd)
        try:
            os.replace(tmp, target)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        # Best-effort directory fsync to make the rename durable on
        # POSIX. On filesystems / platforms where this is not
        # supported (Windows, some FUSE) the call is a no-op.
        try:
            dir_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    @property
    def is_ready(self) -> bool:
        """Whether the vault has an encryption key and can store/retrieve secrets."""
        return self._fernet is not None

    @staticmethod
    def _derive_fernet(master_key: str, salt: bytes) -> Fernet:
        """Derive a Fernet key from a master password using PBKDF2.

        ``salt`` is per-vault and lives in ``salt.bin`` next to the
        encrypted secrets file. New installs get a 16-byte random salt;
        legacy installs that predate this change keep using the static
        salt for backward compatibility (see ``_load_or_create_salt``).
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(master_key.encode()))
        return Fernet(key)

    def _load(self, *, allow_missing: bool = True) -> dict[str, str]:
        """Load and decrypt secrets from file.

        ``allow_missing`` controls behaviour when there is no
        secrets.enc file at all: callers that read (get/list) tolerate
        a missing vault and get an empty dict; callers that write
        (set/delete) also tolerate it because the first write will
        create the file.

        Format detection: a v2 header (``ALSv2\\n``) means strip the
        header + salt before passing the rest to Fernet. Anything else
        is treated as legacy v1 — the entire file goes to Fernet.
        Both branches share the same fernet object set up at __init__.

        On *decryption* failure (wrong master key, corrupt blob) we
        raise ``VaultDecryptionError`` regardless of caller. Returning
        ``{}`` here was the bug Codex flagged: a subsequent ``_save``
        would re-encrypt the empty dict with the wrong key and
        permanently destroy the legacy data the operator could
        otherwise recover.
        """
        if not self._secrets_file.exists():
            if allow_missing:
                return {}
            msg = (
                "Vault file does not exist. This is unexpected for a "
                "non-empty vault — refuse to proceed."
            )
            raise VaultDecryptionError(msg)

        if self._fernet is None:
            msg = "Cannot decrypt vault: no master key configured."
            raise VaultDecryptionError(msg)

        try:
            raw = self._secrets_file.read_bytes()
            if raw.startswith(self._V2_HEADER):
                blob = raw[len(self._V2_HEADER) + self._V2_SALT_LEN:]
            else:
                blob = raw
            import orjson
            decrypted = self._fernet.decrypt(blob)
            return cast("dict[str, str]", orjson.loads(decrypted))
        except InvalidToken as e:
            logger.error("vault_decryption_failed", reason="Wrong master key")
            msg = (
                "Vault decryption failed (wrong master key or corrupted "
                "secrets.enc). Refusing to proceed — a write in this "
                "state would silently overwrite the existing blob and "
                "destroy any secrets recoverable with the correct key."
            )
            raise VaultDecryptionError(msg) from e
        except Exception as e:
            logger.error("vault_load_error", error=str(e))
            msg = f"Vault load failed: {e}"
            raise VaultDecryptionError(msg) from e

    def _safe_load_for_read(self) -> dict[str, str]:
        """Read-side helper that swallows decryption errors so get/list
        callers do not crash when the master key is wrong. They simply
        observe an empty vault. WRITE callers must use ``_load()``
        directly so they fail-fast."""
        try:
            return self._load()
        except VaultDecryptionError:
            return {}

    def _save(self, secrets: dict[str, str]) -> None:
        """Encrypt and atomically save secrets in v2 format.

        Format: ``ALSv2\\n`` magic + 16-byte salt + Fernet token.
        Written via ``_atomic_write`` (temp file + fsync + os.replace),
        so a crash mid-write leaves either the previous good blob or
        the new good blob — never a partial / mismatched state.
        """
        if self._fernet is None:
            logger.error("vault_cannot_encrypt", reason="No master key")
            return
        if not self._current_salt:
            # Should never happen — _open_or_init_vault always sets it.
            # Generate one as a last resort so we don't write a
            # malformed v2 file.
            self._current_salt = secrets_module.token_bytes(self._V2_SALT_LEN)

        import orjson
        plaintext = orjson.dumps(secrets)
        token = self._fernet.encrypt(plaintext)
        v2_blob = self._V2_HEADER + self._current_salt + token
        self._atomic_write(self._secrets_file, v2_blob)

    def set_secret(self, name: str, value: str) -> None:
        """Store a secret securely."""
        if self._fernet is None:
            msg = "Cannot store secrets without encryption key. Set AGENT_VAULT_KEY."
            raise RuntimeError(msg)
        if not name or not name.strip():
            msg = "Secret name cannot be empty"
            raise ValueError(msg)
        if not value:
            msg = "Secret value cannot be empty"
            raise ValueError(msg)
        # CRITICAL: this MUST be _load() (not _safe_load_for_read), so
        # that wrong-key writes fail-fast with VaultDecryptionError
        # instead of silently overwriting the existing legacy blob.
        secrets = self._load()
        secrets[name] = value
        self._save(secrets)
        self._cache[name] = value

        self._audit("set", name)
        # NEVER log the actual value
        logger.info("vault_secret_set", name=name)

    def get_secret(self, name: str) -> str | None:
        """Retrieve a secret. Returns None if not found.

        Read path uses ``_safe_load_for_read`` so that an operator who
        boots the agent with a wrong key gets ``None`` from get_secret
        instead of a hard crash. Subsequent writes still fail-fast
        because they go through ``_load()`` directly.
        """
        # Check cache first
        if name in self._cache:
            self._audit("get_cached", name)
            # Cache hits are too noisy for long-term retention.
            logger.debug("vault_secret_get_cached", name=name)
            return self._cache[name]

        secrets = self._safe_load_for_read()
        value = secrets.get(name)
        if value is not None:
            self._cache[name] = value
            self._audit("get", name)
            logger.info("vault_secret_get", name=name)
        else:
            self._audit("get_miss", name)
            # A get_miss is interesting: someone asked for a secret
            # that does not exist. Worth keeping in long retention so
            # the operator can spot configuration drift.
            logger.warning("vault_secret_get_miss", name=name)

        return value

    def delete_secret(self, name: str) -> bool:
        """Delete a secret. Like ``set_secret``, this is a write
        operation and MUST fail-fast on a wrong master key — otherwise
        a delete in this state would re-encrypt the legacy blob with
        the wrong key and destroy recoverable data."""
        secrets = self._load()
        if name in secrets:
            del secrets[name]
            self._save(secrets)
            self._cache.pop(name, None)
            self._audit("delete", name)
            logger.info("vault_secret_deleted", name=name)
            return True
        return False

    def list_secrets(self) -> list[str]:
        """List secret names (NOT values). Read path."""
        secrets = self._safe_load_for_read()
        self._audit("list", "all")
        return list(secrets.keys())

    def has_secret(self, name: str) -> bool:
        """Check if a secret exists without loading its value. Read path."""
        secrets = self._safe_load_for_read()
        return name in secrets

    def _audit(self, action: str, name: str) -> None:
        """Record an audit trail entry. Bounded to prevent unbounded growth."""
        if len(self._audit_log) >= self._max_audit_entries:
            self._audit_log.pop(0)
        self._audit_log.append({
            "action": action,
            "name": name,
            "timestamp": datetime.now(UTC).isoformat(),
        })

    def get_audit_log(self) -> list[dict[str, str]]:
        return list(self._audit_log)

    def clear_cache(self) -> None:
        """Clear in-memory secret cache."""
        self._cache.clear()

    @staticmethod
    def generate_key() -> str:
        """Generate a new random master key."""
        return Fernet.generate_key().decode()
