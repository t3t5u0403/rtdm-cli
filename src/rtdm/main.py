"""rtdm CLI dispatcher.

Reads config (file + env override), parses argv, picks a backend, and
runs the requested task.  Three task shapes — cmd (default), ``-q``
question, ``-e`` explain — map onto the three backend functions
exposed by both ``backends.local`` and ``backends.remote``.

The ``-c`` (clipboard) and ``-x`` (execute) flags live here rather
than in the backends because they're side effects on the *user's*
machine; whether the text came from local Ollama or a hosted call
makes no difference to how it should be copied or run.

Note on ``-x``: by spec, it's only valid in cmd mode.  Asking the
shell to run a paragraph of natural-language explanation is a foot-gun
we'd rather refuse outright than try to be clever about.
"""

from __future__ import annotations

import argparse
import sys

from rtdm import config as config_module
from rtdm.backends import local as local_backend
from rtdm.backends import remote as remote_backend
from rtdm.config import Config


def _build_config_parser() -> argparse.ArgumentParser:
    """Subparser used only when argv[0] == 'config'."""
    parser = argparse.ArgumentParser(
        prog="rtdm config",
        description="Interactive setup, inspect current config, or print its path.",
    )
    parser.add_argument(
        "action",
        choices=("init", "show", "path"),
        help="init: interactive setup; show: print effective config; path: print config file location",
    )
    return parser


def _build_parser() -> argparse.ArgumentParser:
    """Build the main argparse tree.

    Argparse subparsers don't compose with a free-form ``nargs="*"``
    positional (the first word always wins as a subcommand name), so
    we keep the cmd/-q/-e parser self-contained and route ``config``
    via :func:`_build_config_parser` from :func:`main` before parsing.
    """
    parser = argparse.ArgumentParser(
        prog="rtdm",
        description="An AI shell helper that gives you the command without the commentary.",
        usage=(
            "rtdm [-q | -e] [-c] [-x] <query...>"
            "   |   rtdm config <init|show|path>"
            "   |   rtdm <portal|rotate|usage|whoami>"
        ),
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "-q",
        action="store_true",
        help="ask a general terminal question instead of getting a command",
    )
    mode.add_argument(
        "-e",
        action="store_true",
        help="explain a shell command",
    )
    parser.add_argument(
        "-c",
        action="store_true",
        help="copy the output to clipboard",
    )
    parser.add_argument(
        "-x",
        action="store_true",
        help="execute the returned command (cmd mode only, with confirmation)",
    )
    parser.add_argument(
        "query",
        nargs="*",
        help="natural-language query; multi-word, no quotes needed",
    )
    return parser


def _resolve_task(args: argparse.Namespace) -> str:
    """Translate the ``-q``/``-e`` flags into a task tag."""
    if args.q:
        return "ask"
    if args.e:
        return "explain"
    return "query"


def _dispatch(task: str, user_input: str, cfg: Config) -> str:
    """Run ``task`` against the configured backend, returning the model output.

    Local mode raises :class:`local_backend.LocalBackendError`; remote
    mode raises :class:`remote_backend.RemoteBackendError`.  Both are
    caught at the CLI top level and printed as a single line to stderr.
    """
    if cfg.mode == "remote":
        if not cfg.remote.api_key:
            raise remote_backend.RemoteBackendError(
                "remote mode is enabled but api_key is missing.\n"
                "edit ~/.config/rtdm/config.toml or run 'rtdm config init' to set up."
            )
        fn = getattr(remote_backend, task)
        return fn(user_input, cfg.remote.api_key, cfg.remote.endpoint)

    fn = getattr(local_backend, task)
    return fn(user_input, cfg.local)


def _run_config_subcommand(action: str) -> int:
    """Hand off to the (lazily imported) config CLI module."""
    from rtdm import config_cli

    if action == "init":
        return config_cli.run_init()
    if action == "show":
        return config_cli.run_show()
    if action == "path":
        return config_cli.run_path()
    return 2  # unreachable thanks to argparse choices=, but keeps mypy happy


# Account-management subcommands.  Each maps to a module loaded lazily
# below so a normal "rtdm <query>" invocation never imports webbrowser,
# tomli-w shims, etc.  Keep this tuple in sync with the README's
# Subcommands table.
_ACCOUNT_SUBCOMMANDS = ("portal", "rotate", "usage", "whoami")


def _run_account_subcommand(name: str, rest: list[str]) -> int:
    """Dispatch one of the account subcommands.

    Each handler accepts no positional args (we accept ``rest`` only so
    we can reject extra junk with a clean message instead of argparse's
    "unrecognised arguments" splat).
    """
    if rest:
        print(f"rtdm: '{name}' takes no arguments", file=sys.stderr)
        return 1

    if name == "portal":
        from rtdm import portal_cli
        return portal_cli.run()
    if name == "rotate":
        from rtdm import rotate_cli
        return rotate_cli.run()
    if name == "usage":
        from rtdm import usage_cli
        return usage_cli.run()
    if name == "whoami":
        from rtdm import whoami_cli
        return whoami_cli.run()
    return 2  # unreachable; gated by the membership check in main()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — wired into pyproject.toml as the ``rtdm`` script."""
    raw = list(sys.argv[1:] if argv is None else argv)

    # Hand off the config subcommand before the main parser sees argv.
    # Otherwise an argparse subparser would steal "config" as a literal
    # query word ("rtdm config the system" would fail).
    if raw and raw[0] == "config":
        cfg_args = _build_config_parser().parse_args(raw[1:])
        return _run_config_subcommand(cfg_args.action)

    # Same dance for the account subcommands.  Same trade-off: a query
    # whose first word is "portal" or "usage" must be quoted ("rtdm
    # 'portal access on linux'") — but those collisions are rare enough
    # that the readability of `rtdm portal` wins.
    if raw and raw[0] in _ACCOUNT_SUBCOMMANDS:
        return _run_account_subcommand(raw[0], raw[1:])

    parser = _build_parser()
    args = parser.parse_args(raw)

    if not args.query:
        parser.print_help()
        return 1

    if args.x and (args.q or args.e):
        print(
            "rtdm: -x can only be used in command mode (not with -q or -e)",
            file=sys.stderr,
        )
        return 1

    try:
        cfg = config_module.load_config()
    except ValueError as exc:
        # Corrupted config file — the message already says how to fix it;
        # a traceback here would just bury it.
        print(f"rtdm: {exc}", file=sys.stderr)
        return 1
    task = _resolve_task(args)
    user_input = " ".join(args.query)

    try:
        output = _dispatch(task, user_input, cfg)
    except local_backend.LocalBackendError as exc:
        print(f"rtdm: {exc}", file=sys.stderr)
        return 1
    except remote_backend.RemoteBackendError as exc:
        print(f"rtdm: {exc}", file=sys.stderr)
        return 1

    # Remote backends return without having printed anything; surface
    # the response to the user.  Local backends already streamed the
    # text live, so we'd just be double-printing.
    if cfg.mode == "remote":
        print(output)

    if args.c:
        if local_backend.copy_to_clipboard(output):
            print("(copied to clipboard)", file=sys.stderr)

    if args.x:
        local_backend.confirm_and_execute(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
