"""``rtdm portal`` — open the Stripe billing portal in the browser.

Hits POST /v1/auth/portal with the configured API key, gets back a
short-lived portal URL, and tries to launch a browser at it.  We always
also print the URL so users on headless machines (SSH, WSL without
``wslview``, etc.) can copy-paste it.

This subcommand is meaningless in local mode; we surface a clear error
in that case rather than silently succeeding.
"""

from __future__ import annotations

import sys
import webbrowser
from urllib.parse import urlsplit

import httpx

from rtdm import config as config_module


_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)

# Allow-list of hostnames the portal endpoint is permitted to redirect us
# to.  A startswith("https://") check would happily open
# ``https://stripe.com.evil.example/`` if the server (or a downstream
# response-rewriting proxy) ever returned one.  Stripe-managed billing
# portals only ever live on billing.stripe.com, so pin to exactly that.
_ALLOWED_PORTAL_HOSTS = frozenset({"billing.stripe.com"})


def run() -> int:
    """Entry point invoked by the dispatcher."""
    try:
        cfg = config_module.load_config()
    except ValueError as exc:
        print(f"rtdm: {exc}", file=sys.stderr)
        return 1

    if cfg.mode != "remote":
        print(
            "rtdm: 'portal' is only available in remote mode.\n"
            "Run 'rtdm config init' and pick remote, or visit rtdm.sh.",
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

    url = cfg.remote.endpoint.rstrip("/") + "/v1/auth/portal"
    headers = {"Authorization": f"Bearer {cfg.remote.api_key}"}

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(url, headers=headers)
    except httpx.ConnectError:
        print(
            "rtdm: couldn't reach rtdm.sh. Check your internet.",
            file=sys.stderr,
        )
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
    if resp.status_code == 409:
        print(
            "rtdm: no Stripe customer attached to this account. Contact privacy@rtdm.sh.",
            file=sys.stderr,
        )
        return 1
    if resp.status_code != 200:
        print(
            f"rtdm: unexpected response from rtdm.sh (HTTP {resp.status_code}).",
            file=sys.stderr,
        )
        return 1

    try:
        portal_url = resp.json()["portal_url"]
    except (ValueError, KeyError, TypeError):
        print("rtdm: unexpected response shape from rtdm.sh.", file=sys.stderr)
        return 1

    if not isinstance(portal_url, str):
        print("rtdm: server returned a non-string portal URL; refusing to open.", file=sys.stderr)
        return 1

    # Validate the redirect target against an allow-list of trusted
    # hostnames.  Print the URL either way so the user can see what
    # the server tried to send them to (useful for debugging /
    # support), but refuse to launch the browser at anything off-list.
    try:
        parts = urlsplit(portal_url)
    except ValueError:
        parts = None

    host = parts.hostname.lower() if parts and parts.hostname else None
    if (
        parts is None
        or parts.scheme != "https"
        or host not in _ALLOWED_PORTAL_HOSTS
        or parts.username
        or parts.password
    ):
        print(portal_url)
        print(
            "rtdm: server returned an unexpected portal host; refusing to open. "
            "Visit the URL above only if you trust it, or contact privacy@rtdm.sh.",
            file=sys.stderr,
        )
        return 1

    # Print first so headless users always see the URL even if open()
    # silently no-ops.
    print(portal_url)
    try:
        webbrowser.open(portal_url, new=2)
    except Exception:  # noqa: BLE001 — best-effort; printed URL is the fallback
        pass
    return 0
