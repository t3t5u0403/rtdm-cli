"""Local Ollama backend.

This is the original rtfm streaming logic, now parameterised by URL +
model from config and split into three task-specific entry points so
the dispatcher can call them by name.

Design notes:

* We use ``http.client`` rather than httpx for the streaming path
  because the chat endpoint emits one JSON object per line and stdlib
  is already enough.  Pulling httpx in here would add an import for
  no UX win.
* Tokens are printed live to ``stdout`` and accumulated into a single
  string for the post-call clipboard / -x handlers.
* All three modes use the same ``/api/chat`` endpoint; only the
  system prompt differs.  Keeping the prompts in sync with the hosted
  service is *not* a goal — the local user is free to point at a
  different model that may need different framing.
"""

from __future__ import annotations

import http.client
import json
import sys
from urllib.parse import urlparse

from rtdm.backends import strip_control_chars
from rtdm.config import LocalConfig

SYSTEM_CMD = (
    "Reply with ONLY the shell command(s) that answer the question. "
    "No explanation, no preamble, no markdown, no code fences. "
    "Just the raw command(s), one per line."
)

SYSTEM_QNA = (
    "You are a helpful terminal assistant. "
    "Answer the user's question concisely and clearly. "
    "Use plain text. Do not use markdown or code fences."
)

SYSTEM_EXPLAIN = (
    "Explain the given shell command in plain English. "
    "Break down each part (flags, arguments, pipes, redirects, etc.) concisely. "
    "Use plain text. Do not use markdown or code fences."
)


class LocalBackendError(Exception):
    """Raised when the local Ollama daemon can't be reached or errors out."""


def _error_detail(status: int, body: bytes) -> str:
    """Extract a printable error message from a non-200 Ollama response.

    Ollama puts the human-readable reason in an ``{"error": "..."}``
    JSON body (e.g. ``model 'x' not found``).  Fall back to the bare
    HTTP status when the body isn't in that shape.  Scrubbed of control
    characters because it goes straight to the user's terminal.
    """
    try:
        err = json.loads(body).get("error")
    except (ValueError, AttributeError):
        err = None
    if isinstance(err, str) and err.strip():
        return strip_control_chars(err.strip())
    return f"HTTP {status}"


def _stream_response(resp: http.client.HTTPResponse, live: bool = True) -> str:
    """Accumulate tokens; when ``live`` is True, also print them as they arrive.

    Each token is scrubbed of C0/C1 control characters before being
    written to stdout or accumulated, so a compromised model cannot
    smuggle ANSI escape sequences into the user's terminal or the
    command that gets handed to ``confirm_and_execute``.

    ``live=False`` suppresses the live printing so the caller can render
    the finished text itself (e.g. styled output on a TTY); the full
    string is returned either way.
    """
    chunks: list[str] = []
    while True:
        line = resp.readline()
        if not line:
            break
        chunk = json.loads(line)
        if chunk.get("done"):
            break
        tok = chunk.get("message", {}).get("content", "")
        if tok:
            tok = strip_control_chars(tok)
            if live:
                print(tok, end="", flush=True)
            chunks.append(tok)
    if live:
        print()
    return strip_control_chars("".join(chunks))


def _chat(system_prompt: str, user_input: str, cfg: LocalConfig, live: bool = True) -> str:
    """POST to Ollama's /api/chat and stream the result.

    Raises :class:`LocalBackendError` if the daemon isn't running.
    Anything else (HTTP non-200, malformed JSON) bubbles up as the
    underlying exception, which the CLI top level prints with a
    friendly message.
    """
    parsed = urlparse(cfg.ollama_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 11434

    body = json.dumps(
        {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            "stream": True,
            "think": False,
            "options": {"num_ctx": 2048},
        }
    ).encode()

    try:
        if parsed.scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(host, port)
        else:
            conn = http.client.HTTPConnection(host, port)
        conn.request(
            "POST",
            "/api/chat",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        if resp.status != 200:
            # Ollama reports errors (e.g. model not pulled) as a non-200
            # JSON body, not a stream; without this check the stream
            # reader sees no tokens and we'd exit 0 with blank output.
            body = resp.read()
            conn.close()
            raise LocalBackendError(f"Ollama error: {_error_detail(resp.status, body)}")
        output = _stream_response(resp, live=live)
        conn.close()
        return output
    except (ConnectionRefusedError, OSError) as exc:
        raise LocalBackendError(
            "Could not connect to Ollama. Is it running? (`ollama serve`)"
        ) from exc


def query(prompt: str, cfg: LocalConfig, live: bool = True) -> str:
    """Generate a shell command for ``prompt`` (the cmd-mode default)."""
    return _chat(SYSTEM_CMD, prompt, cfg, live=live)


def ask(question: str, cfg: LocalConfig, live: bool = True) -> str:
    """Answer a free-form terminal question."""
    return _chat(SYSTEM_QNA, question, cfg, live=live)


def explain(command: str, cfg: LocalConfig, live: bool = True) -> str:
    """Explain a shell command."""
    return _chat(SYSTEM_EXPLAIN, command, cfg, live=live)


# ---------------------------------------------------------------------------
# Side-effect helpers (clipboard, execute) — used by both backends, but they
# live here because clipboard support has been a "local mode" feature since
# day one and remote mode just borrows them.  Keeping them in one place
# avoids a third tiny module just to share three functions.
# ---------------------------------------------------------------------------


def copy_to_clipboard(text: str) -> bool:
    """Copy ``text`` to the system clipboard.

    Tries Wayland (wl-copy) first, then X11 (xclip, xsel).  Returns
    True on success; prints to stderr and returns False if no
    clipboard tool is installed.  Subprocess errors propagate.
    """
    import shutil
    import subprocess

    # Try each available tool in preference order. A tool can be installed
    # yet fail at runtime (e.g. xclip with no X display on a Wayland box);
    # fall through to the next rather than raising, so -c never crashes the
    # CLI over a clipboard hiccup.
    candidates = (
        ("wl-copy", ["wl-copy"]),
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
    )
    found = False
    for tool, cmd in candidates:
        if not shutil.which(tool):
            continue
        found = True
        try:
            subprocess.run(
                cmd,
                input=text.encode(),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (subprocess.SubprocessError, OSError):
            continue
    if not found:
        print(
            "No clipboard tool found (install wl-clipboard, xclip, or xsel)",
            file=sys.stderr,
        )
    else:
        print(
            "Clipboard copy failed (no display?). On Wayland, install wl-clipboard.",
            file=sys.stderr,
        )
    return False


def confirm_and_execute(command: str) -> None:
    """Prompt y/N then run ``command`` via the shell on consent.

    Cancels cleanly on Ctrl-C / EOF.  Anything other than a literal
    ``y`` is treated as no.
    """
    import subprocess

    try:
        if sys.stdout.isatty():
            from rich.console import Console

            answer = Console().input("[bold yellow]Run?[/] [dim]\\[y/N][/] ")
        else:
            answer = input("Run? [y/N] ")
        answer = answer.strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return
    if answer == "y":
        subprocess.run(command, shell=True)
