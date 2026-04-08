"""
Test scenarios for Vault/Secrets Manager.

SECURITY CRITICAL:
1. Secrets are encrypted at rest
2. Wrong master key cannot decrypt
3. Secrets are never in logs
4. Audit trail tracks all access
5. Cache cleared properly
6. No secret is ever in git
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agent.vault.secrets import SecretsManager


@pytest.fixture
def vault():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = SecretsManager(vault_dir=tmpdir, master_key="test-master-key-123")
        yield mgr


class TestSecretStorage:
    """Secrets stored and retrieved correctly."""

    def test_set_and_get(self, vault: SecretsManager) -> None:
        vault.set_secret("ANTHROPIC_API_KEY", "sk-ant-xxx123")
        value = vault.get_secret("ANTHROPIC_API_KEY")
        assert value == "sk-ant-xxx123"

    def test_get_nonexistent(self, vault: SecretsManager) -> None:
        assert vault.get_secret("NONEXISTENT") is None

    def test_delete_secret(self, vault: SecretsManager) -> None:
        vault.set_secret("TO_DELETE", "temp_value")
        assert vault.delete_secret("TO_DELETE") is True
        assert vault.get_secret("TO_DELETE") is None

    def test_list_secrets(self, vault: SecretsManager) -> None:
        vault.set_secret("KEY_A", "val_a")
        vault.set_secret("KEY_B", "val_b")
        names = vault.list_secrets()
        assert "KEY_A" in names
        assert "KEY_B" in names
        # Values should NOT be in the list
        assert "val_a" not in names

    def test_has_secret(self, vault: SecretsManager) -> None:
        vault.set_secret("EXISTS", "value")
        assert vault.has_secret("EXISTS") is True
        assert vault.has_secret("NOPE") is False

    def test_overwrite_secret(self, vault: SecretsManager) -> None:
        vault.set_secret("KEY", "old_value")
        vault.set_secret("KEY", "new_value")
        assert vault.get_secret("KEY") == "new_value"


class TestEncryption:
    """Secrets must be encrypted on disk."""

    def test_encrypted_at_rest(self) -> None:
        """Raw file should NOT contain plaintext secret."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SecretsManager(vault_dir=tmpdir, master_key="test-key")
            mgr.set_secret("API_KEY", "super-secret-value-12345")

            # Read raw file
            secrets_file = os.path.join(tmpdir, "secrets.enc")
            with open(secrets_file, "rb") as f:
                raw = f.read()
            assert b"super-secret-value-12345" not in raw

    def test_wrong_key_cannot_decrypt(self) -> None:
        """Different master key cannot read secrets."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr1 = SecretsManager(vault_dir=tmpdir, master_key="correct-key")
            mgr1.set_secret("SECRET", "my-value")

            mgr2 = SecretsManager(vault_dir=tmpdir, master_key="wrong-key")
            value = mgr2.get_secret("SECRET")
            assert value is None  # Decryption fails, returns None

    def test_same_key_can_decrypt(self) -> None:
        """Same master key across instances works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr1 = SecretsManager(vault_dir=tmpdir, master_key="same-key")
            mgr1.set_secret("SECRET", "shared-value")

            mgr2 = SecretsManager(vault_dir=tmpdir, master_key="same-key")
            assert mgr2.get_secret("SECRET") == "shared-value"


class TestAuditTrail:
    """All secret access is audited."""

    def test_audit_on_set(self, vault: SecretsManager) -> None:
        vault.set_secret("KEY", "val")
        log = vault.get_audit_log()
        assert any(e["action"] == "set" and e["name"] == "KEY" for e in log)

    def test_audit_on_get(self, vault: SecretsManager) -> None:
        vault.set_secret("KEY", "val")
        vault.clear_cache()
        vault.get_secret("KEY")
        log = vault.get_audit_log()
        assert any(e["action"] == "get" and e["name"] == "KEY" for e in log)

    def test_audit_on_miss(self, vault: SecretsManager) -> None:
        vault.get_secret("NONEXISTENT")
        log = vault.get_audit_log()
        assert any(e["action"] == "get_miss" for e in log)

    def test_audit_has_timestamp(self, vault: SecretsManager) -> None:
        vault.set_secret("KEY", "val")
        log = vault.get_audit_log()
        assert "timestamp" in log[0]


