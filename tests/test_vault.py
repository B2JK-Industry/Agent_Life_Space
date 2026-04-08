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


class TestVaultV2Format:
    """The on-disk format is v2 single-file: ``ALSv2\\n`` magic + 16-byte
    salt + Fernet token. There is no separate salt.bin — every write is
    a single atomic os.replace, so the salt and the encrypted blob can
    never get out of sync."""

    def test_new_vault_writes_v2_header_only_after_first_write(self, tmp_path):
        """Opening an empty vault does not touch disk; the file is
        only created on the first set_secret call, in v2 format."""
        SecretsManager(vault_dir=str(tmp_path), master_key="testkey")
        assert not (tmp_path / "secrets.enc").exists()
        assert not (tmp_path / "salt.bin").exists()

    def test_first_write_creates_v2_file_with_header(self, tmp_path):
        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="testkey")
        mgr.set_secret("K", "v")
        raw = (tmp_path / "secrets.enc").read_bytes()
        assert raw.startswith(b"ALSv2\n")
        # No legacy salt.bin sidecar.
        assert not (tmp_path / "salt.bin").exists()

    def test_v2_file_embeds_random_salt_in_header(self, tmp_path):
        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="testkey")
        mgr.set_secret("K", "v")
        raw = (tmp_path / "secrets.enc").read_bytes()
        header_len = len(b"ALSv2\n")
        salt = raw[header_len:header_len + 16]
        assert len(salt) == 16
        assert salt != SecretsManager._LEGACY_SALT

    def test_two_fresh_vaults_have_different_embedded_salts(self, tmp_path):
        d1 = tmp_path / "vault1"
        d2 = tmp_path / "vault2"
        d1.mkdir()
        d2.mkdir()
        mgr1 = SecretsManager(vault_dir=str(d1), master_key="samekey")
        mgr2 = SecretsManager(vault_dir=str(d2), master_key="samekey")
        mgr1.set_secret("API_KEY", "shared-value")
        mgr2.set_secret("API_KEY", "shared-value")
        bytes1 = (d1 / "secrets.enc").read_bytes()
        bytes2 = (d2 / "secrets.enc").read_bytes()
        # Both have v2 header but different embedded salts → different bytes.
        header_len = len(b"ALSv2\n")
        salt1 = bytes1[header_len:header_len + 16]
        salt2 = bytes2[header_len:header_len + 16]
        assert salt1 != salt2
        assert bytes1 != bytes2

    def test_v2_writes_use_atomic_temp_replace(self, tmp_path):
        """No leftover .tmp file after a normal write."""
        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="testkey")
        mgr.set_secret("K", "v")
        assert not (tmp_path / "secrets.enc.tmp").exists()


