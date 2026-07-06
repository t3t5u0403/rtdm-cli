"""``rtdm rotate`` — issue a new API key, atomically update the config.

This is the highest-stakes subcommand in the CLI: a server-side
rotation revokes the old key, so if we then fail to persist the new
one the user is locked out.  Two safeguards:

1. Confirm before calling the server.  Once we've called, we *must*
   land the new key somewhere or the user is broken.
2. If the config write fails after a successful rotation, we print
   the new key prominently to stderr so the user can save it manually
   before the terminal scrolls it away.

Local mode has no key to rotate; reject with a helpful message.
"""

from __future__ import annotations

import sys

import httpx

from rtdm import config as config_module


_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


def run() -> int:
    try:
        cfg = config_module.load_config()
    except ValueError as exc:
        print(f"rtdm: {exc}", file=sys.stderr)
        return 1

    if cfg.mode != "remote":
        print(
            "rtdm: 'rotate' is only available in remote mode.\n"
            "There is no key to rotate in local mode.",
            file=sys.stderr,
        )
        return 1

    if not cfg.remote.api_key:
        print(
            "rtdm: no API key configured.\n"
            "Edit ~/.config/rtdm/config.toml or run 'rtdm config init'.",
            file=sys.stderr,
        )
        return 1

    print(
        "About to rotate your API key.\n"
        "Your current key will be REVOKED immediately and replaced.\n"
        "Continue? [y/N]: ",
        end="",
        flush=True,
    )
    answer = sys.stdin.readline().strip().lower()
    if answer not in ("y", "yes"):
        print("aborted; key unchanged.")
        return 0

    url = cfg.remote.endpoint.rstrip("/") + "/v1/auth/rotate-key"
    headers = {
        "Authorization": f"Bearer {cfg.remote.api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(url, headers=headers)
    except httpx.ConnectError:
        print("rtdm: couldn't reach rtdm.sh. Check your internet.", file=sys.stderr)
        return 1
    except httpx.TimeoutException:
        print("rtdm: rtdm.sh took too long to respond.", file=sys.stderr)
        return 1

    if resp.status_code == 401:
        print(
            "rtdm: invalid or revoked API key. Run 'rtdm config init' to update.",
            file=sys.stderr,
        )
        return 1
    if resp.status_code == 403:
        print(
            "rtdm: subscription is not active. Visit rtdm.sh to manage.",
            file=sys.stderr,
        )
        return 1
    if resp.status_code == 429:
        print("rtdm: too many requests. Try again in a few seconds.", file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(
            f"rtdm: unexpected response from rtdm.sh (HTTP {resp.status_code}). "
            "Key NOT rotated.",
            file=sys.stderr,
        )
        return 1

    try:
        new_key = resp.json()["api_key"]
    except (ValueError, KeyError, TypeError):
        # Server said 200 but the body is wrong; the old key may have
        # been revoked but we can't tell the user the new one.  This
        # is the worst-case path; surface it loudly.
        print(
            "rtdm: server returned 200 with an unexpected body.\n"
            "Your old key may already be revoked. Visit rtdm.sh and rotate again.",
            file=sys.stderr,
        )
        return 1
    if not isinstance(new_key, str) or not new_key.startswith("rtdm_live_"):
        print(
            "rtdm: server returned a key in an unexpected format.\n"
            "Visit rtdm.sh and rotate again.",
            file=sys.stderr,
        )
        return 1

    # Persist or shout — this is the lock-out window.
    try:
        path = config_module.update_api_key(new_key)
    except OSError as exc:
        print(
            "\n!! KEY ROTATION SUCCEEDED BUT CONFIG WRITE FAILED !!\n"
            f"Reason: {exc}\n"
            "Save this key NOW; the old one no longer works:\n\n"
            f"    {new_key}\n\n"
            "Then put it in ~/.config/rtdm/config.toml under [remote].api_key.",
            file=sys.stderr,
        )
        return 1

    print(f"New API key saved to {path}")
    return 0