class TestCache:
    """In-memory cache for performance."""

    def test_cache_hit(self, vault: SecretsManager) -> None:
        vault.set_secret("CACHED", "value")
        # Second get should hit cache
        vault.get_secret("CACHED")
        log = vault.get_audit_log()
        assert any(e["action"] == "get_cached" for e in log)

    def test_cache_cleared(self, vault: SecretsManager) -> None:
        vault.set_secret("KEY", "val")
        vault.clear_cache()
        vault.get_secret("KEY")
        log = vault.get_audit_log()
        # After clear, should be a regular get, not cached
        last_get = [e for e in log if "get" in e["action"]][-1]
        assert last_get["action"] == "get"


class TestKeyGeneration:
    def test_generate_key(self) -> None:
        key = SecretsManager.generate_key()
        assert isinstance(key, str)
        assert len(key) > 20


class TestVaultSaltRotation:
    """A fresh vault must use a per-vault random salt, not the legacy
    static salt. Pre-existing vaults must keep working via the legacy
    fallback."""

    def test_new_vault_persists_random_salt(self, tmp_path):
        SecretsManager(vault_dir=str(tmp_path), master_key="testkey")
        salt_file = tmp_path / "salt.bin"
        assert salt_file.exists(), "fresh vault should create salt.bin"
        salt_bytes = salt_file.read_bytes()
        assert len(salt_bytes) >= 16, "salt should be at least 16 bytes"
        # And the salt MUST NOT be the legacy static value.
        assert salt_bytes != SecretsManager._LEGACY_SALT

    def test_two_fresh_vaults_have_different_salts(self, tmp_path):
        d1 = tmp_path / "v1"
        d2 = tmp_path / "v2"
        d1.mkdir()
        d2.mkdir()
        mgr1 = SecretsManager(vault_dir=str(d1), master_key="samekey")
        mgr2 = SecretsManager(vault_dir=str(d2), master_key="samekey")
        salt1 = (d1 / "salt.bin").read_bytes()
        salt2 = (d2 / "salt.bin").read_bytes()
        assert salt1 != salt2, "two fresh vaults must have independent salts"
        # And consequently the same secret encrypts to different bytes.
        mgr1.set_secret("API_KEY", "shared-value")
        mgr2.set_secret("API_KEY", "shared-value")
        bytes1 = (d1 / "secrets.enc").read_bytes()
        bytes2 = (d2 / "secrets.enc").read_bytes()
        assert bytes1 != bytes2

    def test_legacy_vault_without_salt_file_keeps_working(self, tmp_path):
        """A vault that was created before the random-salt change has
        only secrets.enc and no salt.bin. The new code must fall back
        to the legacy static salt so it can still decrypt."""
        # Bootstrap a legacy vault by writing secrets first, then
        # deleting salt.bin (simulating a pre-rotation install).
        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")
        mgr.set_secret("LEGACY", "value-1")
        # Wipe the per-vault salt and replace with the legacy fixture by
        # encrypting with the legacy salt directly.
        (tmp_path / "salt.bin").unlink()
        # Re-bootstrap with the same key so encryption uses legacy salt.
        import base64

        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=SecretsManager._LEGACY_SALT,
            iterations=480000,
        )
        legacy_key = base64.urlsafe_b64encode(kdf.derive(b"legacy-key"))
        legacy_fernet = Fernet(legacy_key)
        import orjson
        legacy_payload = legacy_fernet.encrypt(orjson.dumps({"LEGACY": "value-1"}))
        (tmp_path / "secrets.enc").write_bytes(legacy_payload)

        # Now open the vault again — no salt.bin, but secrets.enc exists.
        mgr2 = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")
        assert mgr2.get_secret("LEGACY") == "value-1"


