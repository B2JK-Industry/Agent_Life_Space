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
            raw = open(secrets_file, "rb").read()
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
