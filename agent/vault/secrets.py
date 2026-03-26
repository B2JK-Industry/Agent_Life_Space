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
from datetime import UTC, datetime
from pathlib import Path

import structlog
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = structlog.get_logger(__name__)

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
            self._fernet = self._derive_fernet(master_key)

        # In-memory decrypted cache (cleared on close)
        self._cache: dict[str, str] = {}

    @staticmethod
    def _derive_fernet(master_key: str) -> Fernet:
        """Derive a Fernet key from a master password using PBKDF2."""
        salt = b"agent-life-space-vault-salt-v1"  # Static salt (vault is local)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(master_key.encode()))
        return Fernet(key)

    def _load(self) -> dict[str, str]:
        """Load and decrypt secrets from file."""
        if not self._secrets_file.exists():
            return {}

        if self._fernet is None:
            logger.error("vault_cannot_decrypt", reason="No master key")
            return {}

        try:
            encrypted = self._secrets_file.read_bytes()
            import orjson
            decrypted = self._fernet.decrypt(encrypted)
            return orjson.loads(decrypted)
        except InvalidToken:
            logger.error("vault_decryption_failed", reason="Wrong master key")
            return {}
        except Exception as e:
            logger.error("vault_load_error", error=str(e))
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
        secrets = self._load()
        secrets[name] = value
        self._save(secrets)
        self._cache[name] = value

        self._audit("set", name)
        # NEVER log the actual value
        logger.info("vault_secret_set", name=name)

    def get_secret(self, name: str) -> str | None:
        """Retrieve a secret. Returns None if not found."""
        # Check cache first
        if name in self._cache:
            self._audit("get_cached", name)
            return self._cache[name]

        secrets = self._load()
        value = secrets.get(name)
        if value is not None:
            self._cache[name] = value
            self._audit("get", name)
        else:
            self._audit("get_miss", name)

        return value

    def delete_secret(self, name: str) -> bool:
        """Delete a secret."""
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
        """List secret names (NOT values)."""
        secrets = self._load()
        self._audit("list", "all")
        return list(secrets.keys())

    def has_secret(self, name: str) -> bool:
        """Check if a secret exists without loading its value."""
        secrets = self._load()
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
