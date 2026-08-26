"""Tests for shoutout_gen/drive.py and its CLI hook-up.

No network anywhere: the Drive service is a stub. The contract under test is
"invisible unless .env opts in" -- the generator never touches Google unless a
folder is configured, and when it is, exactly one convert-to-Slides upload goes
into that folder.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_shoutouts as cli  # noqa: E402
import upload_to_slides  # noqa: E402
from shoutout_gen import drive  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_FORM = ROOT / "samples" / "example_form.csv"
FOLDER_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz_0123456"
FOLDER_LINK = f"https://drive.google.com/drive/folders/{FOLDER_ID}?usp=sharing"
DRIVE_KEYS = (
    drive.KEY_FOLDER, drive.KEY_SWITCH, drive.KEY_CLIENT_ID, drive.KEY_CLIENT_SECRET,
    drive.KEY_CLIENT_FILE, drive.KEY_TOKEN_FILE,
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """The developer's own shell must not leak DRIVE_* settings into the CLI tests."""
    for key in DRIVE_KEYS:
        monkeypatch.delenv(key, raising=False)


def _env(tmp_path: Path, text: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(text, encoding="utf-8")
    return env


# --------------------------------------------------------------------------- #
# .env parsing
# --------------------------------------------------------------------------- #
def test_missing_env_file_is_empty(tmp_path):
    assert drive.read_env_file(tmp_path / "nope.env") == {}


def test_env_file_syntax(tmp_path):
    env = _env(tmp_path, "\n".join([
        "# comment",
        "",
        "DRIVE_FOLDER=" + FOLDER_LINK,
        "export DRIVE_UPLOAD = on ",
        'DRIVE_CLIENT_ID="quoted # not a comment"',
        "DRIVE_CLIENT_SECRET='single'",
        "DRIVE_TOKEN_FILE=tok.json # trailing comment",
    ]))
    assert drive.read_env_file(env) == {
        "DRIVE_FOLDER": FOLDER_LINK,
        "DRIVE_UPLOAD": "on",
        "DRIVE_CLIENT_ID": "quoted # not a comment",
        "DRIVE_CLIENT_SECRET": "single",
        "DRIVE_TOKEN_FILE": "tok.json",
    }


def test_env_file_with_bom_and_crlf(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"\xef\xbb\xbfDRIVE_FOLDER=" + FOLDER_ID.encode() + b"\r\n")
    assert drive.read_env_file(env) == {"DRIVE_FOLDER": FOLDER_ID}


def test_malformed_line_is_an_error_not_silently_skipped(tmp_path):
    env = _env(tmp_path, "DRIVE_FOLDER=x\nthis is not a setting\n")
    with pytest.raises(drive.DriveConfigError, match=r"\.env:2"):
        drive.read_env_file(env)


@pytest.mark.parametrize("text", [
    FOLDER_LINK,
    f"https://drive.google.com/drive/u/0/folders/{FOLDER_ID}",
    f"https://drive.google.com/drive/folders/{FOLDER_ID}",
    FOLDER_ID,
    f"  {FOLDER_ID}\n",
])
def test_extract_folder_id(text):
    assert drive.extract_folder_id(text) == FOLDER_ID


@pytest.mark.parametrize("bad", ["", "not a link", "https://drive.google.com/file/d/abcdefghijk/view", "short"])
def test_extract_folder_id_rejects_non_folders(bad):
    with pytest.raises(drive.DriveConfigError):
        drive.extract_folder_id(bad)


# --------------------------------------------------------------------------- #
# load_config: off unless opted in
# --------------------------------------------------------------------------- #
def test_off_without_env_file(tmp_path):
    assert drive.load_config(tmp_path / ".env", environ={}) is None


def test_off_without_folder(tmp_path):
    env = _env(tmp_path, "DRIVE_CLIENT_ID=abc\nDRIVE_FOLDER=\n")
    assert drive.load_config(env, environ={}) is None


@pytest.mark.parametrize("value", ["off", "OFF", "false", "0", "no"])
def test_kill_switch(tmp_path, value):
    env = _env(tmp_path, f"DRIVE_FOLDER={FOLDER_LINK}\nDRIVE_UPLOAD={value}\n")
    assert drive.load_config(env, environ={}) is None


def test_enabled_with_defaults(tmp_path):
    env = _env(tmp_path, f"DRIVE_FOLDER={FOLDER_LINK}\n")
    assert drive.load_config(env, environ={}) == drive.DriveConfig(
        folder_id=FOLDER_ID,
        token_file=tmp_path.resolve() / ".google" / "token.json",
        client_file=tmp_path.resolve() / ".google" / "credentials.json",
    )


def test_paths_resolve_against_env_file_unless_absolute(tmp_path):
    absolute = (tmp_path / "elsewhere" / "creds.json").resolve()
    env = _env(tmp_path, f"DRIVE_FOLDER={FOLDER_ID}\nDRIVE_TOKEN_FILE=cache/tok.json\nDRIVE_CLIENT_FILE={absolute}\n")
    cfg = drive.load_config(env, environ={})
    assert cfg.token_file == tmp_path.resolve() / "cache" / "tok.json"
    assert cfg.client_file == absolute


def test_inline_client_is_read(tmp_path):
    env = _env(tmp_path, f"DRIVE_FOLDER={FOLDER_ID}\nDRIVE_CLIENT_ID=id.apps\nDRIVE_CLIENT_SECRET=s3cret\n")
    cfg = drive.load_config(env, environ={})
    assert (cfg.client_id, cfg.client_secret) == ("id.apps", "s3cret")


def test_environment_overrides_file(tmp_path):
    env = _env(tmp_path, f"DRIVE_FOLDER={FOLDER_ID}\n")
    assert drive.load_config(env, environ={"DRIVE_UPLOAD": "off"}) is None
    other = "0123456789abcdef_other"
    assert drive.load_config(env, environ={"DRIVE_FOLDER": other}).folder_id == other
    assert drive.load_config(tmp_path / "absent.env", environ={"DRIVE_FOLDER": other}).folder_id == other


def test_bad_folder_link_is_a_config_error(tmp_path):
    env = _env(tmp_path, "DRIVE_FOLDER=https://example.com/not-drive\n")
    with pytest.raises(drive.DriveConfigError, match="DRIVE_FOLDER"):
        drive.load_config(env, environ={})


# --------------------------------------------------------------------------- #
# The upload request (stub service, no network)
# --------------------------------------------------------------------------- #
class _StubDrive:
    """Mimics ``service.files().create(...).execute()`` and records the request."""

    def __init__(self):
        self.requests = []

    def files(self):
        return self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self

    def execute(self):
        name = self.requests[-1]["body"]["name"]
        return {"id": "file123", "name": name, "webViewLink": "https://docs.google.com/presentation/d/file123/edit"}


def _cfg(tmp_path):
    return drive.DriveConfig(folder_id=FOLDER_ID, token_file=tmp_path / "t.json", client_file=tmp_path / "c.json")


def test_upload_converts_into_the_folder(tmp_path):
    pytest.importorskip("googleapiclient")
    deck = tmp_path / "GM #25 9.10---shoutouts.pptx"
    deck.write_bytes(b"not really a deck")
    stub = _StubDrive()

    result = drive.upload_deck(deck, _cfg(tmp_path), service=stub)

    (request,) = stub.requests
    assert request["body"] == {
        "name": "GM #25 9.10---shoutouts", "parents": [FOLDER_ID], "mimeType": drive.GOOGLE_SLIDES_MIME,
    }
    assert request["media_body"].mimetype() == drive.PPTX_MIME
    assert request["supportsAllDrives"] is True
    assert "webViewLink" in request["fields"]
    assert result == drive.UploadResult(
        "file123", "GM #25 9.10---shoutouts", "https://docs.google.com/presentation/d/file123/edit"
    )


def test_upload_missing_deck_fails_before_touching_drive(tmp_path):
    stub = _StubDrive()
    with pytest.raises(FileNotFoundError):
        drive.upload_deck(tmp_path / "missing.pptx", _cfg(tmp_path), service=stub)
    assert stub.requests == []


def test_client_config_shape():
    conf = drive.client_config("id", "secret")["installed"]
    assert conf["client_id"] == "id" and conf["client_secret"] == "secret"
    assert conf["redirect_uris"] == ["http://localhost"]


# --------------------------------------------------------------------------- #
# CLI gating
# --------------------------------------------------------------------------- #
@pytest.fixture
def upload_spy(monkeypatch):
    """Replace the real upload with a recorder; the tests assert on what reached it."""
    calls = []

    def fake_upload(deck_path, cfg, service=None):
        calls.append((Path(deck_path), cfg))
        return drive.UploadResult("f1", Path(deck_path).stem, "https://docs.google.com/presentation/d/f1/edit")

    monkeypatch.setattr(drive, "upload_deck", fake_upload)
    return calls


def _generate(tmp_path, env_file, *extra):
    return cli._main([
        str(EXAMPLE_FORM), "--gm", "1", "--date", "9.17", "--out", str(tmp_path / "out"),
        "--env-file", str(env_file), *extra,
    ])


def test_cli_does_not_upload_without_env(tmp_path, upload_spy, capsys):
    assert _generate(tmp_path, tmp_path / "absent.env") == 0
    assert upload_spy == []
    assert "Drive" not in capsys.readouterr().out


def test_cli_uploads_when_env_enables_it(tmp_path, upload_spy, capsys):
    env = _env(tmp_path, f"DRIVE_FOLDER={FOLDER_LINK}\n")
    assert _generate(tmp_path, env) == 0
    ((deck, cfg),) = upload_spy
    assert deck == tmp_path / "out" / "GM #1 9.17---shoutouts.pptx" and deck.exists()
    assert cfg.folder_id == FOLDER_ID
    assert "https://docs.google.com/presentation/d/f1/edit" in capsys.readouterr().out


def test_cli_no_upload_flag_wins_over_env(tmp_path, upload_spy):
    env = _env(tmp_path, f"DRIVE_FOLDER={FOLDER_LINK}\n")
    assert _generate(tmp_path, env, "--no-upload") == 0
    assert upload_spy == []


def test_cli_kill_switch_in_env(tmp_path, upload_spy):
    env = _env(tmp_path, f"DRIVE_FOLDER={FOLDER_LINK}\nDRIVE_UPLOAD=off\n")
    assert _generate(tmp_path, env) == 0
    assert upload_spy == []


def test_cli_bad_env_fails_before_generating(tmp_path, upload_spy, capsys):
    env = _env(tmp_path, "DRIVE_FOLDER=https://example.com/nope\n")
    assert _generate(tmp_path, env) == 2
    assert not (tmp_path / "out").exists()
    assert upload_spy == []
    assert "DRIVE_FOLDER" in capsys.readouterr().err


def test_cli_upload_failure_keeps_deck_and_reports(tmp_path, monkeypatch, capsys):
    def boom(deck_path, cfg, service=None):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(drive, "upload_deck", boom)
    env = _env(tmp_path, f"DRIVE_FOLDER={FOLDER_LINK}\n")
    assert _generate(tmp_path, env) == 3
    assert (tmp_path / "out" / "GM #1 9.17---shoutouts.pptx").exists()
    err = capsys.readouterr().err
    assert "quota exceeded" in err and "upload_to_slides.py" in err


# --------------------------------------------------------------------------- #
# upload_to_slides.py (retry path)
# --------------------------------------------------------------------------- #
def test_retry_script_requires_enabled_config(tmp_path, upload_spy, capsys):
    deck = tmp_path / "d.pptx"
    deck.write_bytes(b"x")
    assert upload_to_slides._main([str(deck), "--env-file", str(tmp_path / "absent.env")]) == 2
    assert upload_spy == []
    assert "DRIVE_FOLDER" in capsys.readouterr().err


def test_retry_script_uploads_existing_deck(tmp_path, upload_spy, capsys):
    deck = tmp_path / "GM #3 10.1---shoutouts.pptx"
    deck.write_bytes(b"x")
    env = _env(tmp_path, f"DRIVE_FOLDER={FOLDER_ID}\n")
    assert upload_to_slides._main([str(deck), "--env-file", str(env)]) == 0
    assert [d for d, _ in upload_spy] == [deck]
    assert "docs.google.com" in capsys.readouterr().out
