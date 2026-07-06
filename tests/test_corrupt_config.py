"""A corrupted config file must produce a friendly error, not a traceback.

``config.load_config`` raises ValueError on a malformed api_key or
endpoint.  Every entry point — including the two "help me debug my
config" tools, ``rtdm whoami`` and ``rtdm config show`` — must catch
it, print the message to stderr, and exit 1.

Each test just calls the real entry point; if the ValueError escaped,
pytest would report the raised exception rather than an assertion
failure, so these double as no-traceback proofs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rtdm import main as rtdm_main

_CORRUPT_KEY = 'mode = "remote"\n\n[remote]\napi_key = "not-a-valid-key"\n'
_CORRUPT_ENDPOINT = (
    'mode = "remote"\n\n[remote]\n'
    'api_key = "rtdm_live_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
    'endpoint = "http://rtdm.sh"\n'
)


@pytest.fixture
def corrupt_config(tmp_path, monkeypatch):
    """Point default_config_path at a config with an invalid api_key."""
    p = tmp_path / "config.toml"
    p.write_text(_CORRUPT_KEY, encoding="utf-8")
    monkeypatch.setattr("rtdm.config.default_config_path", lambda: p)
    return p


def _assert_friendly(capsys, rc: int) -> None:
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("rtdm: ")
    assert "config init" in err  # points the user at the fix


@pytest.mark.parametrize(
    "argv",
    [
        ["whoami"],
        ["usage"],
        ["portal"],
        ["rotate"],
        ["config", "show"],
        ["find", "big", "files"],  # plain query dispatch
    ],
    ids=["whoami", "usage", "portal", "rotate", "config-show", "query"],
)
def test_corrupt_api_key_friendly_error(corrupt_config, capsys, argv):
    rc = rtdm_main.main(argv)
    _assert_friendly(capsys, rc)


def test_corrupt_endpoint_friendly_error(tmp_path, monkeypatch, capsys):
    """The endpoint validation path raises too; same friendly handling."""
    p = tmp_path / "config.toml"
    p.write_text(_CORRUPT_ENDPOINT, encoding="utf-8")
    monkeypatch.setattr("rtdm.config.default_config_path", lambda: p)

    rc = rtdm_main.main(["whoami"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("rtdm: ")
    assert "Invalid endpoint" in err


def test_valid_config_still_loads(tmp_path, monkeypatch, capsys):
    """Sanity: the try/except doesn't swallow the happy path."""
    p = tmp_path / "config.toml"
    p.write_text(
        'mode = "remote"\n\n[remote]\n'
        'api_key = "rtdm_live_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
        'endpoint = "https://rtdm.sh"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("rtdm.config.default_config_path", lambda: p)

    rc = rtdm_main.main(["whoami"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "remote" in out
