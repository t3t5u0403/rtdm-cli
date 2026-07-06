"""Tests for the local backend's Ollama HTTP error handling.

Pin: a non-200 response from Ollama (most commonly a model that isn't
pulled) must raise :class:`LocalBackendError` carrying the daemon's
error message — not silently stream nothing and exit 0.
"""

from __future__ import annotations

import json

import pytest

from rtdm.backends import local
from rtdm.backends.local import LocalBackendError, _error_detail
from rtdm.config import LocalConfig


class _FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def readline(self) -> bytes:  # pragma: no cover — must not be reached on non-200
        raise AssertionError("stream reader must not run on a non-200 response")


class _FakeConnection:
    """Stands in for http.client.HTTPConnection; returns a canned response."""

    response: _FakeResponse  # set by the test via monkeypatch closure

    def __init__(self, host, port):
        pass

    def request(self, method, path, body=None, headers=None):
        pass

    def getresponse(self):
        return self.response

    def close(self):
        pass


@pytest.fixture
def fake_ollama(monkeypatch):
    """Patch http.client.HTTPConnection inside the local backend."""

    def _install(status: int, body: bytes) -> None:
        _FakeConnection.response = _FakeResponse(status, body)
        monkeypatch.setattr(local.http.client, "HTTPConnection", _FakeConnection)

    return _install


def test_non_200_raises_with_ollama_error_text(fake_ollama):
    """404 with an ``{"error": ...}`` body surfaces the daemon's message."""
    fake_ollama(404, json.dumps({"error": "model 'qwen2.5-coder:7b' not found"}).encode())

    with pytest.raises(LocalBackendError) as excinfo:
        local.query("list files", LocalConfig())

    msg = str(excinfo.value)
    assert "Ollama error" in msg
    assert "model 'qwen2.5-coder:7b' not found" in msg


def test_non_200_with_non_json_body_falls_back_to_status(fake_ollama):
    """A non-JSON error body (proxy HTML, etc.) still produces a clear error."""
    fake_ollama(502, b"<html>Bad Gateway</html>")

    with pytest.raises(LocalBackendError) as excinfo:
        local.query("list files", LocalConfig())

    assert "HTTP 502" in str(excinfo.value)


def test_error_detail_prefers_error_field():
    assert _error_detail(404, b'{"error": "model not found"}') == "model not found"


def test_error_detail_handles_non_dict_json():
    assert _error_detail(500, b'["not", "a", "dict"]') == "HTTP 500"


def test_error_detail_strips_control_chars():
    body = json.dumps({"error": "bad\x1b[2Jmodel"}).encode()
    assert _error_detail(404, body) == "bad[2Jmodel"
