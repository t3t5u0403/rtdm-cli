"""``rtdm usage`` — show this month's usage and remaining quota.

Hits GET /v1/usage and renders ``{used, quota, resets_at}`` as a
plain-text bar.  No colour, no rich tables — keeps the output usable
inside scripts and over SSH on dumb terminals.

Local mode has no quota concept; we say so explicitly rather than
silently succeeding on stale data.
"""

from __future__ import annotations

import sys

import httpx

from rtdm import config as config_module


_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
_BAR_WIDTH = 30


def run() -> int:
    try:
        cfg = config_module.load_config()
    except ValueError as exc:
        print(f"rtdm: {exc}", file=sys.stderr)
        return 1

    if cfg.mode != "remote":
        print(
            "rtdm: 'usage' is only available in remote mode.\n"
            "Local mode has no quota — every query is free.",
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

    url = cfg.remote.endpoint.rstrip("/") + "/v1/usage"
    headers = {"Authorization": f"Bearer {cfg.remote.api_key}"}

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, headers=headers)
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
    if resp.status_code == 429:
        print("rtdm: too many requests. Try again in a few seconds.", file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(
            f"rtdm: unexpected response from rtdm.sh (HTTP {resp.status_code}).",
            file=sys.stderr,
        )
        return 1

    try:
        body = resp.json()
        used = int(body["used"])
        quota = int(body["quota"])
        resets_at = str(body["resets_at"])
    except (ValueError, KeyError, TypeError):
        print("rtdm: unexpected response shape from rtdm.sh.", file=sys.stderr)
        return 1

    print(_render(used, quota, resets_at))
    return 0


def _render(used: int, quota: int, resets_at: str) -> str:
    """Render a single multi-line block describing usage.

    Bar uses ``=`` for filled and ``-`` for empty so it survives any
    terminal that has trouble with Unicode block characters.  When the
    quota is zero (e.g. an unconfigured key, defensive) the bar is
    hidden to avoid a divide-by-zero.
    """
    if quota <= 0:
        return f"used: {used}\n(no quota configured for this key)"

    ratio = min(used / quota, 1.0)
    filled = int(round(ratio * _BAR_WIDTH))
    bar = "[" + "=" * filled + "-" * (_BAR_WIDTH - filled) + "]"
    pct = int(round(ratio * 100))
    remaining = max(quota - used, 0)
    return (
        f"{bar}  {used}/{quota} ({pct}%)\n"
        f"{remaining} request{'s' if remaining != 1 else ''} remaining; resets {resets_at}"
    )