class TestLegacyV1Compat:
    """Vaults created before the v2 single-file format must still
    decrypt correctly on first open, and migrate to v2 atomically."""

    def _bootstrap_v1_static_salt(self, tmp_path, payload, master_key="legacy-key"):
        """Write a true pre-1.34 vault: secrets.enc encrypted with the
        static legacy salt, no salt.bin sidecar."""
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
        fernet = Fernet(base64.urlsafe_b64encode(kdf.derive(master_key.encode())))
        (tmp_path / "secrets.enc").write_bytes(fernet.encrypt(orjson.dumps(payload)))

    def _bootstrap_v1_random_salt(self, tmp_path, payload, master_key="legacy-key"):
        """Write a 1.34-era vault: secrets.enc encrypted with a random
        salt that lives in salt.bin (no v2 header)."""
        import base64
        import secrets as sm

        import orjson
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        salt = sm.token_bytes(16)
        (tmp_path / "salt.bin").write_bytes(salt)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        fernet = Fernet(base64.urlsafe_b64encode(kdf.derive(master_key.encode())))
        (tmp_path / "secrets.enc").write_bytes(fernet.encrypt(orjson.dumps(payload)))

    def test_v1_static_salt_vault_reads_and_migrates(self, tmp_path):
        self._bootstrap_v1_static_salt(tmp_path, {"OLD": "old-value"})

        # Pre-condition: file is NOT v2.
        assert not (tmp_path / "secrets.enc").read_bytes().startswith(b"ALSv2\n")

        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")

        # Post-condition: file IS v2 after the open call.
        assert (tmp_path / "secrets.enc").read_bytes().startswith(b"ALSv2\n")
        assert mgr.get_secret("OLD") == "old-value"

    def test_v1_random_salt_vault_reads_and_migrates_drops_salt_file(self, tmp_path):
        self._bootstrap_v1_random_salt(tmp_path, {"KEY": "val"})
        assert (tmp_path / "salt.bin").exists()

        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")

        assert (tmp_path / "secrets.enc").read_bytes().startswith(b"ALSv2\n")
        assert not (tmp_path / "salt.bin").exists(), \
            "salt.bin must be removed after v1→v2 migration"
        assert mgr.get_secret("KEY") == "val"

    def test_v1_wrong_key_does_not_touch_file(self, tmp_path):
        """Opening a legacy vault with the wrong key MUST NOT migrate
        or otherwise modify secrets.enc — that would silently destroy
        recoverable data."""
        self._bootstrap_v1_static_salt(tmp_path, {"KEEP": "important"})
        blob_before = (tmp_path / "secrets.enc").read_bytes()

        SecretsManager(vault_dir=str(tmp_path), master_key="WRONG")

        assert (tmp_path / "secrets.enc").read_bytes() == blob_before
        # Still legacy format — no migration happened.
        assert not (tmp_path / "secrets.enc").read_bytes().startswith(b"ALSv2\n")


class TestVaultV2MigrationCrashSafety:
    """Codex finding (MED): the previous v1→v2 migration wrote salt.bin
    BEFORE swapping secrets.enc, leaving a window where a crash could
    desync salt and blob. The v2 single-file format eliminates that
    window by embedding the salt in the secrets.enc header — every
    write is one atomic os.replace."""

    def _v1_static_salt_blob(self, payload, master_key="legacy-key"):
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
        fernet = Fernet(base64.urlsafe_b64encode(kdf.derive(master_key.encode())))
        return fernet.encrypt(orjson.dumps(payload))

    def test_migration_uses_atomic_swap_no_partial_state(self, tmp_path):
        """The migration must never leave a half-written file or a
        salt.bin without a matching blob. We assert by inspecting the
        post-migration directory: only secrets.enc, no .tmp file."""
        (tmp_path / "secrets.enc").write_bytes(
            self._v1_static_salt_blob({"K": "v"}),
        )

        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")

        # Post-migration files: only secrets.enc, in v2 format. No
        # leftover .tmp file. No salt.bin.
        files = sorted(p.name for p in tmp_path.iterdir())
        assert files == ["secrets.enc"], f"unexpected files left over: {files}"
        assert (tmp_path / "secrets.enc").read_bytes().startswith(b"ALSv2\n")
        assert mgr.get_secret("K") == "v"

    def test_migration_failure_leaves_legacy_blob_untouched(self, tmp_path, monkeypatch):
        """If the v2 write fails (e.g. disk full), the legacy
        secrets.enc must remain intact and the agent must keep
        running with the legacy fernet."""
        (tmp_path / "secrets.enc").write_bytes(
            self._v1_static_salt_blob({"K": "v"}),
        )
        legacy_blob = (tmp_path / "secrets.enc").read_bytes()

        # Force _atomic_write to fail on the migration attempt.
        original_atomic_write = SecretsManager._atomic_write
        call_count = {"n": 0}

        def failing_atomic_write(self, target, data):
            call_count["n"] += 1
            if call_count["n"] == 1:
                msg = "simulated disk full"
                raise OSError(msg)
            return original_atomic_write(self, target, data)

        monkeypatch.setattr(SecretsManager, "_atomic_write", failing_atomic_write)

        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")

        # The legacy blob must be untouched.
        assert (tmp_path / "secrets.enc").read_bytes() == legacy_blob
        # And reads must still work via the legacy fernet.
        assert mgr.get_secret("K") == "v"

    def test_migration_preserves_multiple_secrets(self, tmp_path):
        (tmp_path / "secrets.enc").write_bytes(
            self._v1_static_salt_blob({"K1": "v1", "K2": "v2", "K3": "v3"}),
        )
        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")
        assert mgr.get_secret("K1") == "v1"
        assert mgr.get_secret("K2") == "v2"
        assert mgr.get_secret("K3") == "v3"
        # And new writes go through the v2 format unchanged.
        mgr.set_secret("K4", "v4")
        assert (tmp_path / "secrets.enc").read_bytes().startswith(b"ALSv2\n")
        mgr2 = SecretsManager(vault_dir=str(tmp_path), master_key="legacy-key")
        assert mgr2.get_secret("K4") == "v4"


