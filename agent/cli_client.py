"""
Agent Life Space — Terminal Client

Connects to a running agent via its local API (port 8420).
No new agent instance — just a REPL that talks to the existing one.

Usage:
    python -m agent.cli_client
    # or via ~/bin/agent script
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def get_config() -> tuple[str, str, str]:
    """Read agent connection config from environment."""
    host = os.environ.get("AGENT_API_HOST", "127.0.0.1")
    port = os.environ.get("AGENT_API_PORT", "8420")
    api_key = os.environ.get("AGENT_API_KEY", "")
    return f"http://{host}:{port}", api_key, ""


def send_message(base_url: str, api_key: str, text: str) -> str:
    """Send a message to the running agent and return the response."""
    url = f"{base_url}/api/message"
    payload = json.dumps({"text": text, "sender": "terminal"}).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode())
            return body.get("response", body.get("text", str(body)))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        return f"Error {e.code}: {error_body[:200]}"
    except urllib.error.URLError as e:
        return f"Connection failed: {e.reason}\nIs the agent running? (python -m agent)"


def check_status(base_url: str) -> bool:
    """Check if agent is reachable."""
    try:
        req = urllib.request.Request(f"{base_url}/api/status", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def strip_markdown(text: str) -> str:
    return text.replace("*", "").replace("`", "").replace("_", "")


def main() -> None:
    base_url, api_key, _ = get_config()

    # Check connection
    if not check_status(base_url):
        print(f"\n  Cannot connect to agent at {base_url}")
        print("  Is the agent running? Start it with: python -m agent\n")
        sys.exit(1)

    # Get agent name from status
    try:
        req = urllib.request.Request(f"{base_url}/api/status", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = json.loads(resp.read().decode())
        agent_name = status.get("agent", "agent").lower().replace(" ", "")
    except Exception:
        agent_name = "agent"

    print(f"\n  Connected to {base_url}")
    print("  Type a message or /command. 'exit' to quit.\n")

    while True:
        try:
            text = input(f"  {agent_name}> ")
        except (EOFError, KeyboardInterrupt):
            print("\n  Disconnected.")
            break

        text = text.strip()
        if not text:
            continue
        if text.lower() in ("exit", "quit", "q"):
            print("  Disconnected.")
            break

        print("  ...")
        response = send_message(base_url, api_key, text)
        cleaned = strip_markdown(response)
        print(f"\n  {cleaned}\n")


if __name__ == "__main__":
    main()
