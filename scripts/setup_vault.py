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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.vault.secrets import SecretsManager

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
        try:
            eth_address, eth_key = _create_eth_wallet()
            vault.set_secret("ETH_PRIVATE_KEY", eth_key)
            vault.set_secret("ETH_ADDRESS", eth_address)
            print("[NEW] ETH wallet created")
            print(f"      Address: {eth_address}")
        except ImportError:
            print("[SKIP] ETH wallet — install eth_account: pip install eth-account")

    if "BTC_PRIVATE_KEY" in existing:
        print("[OK] BTC wallet already exists in vault")
        print(f"     Address: {vault.get_secret('BTC_ADDRESS')}")
    else:
        try:
            btc_address, btc_key = _create_btc_wallet()
            vault.set_secret("BTC_PRIVATE_KEY", btc_key)
            vault.set_secret("BTC_ADDRESS", btc_address)
            print("[NEW] BTC wallet created")
            print(f"      Address: {btc_address}")
        except ImportError:
            print("[SKIP] BTC wallet — install bit: pip install bit")

    # Step 4: Store additional tokens in vault (if provided via env)
    _store_if_env(vault, "GITHUB_TOKEN")
    _store_if_env(vault, "TELEGRAM_BOT_TOKEN")

    # Step 5: Verify
    print("\n--- Vault Contents (names only) ---")
    for name in vault.list_secrets():
        print(f"  • {name}")

    print("\n--- Systemd env variable needed ---")
    print(f"  AGENT_VAULT_KEY={master_key}")
    print("  (plus TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, CLAUDE_CODE_OAUTH_TOKEN)")
    print("  Add to: ~/.config/systemd/user/agent-life-space.service")
    print("  Then: systemctl --user daemon-reload && systemctl --user restart agent-life-space")

    # Step 6: Lock down vault directory
    os.chmod(str(vault_dir), 0o700)
    print(f"\n[OK] Vault directory locked: {vault_dir} (chmod 700)")


def _store_if_env(vault: SecretsManager, env_name: str) -> None:
    """If env var is set, store it in vault (safer than raw env)."""
    value = os.environ.get(env_name, "")
    if value and env_name not in vault.list_secrets():
        vault.set_secret(env_name, value)
        print(f"[NEW] {env_name} stored in vault")
    elif env_name in vault.list_secrets():
        print(f"[OK] {env_name} already in vault")


def _create_eth_wallet() -> tuple[str, str]:
    """Generate ETH wallet. Returns (address, private_key_hex)."""
    from eth_account import Account
    account = Account.create()
    return (account.address, account.key.hex())


def _create_btc_wallet() -> tuple[str, str]:
    """Generate BTC wallet. Returns (address, private_key_wif)."""
    from bit import Key
    key = Key()
    return (key.address, key.to_wif())


if __name__ == "__main__":
    main()