class TestLegacyVaultSaltMigration:
    """Regression: opening a legacy vault with the correct master key
    must MIGRATE it to a per-vault random salt, not just keep falling
    back to the static legacy salt forever."""

    def _bootstrap_legacy_vault(self, tmp_path, secrets_dict):
        """Write a true pre-rotation vault: secrets.enc encrypted with
        the legacy static salt, no salt.bin file present."""
        import base64

        import orjson
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=SecretsManager._LEGACY_SALT,
            iterations=480000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(b"legacy-key"))
        fernet = Fernet(key)
        blob = fernet.encrypt(orjson.dumps(secrets_dict))
        (tmp_path / "secrets.enc").write_bytes(blob)
        assert not (tmp_path / "salt.bin").exists()

    def test_correct_key_migrates_to_random_salt(self, tmp_path):
        self._bootstrap_legacy_vault(tmp_path, {"OLD_KEY": "old_value"})
        blob_before = (tmp_path / "secrets.enc").read_bytes()

        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")

        # salt.bin must now exist with a random (non-legacy) salt.
        salt_path = tmp_path / "salt.bin"
        assert salt_path.exists(), "migration must persist salt.bin"
        salt_bytes = salt_path.read_bytes()
        assert len(salt_bytes) >= 16
        assert salt_bytes != SecretsManager._LEGACY_SALT

        # secrets.enc must have been re-encrypted with the new salt.
        blob_after = (tmp_path / "secrets.enc").read_bytes()
        assert blob_after != blob_before, "vault must be re-encrypted under new salt"

        # And the secret remains readable on this instance and after reopen.
        assert mgr.get_secret("OLD_KEY") == "old_value"
        mgr2 = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")
        assert mgr2.get_secret("OLD_KEY") == "old_value"

    def test_wrong_key_does_not_migrate(self, tmp_path):
        """If the master key is wrong, decryption fails and we MUST NOT
        touch secrets.enc or write salt.bin — otherwise the next boot
        with the correct key would be unrecoverable."""
        self._bootstrap_legacy_vault(tmp_path, {"OLD_KEY": "old_value"})
        blob_before = (tmp_path / "secrets.enc").read_bytes()

        SecretsManager(vault_dir=str(tmp_path), master_key="WRONG-KEY")

        assert not (tmp_path / "salt.bin").exists(), \
            "wrong key must not produce a salt.bin"
        assert (tmp_path / "secrets.enc").read_bytes() == blob_before, \
            "wrong key must not touch the encrypted blob"

    def test_migration_preserves_multiple_secrets(self, tmp_path):
        self._bootstrap_legacy_vault(
            tmp_path,
            {"K1": "v1", "K2": "v2", "K3": "v3"},
        )
        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")
        assert mgr.get_secret("K1") == "v1"
        assert mgr.get_secret("K2") == "v2"
        assert mgr.get_secret("K3") == "v3"
        # And the post-migration vault stores new secrets under the new salt.
        mgr.set_secret("K4", "v4")
        mgr2 = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")
        assert mgr2.get_secret("K4") == "v4"


class TestFinanceTrackerLock:
    """Concurrent approve calls on the same transaction must serialise."""

    @pytest.mark.asyncio
    async def test_double_approve_raises_on_second_call(self, tmp_path):
        import asyncio

        from agent.finance.tracker import FinanceTracker

        db_path = str(tmp_path / "finance.db")
        tracker = FinanceTracker(db_path=db_path)
        await tracker.initialize()

        tx = await tracker.propose_expense(
            amount_usd=1.0,
            description="test",
            category="api_subscription",
            rationale="unit test",
        )

        # Two concurrent approves on the same tx_id. Without the lock
        # both would observe status=PROPOSED and the second would
        # silently succeed. With the lock the second must raise.
        results = await asyncio.gather(
            tracker.approve(tx.id),
            tracker.approve(tx.id),
            return_exceptions=True,
        )
        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], ValueError)
        assert "cannot approve" in str(failures[0])
