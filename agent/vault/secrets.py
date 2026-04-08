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
            # Load existing per-vault salt, or generate a fresh one for
            # new installs. Existing vaults that predate the random-salt
            # change keep working via the legacy static salt fallback.
            salt, used_legacy_fallback = self._load_or_create_salt()
            self._fernet = self._derive_fernet(master_key, salt)
            # If we fell back to the legacy static salt for an existing
            # vault, attempt a one-shot migration to a per-vault random
            # salt. We only proceed if decryption with the legacy salt
            # actually succeeds — otherwise the master key is wrong and
            # we must NOT touch secrets.enc.
            if used_legacy_fallback:
                self._migrate_legacy_salt(master_key)

        # In-memory decrypted cache (cleared on close)
        self._cache: dict[str, str] = {}

    # ── Salt management ────────────────────────────────────────────
    # We accept three states:
    #   1. Brand-new install: no salt.bin AND no secrets.enc → generate
    #      a fresh random salt and persist it.
    #   2. Pre-random-salt install: no salt.bin but secrets.enc EXISTS
    #      → fall back to the legacy static salt so the existing vault
    #      can still be decrypted. Operators are expected to rotate by
    #      re-encrypting (out of band) which then writes salt.bin.
    #   3. Post-random-salt install: salt.bin exists → use it.

    _LEGACY_SALT: ClassVar[bytes] = b"agent-life-space-vault-salt-v1"

    def _load_or_create_salt(self) -> tuple[bytes, bool]:
        """Return ``(salt, used_legacy_fallback)``.

        ``used_legacy_fallback`` is True iff we returned ``_LEGACY_SALT``
        because an existing pre-rotation vault has no ``salt.bin``. The
        caller uses that flag to trigger the one-shot migration that
        re-encrypts the vault with a per-vault random salt.
        """
        if self._salt_file.exists():
            try:
                data = self._salt_file.read_bytes()
                if len(data) >= 16:
                    return data, False
                logger.warning(
                    "vault_salt_too_short",
                    bytes=len(data),
                    hint="Regenerating salt — existing secrets cannot be decrypted",
                )
            except OSError as e:
                logger.warning("vault_salt_read_error", error=str(e))
                # Fall through to legacy/regen path

        if self._secrets_file.exists():
            # Existing vault from pre-salt-rotation era. Return the
            # legacy static salt so the caller can decrypt; the caller
            # is responsible for migrating to a random salt afterwards.
            return self._LEGACY_SALT, True

        # Brand-new vault: generate a strong random salt and persist it.
        salt = secrets_module.token_bytes(16)
        self._persist_salt(salt)
        return salt, False

    def _persist_salt(self, salt: bytes) -> None:
        """Write salt.bin and chmod 600. Logs (but does not raise) on failure."""
        try:
            self._salt_file.write_bytes(salt)
            try:
                self._salt_file.chmod(0o600)
            except OSError:
                # chmod failures are non-fatal on filesystems that
                # ignore POSIX modes (e.g. some FUSE/Windows mounts).
                pass
        except OSError as e:
            logger.warning(
                "vault_salt_persist_failed",
                error=str(e),
                hint="Falling back to in-memory salt; vault may be unrecoverable on restart",
            )

    def _migrate_legacy_salt(self, master_key: str) -> None:
        """Re-encrypt a legacy vault with a fresh per-vault random salt.

        Only runs when ``salt.bin`` is missing AND ``secrets.enc`` exists.
        Steps:
            1. Decrypt the existing vault with the legacy-salt fernet.
               If decryption fails (wrong key, corrupt file) we ABORT
               the migration and leave secrets.enc untouched.
            2. Generate a 16-byte random salt and derive a new fernet.
            3. Re-encrypt the secrets payload with the new fernet.
            4. Persist salt.bin first, then secrets.enc. If salt.bin
               cannot be written, we abort before touching secrets.enc
               so the operator's data stays decryptable on next boot.
        """
        if self._fernet is None:
            return
        if not self._secrets_file.exists():
            return
        try:
            encrypted = self._secrets_file.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
        except InvalidToken:
            logger.warning(
                "vault_legacy_salt_migration_skipped",
                reason="wrong_master_key",
                hint="Existing vault could not be decrypted with the legacy salt; not migrating.",
            )
            return
        except OSError as e:
            logger.warning("vault_legacy_salt_migration_skipped", error=str(e))
            return

        try:
            import orjson
            payload = orjson.loads(decrypted)
        except Exception as e:
            logger.warning("vault_legacy_salt_migration_skipped", parse_error=str(e))
            return
        if not isinstance(payload, dict):
            logger.warning(
                "vault_legacy_salt_migration_skipped",
                reason="payload_not_dict",
            )
            return

        new_salt = secrets_module.token_bytes(16)
        try:
            new_fernet = self._derive_fernet(master_key, new_salt)
        except Exception as e:
            logger.warning("vault_legacy_salt_migration_failed", error=str(e))
            return

        # Persist salt FIRST so a crash between the two writes leaves
        # the operator with a salt that matches the still-legacy
        # secrets.enc — which is wrong. To avoid that, write the new
        # encrypted blob to a temp file, then atomically swap, and only
        # then write salt.bin. Order: secrets.enc.tmp → salt.bin → swap.
        try:
            new_blob = new_fernet.encrypt(orjson.dumps(payload))
        except Exception as e:
            logger.warning("vault_legacy_salt_migration_failed", error=str(e))
            return

        tmp_path = self._secrets_file.with_suffix(self._secrets_file.suffix + ".migrate")
        try:
            tmp_path.write_bytes(new_blob)
        except OSError as e:
            logger.warning("vault_legacy_salt_migration_failed", error=str(e))
            return

        # Persist the new salt next. If this fails the temp file is
        # discarded and the legacy vault stays intact.
        try:
            self._salt_file.write_bytes(new_salt)
            try:
                self._salt_file.chmod(0o600)
            except OSError:
                pass
        except OSError as e:
            logger.warning("vault_legacy_salt_migration_failed", error=str(e))
            try:
                tmp_path.unlink()
            except OSError:
                pass
            return

        # Atomic swap of the encrypted blob.
        try:
            os.replace(tmp_path, self._secrets_file)
        except OSError as e:
            # Roll back the salt file so we don't end up with a
            # mismatched (new salt, old blob) pair.
            logger.error("vault_legacy_salt_migration_failed", error=str(e))
            try:
                self._salt_file.unlink()
            except OSError:
                pass
            try:
                tmp_path.unlink()
            except OSError:
                pass
            return

        # Switch the live fernet to the new key so subsequent
        # reads/writes use the migrated salt.
        self._fernet = new_fernet
        logger.info(
            "vault_legacy_salt_migrated",
            secrets_count=len(payload),
        )

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
            encrypted = self._secrets_file.read_bytes()
            import orjson
            decrypted = self._fernet.decrypt(encrypted)
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
        """Encrypt and save secrets to file."""
        if self._fernet is None:
            logger.error("vault_cannot_encrypt", reason="No master key")
            return

        import orjson
        data = orjson.dumps(secrets)
        encrypted = self._fernet.encrypt(data)
        self._secrets_file.write_bytes(encrypted)

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
