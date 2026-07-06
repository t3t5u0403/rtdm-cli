"""Implementation of the ``rtdm config <init|show|path>`` subcommand.

Lives in its own module so the main dispatch path doesn't pay the cost
of ``getpass`` and friends on every shell-command lookup.  Only loaded
when the user actually runs ``rtdm config ...``.

Design choices worth calling out:

* ``init`` writes TOML by hand rather than via the ``tomli-w`` package
  so we keep zero extra dependencies.  The file is small and we
  control exactly what goes in it, so the lossless-roundtrip concerns
  that motivate tomli-w don't apply.
* The API key is masked with ``getpass`` so it doesn't leak into
  scrollback.  Echoed as ``****`` only conceptually — getpass
  shows nothing.
* If the file already exists, we ask before clobbering it.  This is
  the only place in the CLI where we destructively touch user state.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from rtdm.config import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_REMOTE_ENDPOINT,
    default_config_path,
    is_valid_api_key,
    is_valid_endpoint,
    load_config,
)

_MAX_KEY_ATTEMPTS = 3
_KEY_FORMAT_ERROR = (
    "API key looks invalid. Expected format: "
    "rtdm_live_<32 alphanumeric/dash/underscore chars>. Try again."
)
_MAX_ENDPOINT_ATTEMPTS = 3
_ENDPOINT_FORMAT_ERROR = (
    "Endpoint looks invalid. Must be https://, or http:// with a "
    "loopback host (localhost / 127.0.0.1 / ::1). Try again."
)


def run_path() -> int:
    """Print the absolute path the loader would read from."""
    print(default_config_path())
    return 0


def run_show() -> int:
    """Print the *effective* config (defaults + file + env override)."""
    try:
        cfg = load_config()
    except ValueError as exc:
        # `config show` is the "help me debug my config" tool — it must
        # print a clean pointer at the problem, never a traceback.
        print(f"rtdm: {exc}", file=sys.stderr)
        return 1
    if cfg.source is None:
        print("# (no config file; showing defaults)")
    else:
        print(f"# loaded from {cfg.source}")
    print(f"mode = {cfg.mode!r}")
    print()
    print("[remote]")
    if cfg.remote.api_key:
        masked = cfg.remote.api_key[:10] + "…"  # don't print live keys
        print(f"api_key = {masked!r}  # truncated; this is just for display")
    else:
        print("api_key = <unset>")
    print(f"endpoint = {cfg.remote.endpoint!r}")
    print()
    print("[local]")
    print(f"ollama_url = {cfg.local.ollama_url!r}")
    print(f"model = {cfg.local.model!r}")
    return 0


def _ask(prompt: str, default: str | None = None) -> str:
    """Ask the user for a line; return ``default`` on empty input."""
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    if not answer and default is not None:
        return default
    return answer


def _ask_choice(prompt: str, choices: list[str], default: str) -> str:
    """Ask for one of ``choices`` with ``default`` if the user just hits enter."""
    rendered = "/".join(c if c != default else c.upper() for c in choices)
    while True:
        raw = input(f"{prompt} [{rendered}]: ").strip().lower()
        if not raw:
            return default
        if raw in choices:
            return raw
        print(f"  please enter one of: {', '.join(choices)}")


def run_init() -> int:
    """Interactive setup wizard.

    Walks the user through picking a mode, supplying the bits that
    mode needs, and writes the result to ``~/.config/rtdm/config.toml``.
    Refuses to clobber an existing file without confirmation.
    """
    path = default_config_path()
    if path.exists():
        print(f"Config already exists at {path}")
        replace = _ask_choice("overwrite?", ["y", "n"], "n")
        if replace != "y":
            print("aborted; existing config left untouched.")
            return 0

    print("Welcome to rtdm. Pick a mode:")
    print("  local  — use your own Ollama (free, offline)")
    print("  remote — use the hosted service at rtdm.sh ($3/mo)")
    mode = _ask_choice("mode?", ["local", "remote"], "local")

    if mode == "remote":
        # getpass hides the input entirely; the prompt asks for it
        # explicitly so the user knows nothing's broken.  We also
        # validate the format and re-prompt so a fat-fingered paste
        # doesn't get persisted to disk in a shape the loader will
        # later refuse.
        api_key = ""
        for _ in range(_MAX_KEY_ATTEMPTS):
            candidate = getpass.getpass("API key (input hidden): ").strip()
            if not candidate:
                print(
                    "rtdm: API key cannot be empty for remote mode.",
                    file=sys.stderr,
                )
                return 1
            if is_valid_api_key(candidate):
                api_key = candidate
                break
            print(_KEY_FORMAT_ERROR, file=sys.stderr)
        else:
            print(
                "rtdm: too many invalid attempts; aborting.",
                file=sys.stderr,
            )
            return 1
        # Endpoint validation mirrors the api_key flow: we re-prompt
        # rather than persist a cleartext-http endpoint, which would
        # silently leak the bearer token.  The default is always safe.
        endpoint = ""
        for _ in range(_MAX_ENDPOINT_ATTEMPTS):
            candidate = _ask("endpoint", DEFAULT_REMOTE_ENDPOINT)
            if is_valid_endpoint(candidate):
                endpoint = candidate
                break
            print(_ENDPOINT_FORMAT_ERROR, file=sys.stderr)
        else:
            print(
                "rtdm: too many invalid endpoint attempts; aborting.",
                file=sys.stderr,
            )
            return 1
        ollama_url = DEFAULT_OLLAMA_URL
        model = DEFAULT_OLLAMA_MODEL
    else:
        api_key = ""
        endpoint = DEFAULT_REMOTE_ENDPOINT
        ollama_url = _ask("ollama URL", DEFAULT_OLLAMA_URL)
        model = _ask("ollama model", DEFAULT_OLLAMA_MODEL)

    _write_config(
        path,
        mode=mode,
        api_key=api_key,
        endpoint=endpoint,
        ollama_url=ollama_url,
        model=model,
    )
    print(f"Saved.  Config written to {path}")
    return 0


def _write_config(
    path: Path,
    *,
    mode: str,
    api_key: str,
    endpoint: str,
    ollama_url: str,
    model: str,
) -> None:
    """Write the config file atomically-ish, with parents created.

    We don't bother with a temp-file rename dance: this file is per-user,
    written interactively, and a Ctrl-C mid-write produces a partial
    file the user will obviously re-run ``rtdm config init`` to fix.
    Adding atomicity would imply we expect concurrent writers, which
    we don't.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _render_toml(
        mode=mode,
        api_key=api_key,
        endpoint=endpoint,
        ollama_url=ollama_url,
        model=model,
    )
    path.write_text(body, encoding="utf-8")
    # Tighten perms — the API key is a credential.
    try:
        path.chmod(0o600)
    except OSError:
        # Non-POSIX FS (e.g. some Windows mounts) — best-effort.
        pass


def _render_toml(
    *,
    mode: str,
    api_key: str,
    endpoint: str,
    ollama_url: str,
    model: str,
) -> str:
    """Render the config as a TOML document.

    Hand-rolled for zero deps; only escapes a quote character because
    that's the one TOML metacharacter the inputs we accept can contain.
    """
    def s(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [
        "# rtdm config — written by `rtdm config init`.",
        "# Edit by hand if you prefer; see `rtdm config show` for the effective values.",
        "",
        f"mode = {s(mode)}",
        "",
        "[remote]",
        f"api_key = {s(api_key)}",
        f"endpoint = {s(endpoint)}",
        "",
        "[local]",
        f"ollama_url = {s(ollama_url)}",
        f"model = {s(model)}",
        "",
    ]
    return "\n".join(lines)
