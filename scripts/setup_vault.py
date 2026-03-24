#!/usr/bin/env python3
"""
Setup vault on server — run ONCE to initialize encrypted storage.

Usage:
    cd ~/agent-life-space
    .venv/bin/python scripts/setup_vault.py

This will:
    1. Generate a master key (if not exists)
    2. Store it in ~/.agent-vault-key (chmod 600)
    3. Create wallet addresses (ETH + BTC)
    4. Store private keys in encrypted vault
    5. Print public addresses (safe to share)

IMPORTANT:
    - Run this on the SERVER only
    - Never commit ~/.agent-vault-key to git
    - Back up the master key somewhere safe (offline)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    key_file = Path.home() / ".agent-vault-key"
    vault_dir = Path.home() / "agent-life-space" / "agent" / "vault"

    # Step 1: Master key
    if key_file.exists():
        master_key = key_file.read_text().strip()
        print(f"[OK] Master key exists: {key_file}")
    else:
        from agent.vault.secrets import SecretsManager
        master_key = SecretsManager.generate_key()
        key_file.write_text(master_key)
        os.chmod(str(key_file), 0o600)  # Owner read/write only
        print(f"[NEW] Master key generated: {key_file}")
        print(f"      BACK THIS UP: {master_key}")

    # Step 2: Initialize vault
    from agent.vault.secrets import SecretsManager
    vault = SecretsManager(vault_dir=str(vault_dir), master_key=master_key)

    # Step 3: Check if wallets already exist
    existing = vault.list_secrets()
    if "ETH_PRIVATE_KEY" in existing:
        print("[OK] ETH wallet already exists in vault")
        print(f"     Address: {vault.get_secret('ETH_ADDRESS')}")
    else:
        eth_address, eth_key = _create_eth_wallet()
        vault.set_secret("ETH_PRIVATE_KEY", eth_key)
        vault.set_secret("ETH_ADDRESS", eth_address)
        print(f"[NEW] ETH wallet created")
        print(f"      Address: {eth_address}")

    if "BTC_PRIVATE_KEY" in existing:
        print("[OK] BTC wallet already exists in vault")
        print(f"     Address: {vault.get_secret('BTC_ADDRESS')}")
    else:
        btc_address, btc_key = _create_btc_wallet()
        vault.set_secret("BTC_PRIVATE_KEY", btc_key)
        vault.set_secret("BTC_ADDRESS", btc_address)
        print(f"[NEW] BTC wallet created")
        print(f"      Address: {btc_address}")

    # Step 4: Verify
    print("\n--- Vault Contents (names only) ---")
    for name in vault.list_secrets():
        print(f"  • {name}")

    print("\n--- Systemd env variable needed ---")
    print(f"  AGENT_VAULT_KEY={master_key}")
    print("  Add to: ~/.config/systemd/user/agent-life-space.service")
    print("  Then: systemctl --user daemon-reload && systemctl --user restart agent-life-space")

    # Step 5: Lock down vault directory
    os.chmod(str(vault_dir), 0o700)
    print(f"\n[OK] Vault directory locked: {vault_dir} (chmod 700)")


def _create_eth_wallet() -> tuple[str, str]:
    """Generate ETH wallet. Returns (address, private_key)."""
    try:
        from eth_account import Account
        account = Account.create()
        return (account.address, account.key.hex())
    except ImportError:
        # Fallback: generate raw key
        import secrets
        private_key = "0x" + secrets.token_hex(32)
        # Can't derive address without eth_account — store key, derive later
        return ("DERIVE_AFTER_INSTALL_ETH_ACCOUNT", private_key)


def _create_btc_wallet() -> tuple[str, str]:
    """Generate BTC wallet. Returns (address, private_key_wif)."""
    try:
        import hashlib
        import secrets

        # Generate private key (32 bytes)
        private_key_bytes = secrets.token_bytes(32)
        private_key_hex = private_key_bytes.hex()

        # Simple P2PKH address derivation (for receive-only)
        # Full BTC address generation requires more dependencies
        # For now, store the private key and derive address later
        return ("DERIVE_WITH_BTC_LIBRARY", private_key_hex)
    except Exception:
        import secrets
        return ("DERIVE_LATER", secrets.token_hex(32))


if __name__ == "__main__":
    main()