class TestVaultWrongKeyWriteFailFast:
    """Codex finding (HIGH): a write attempt with the wrong master key
    must NOT silently overwrite the existing secrets.enc. Previously
    _load() returned {} on InvalidToken, set_secret() then re-encrypted
    the empty dict with the wrong key and destroyed the legacy blob.
    The fix raises VaultDecryptionError on any decrypt failure inside
    a write path."""

    def test_wrong_key_set_secret_raises_and_preserves_legacy(self, tmp_path):
        from agent.vault.secrets import VaultDecryptionError

        # Bootstrap with the correct key.
        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="correct-key")
        mgr.set_secret("KEEP", "important-value")
        original_blob = (tmp_path / "secrets.enc").read_bytes()

        # Open with wrong key and try to write.
        mgr_bad = SecretsManager(vault_dir=str(tmp_path), master_key="wrong-key")
        with pytest.raises(VaultDecryptionError):
            mgr_bad.set_secret("NEW", "wrong-value")

        # The encrypted blob on disk MUST be untouched.
        assert (tmp_path / "secrets.enc").read_bytes() == original_blob

        # And the legacy secret MUST still be readable with the correct key.
        mgr_good = SecretsManager(vault_dir=str(tmp_path), master_key="correct-key")
        assert mgr_good.get_secret("KEEP") == "important-value"

    def test_wrong_key_delete_secret_also_raises(self, tmp_path):
        from agent.vault.secrets import VaultDecryptionError

        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="correct-key")
        mgr.set_secret("KEEP", "important-value")
        original_blob = (tmp_path / "secrets.enc").read_bytes()

        mgr_bad = SecretsManager(vault_dir=str(tmp_path), master_key="wrong-key")
        with pytest.raises(VaultDecryptionError):
            mgr_bad.delete_secret("KEEP")

        # delete_secret is also a write — same fail-fast contract.
        assert (tmp_path / "secrets.enc").read_bytes() == original_blob

        mgr_good = SecretsManager(vault_dir=str(tmp_path), master_key="correct-key")
        assert mgr_good.get_secret("KEEP") == "important-value"

    def test_wrong_key_read_path_returns_none_no_crash(self, tmp_path):
        """Read callers (get_secret, list_secrets, has_secret) must
        tolerate decryption failures so the agent can boot with a
        wrong key, log the warning, and let the operator fix .env
        without crashing."""
        mgr = SecretsManager(vault_dir=str(tmp_path), master_key="correct-key")
        mgr.set_secret("KEEP", "important-value")

        mgr_bad = SecretsManager(vault_dir=str(tmp_path), master_key="wrong-key")
        # All read calls must succeed (no exception) and observe an empty vault.
        assert mgr_bad.get_secret("KEEP") is None
        assert mgr_bad.list_secrets() == []
        assert mgr_bad.has_secret("KEEP") is False


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
