"""``rtdm whoami`` — print which key/host this CLI is configured against.

Pure-local introspection: NO network call.  Useful for the "wait, why
is this hitting the wrong endpoint?" debugging case before opening
support tickets.

The API key is shown as its first 16 characters only (the public
prefix portion of the format) followed by an ellipsis.  We never echo
the secret half.
"""

from __future__ import annotations

import sys

from rtdm import config as config_module


_PREFIX_LEN = 16  # matches the server-side key_prefix length


def run() -> int:
    try:
        cfg = config_module.load_config()
    except ValueError as exc:
        # whoami is a debugging tool; it must degrade to a clean message,
        # not a traceback, when the config itself is the problem.
        print(f"rtdm: {exc}", file=sys.stderr)
        return 1

    print(f"mode:     {cfg.mode}")
    if cfg.source is None:
        print("config:   <defaults — no file on disk>")
    else:
        print(f"config:   {cfg.source}")

    if cfg.mode == "remote":
        print(f"endpoint: {cfg.remote.endpoint}")
        print(f"api_key:  {_mask_key(cfg.remote.api_key)}")
    else:
        print(f"ollama:   {cfg.local.ollama_url}")
        print(f"model:    {cfg.local.model}")

    return 0


def _mask_key(key: str | None) -> str:
    """Return a display-safe form of the API key.

    Only the public prefix portion (``rtdm_live_<6 random chars>``) is
    printed; the rest is replaced with ``…``.  An unset key shows as a
    literal placeholder so users notice immediately.
    """
    if not key:
        return "<unset>"
    if len(key) <= _PREFIX_LEN:
        # Defensive: not in the canonical format.  Show as-is rather
        # than fake-masking something we can't guarantee the shape of.
        return key
    return key[:_PREFIX_LEN] + "…"
