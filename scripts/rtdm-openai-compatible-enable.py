#!/usr/bin/env python3
"""Enable an OpenAI-compatible local backend in rtdm without a fragile diff.

Run from the rtdm-cli repository root:
    python scripts/rtdm-openai-compatible-enable.py

The script changes only src/rtdm/backends/local.py and creates a .bak copy.
Configuration is supplied through environment variables:
    RTDM_OPENAI_BASE_URL  e.g. http://127.0.0.1:1234/v1
    RTDM_OPENAI_MODEL     optional; defaults to [local].model in config.toml
    RTDM_LOCAL_API_KEY    optional Bearer token
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "src" / "rtdm" / "backends" / "local.py"
FUNCTION_MARKER = "# BEGIN rtdm OpenAI-compatible backend"
DISPATCH_MARKER = "RTDM_OPENAI_BASE_URL"

OPENAI_FUNCTION = r'''# BEGIN rtdm OpenAI-compatible backend
def _chat_openai_compatible(
    system_prompt: str, user_input: str, cfg: LocalConfig, live: bool = True
) -> str:
    """Call an OpenAI-compatible /v1/chat/completions endpoint using SSE."""
    base_url = os.environ.get("RTDM_OPENAI_BASE_URL", "").strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise LocalBackendError(
            "Invalid RTDM_OPENAI_BASE_URL; expected an http(s) URL such as "
            "http://127.0.0.1:1234/v1."
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LocalBackendError(
            "RTDM_OPENAI_BASE_URL must not contain credentials, query, or fragment."
        )
    host = parsed.hostname
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise LocalBackendError(f"Invalid RTDM_OPENAI_BASE_URL: {exc}") from exc

    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    request_path = f"{path}/chat/completions"

    api_key = os.environ.get("RTDM_LOCAL_API_KEY", "").strip()
    if api_key and parsed.scheme != "https" and host.lower() not in {
        "localhost", "127.0.0.1", "::1"
    }:
        raise LocalBackendError(
            "Refusing to send RTDM_LOCAL_API_KEY over plain HTTP to a non-local host; use HTTPS."
        )

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = json.dumps(
        {
            "model": os.environ.get("RTDM_OPENAI_MODEL") or cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            "stream": True,
        }
    ).encode("utf-8")

    conn: http.client.HTTPConnection | None = None
    try:
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=60)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=60)
        conn.request("POST", request_path, body=body, headers=headers)
        resp = conn.getresponse()
        if resp.status != 200:
            error_body = resp.read()
            try:
                payload = json.loads(error_body)
                error = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(error, dict):
                    error = error.get("message")
                detail = str(error) if error else error_body.decode("utf-8", "replace")
            except (ValueError, UnicodeDecodeError):
                detail = error_body.decode("utf-8", "replace")
            raise LocalBackendError(
                f"OpenAI-compatible API error (HTTP {resp.status}): "
                f"{strip_control_chars(detail[:1000])}"
            )

        chunks: list[str] = []
        while True:
            line = resp.readline()
            if not line:
                break
            line = line.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("error"):
                error = event["error"]
                if isinstance(error, dict):
                    error = error.get("message", "unknown streaming error")
                raise LocalBackendError(
                    "OpenAI-compatible streaming error: "
                    + strip_control_chars(str(error))
                )
            choices = event.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content") or ""
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if isinstance(content, str) and content:
                content = strip_control_chars(content)
                if live:
                    print(content, end="", flush=True)
                chunks.append(content)
        if live:
            print()
        return strip_control_chars("".join(chunks))
    except (OSError, ConnectionError) as exc:
        raise LocalBackendError(
            "Could not connect to the configured OpenAI-compatible server. "
            "Check RTDM_OPENAI_BASE_URL and that the server is running."
        ) from exc
    finally:
        if conn is not None:
            conn.close()
# END rtdm OpenAI-compatible backend
'''


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for match in re.finditer("\n", text):
        offsets.append(match.end())
    return offsets


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise RuntimeError(f"Could not find top-level function {name}() in {BACKEND}")


def _add_os_import(source: str, tree: ast.Module) -> str:
    if re.search(r"^import\s+os(?:\s|$)", source, re.MULTILINE):
        return source
    # Place it alongside the stdlib imports, after import json when possible.
    match = re.search(r"^import\s+json\s*$", source, re.MULTILINE)
    if match:
        return source[: match.end()] + "\nimport os" + source[match.end() :]
    import_nodes = [
        n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    if not import_nodes:
        raise RuntimeError("Could not find an import section in local.py")
    offsets = _line_offsets(source)
    insert_at = offsets[max(n.end_lineno for n in import_nodes)]
    return source[:insert_at] + "import os\n" + source[insert_at:]


def transform(source: str) -> str:
    if FUNCTION_MARKER in source or DISPATCH_MARKER in source:
        raise RuntimeError(
            "An OpenAI-compatible backend marker already exists in local.py. "
            "No changes made; inspect the file before re-running."
        )

    tree = ast.parse(source)
    chat_node = _find_function(tree, "_chat")
    source = _add_os_import(source, tree)

    # Insert the provider function immediately before the existing Ollama _chat.
    tree = ast.parse(source)
    chat_node = _find_function(tree, "_chat")
    offsets = _line_offsets(source)
    chat_start = offsets[chat_node.lineno - 1]
    source = source[:chat_start] + OPENAI_FUNCTION + "\n\n" + source[chat_start:]

    # Dispatch to OpenAI-compatible mode only when explicitly enabled by env var.
    tree = ast.parse(source)
    chat_node = _find_function(tree, "_chat")
    body = chat_node.body
    if not body:
        raise RuntimeError("_chat() has no body; cannot insert provider dispatch")
    if (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        insert_line = body[0].end_lineno + 1
    else:
        insert_line = body[0].lineno
    offsets = _line_offsets(source)
    insert_at = offsets[insert_line - 1]
    dispatch = (
        '    openai_base_url = os.environ.get("RTDM_OPENAI_BASE_URL")\n'
        "    if openai_base_url:\n"
        "        return _chat_openai_compatible(system_prompt, user_input, cfg, live=live)\n\n"
    )
    source = source[:insert_at] + dispatch + source[insert_at:]
    ast.parse(source)  # fail before writing if the transformed module is invalid
    return source


def main() -> int:
    if not BACKEND.is_file():
        print(
            f"ERROR: {BACKEND.relative_to(ROOT)} not found. "
            "Run this script from an rtdm-cli checkout.",
            file=sys.stderr,
        )
        return 2
    original = BACKEND.read_text(encoding="utf-8")
    try:
        updated = transform(original)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("No files were changed.", file=sys.stderr)
        return 1
    backup = BACKEND.with_suffix(BACKEND.suffix + ".pre-openai.bak")
    if backup.exists():
        print(f"ERROR: refusing to overwrite existing backup: {backup}", file=sys.stderr)
        return 1
    backup.write_text(original, encoding="utf-8")
    BACKEND.write_text(updated, encoding="utf-8")
    print(f"Updated: {BACKEND.relative_to(ROOT)}")
    print(f"Backup:  {backup.relative_to(ROOT)}")
    print("OpenAI-compatible mode is enabled when RTDM_OPENAI_BASE_URL is set.")
    print("Run: python -m compileall -q src/rtdm/backends/local.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
