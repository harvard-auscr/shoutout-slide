"""Optional upload of a generated deck to Google Drive as a Google Slides file.

The club's GM decks live in Google Slides, so the useful end state of a
generated PPTX is a Slides file sitting in a shared Drive folder, ready for
File -> Import slides. This module does exactly that one upload (Drive converts
the PPTX on the way in) and nothing else.

The feature is opt-in via a ``.env`` file: ``load_config`` returns ``None``
unless ``DRIVE_FOLDER`` is set and ``DRIVE_UPLOAD`` is not switched off, and the
Google client libraries are imported only when an upload actually runs, so the
generator needs neither credentials nor network access by default.

Auth choices (both deliberate):
  * A user OAuth flow, not a service account: service accounts have no storage
    quota on a personal My Drive, so their uploads into a shared folder fail.
    The one-time browser consent caches a refresh token next to the config.
  * The full ``drive`` scope, not ``drive.file``: the narrower scope only sees
    files the app itself created, so it cannot place an upload into a folder
    the user picked by pasting a link (the parent lookup 404s).
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# The .env contract
# --------------------------------------------------------------------------- #
KEY_FOLDER = "DRIVE_FOLDER"  # folder link or bare ID; the feature is off without it
KEY_SWITCH = "DRIVE_UPLOAD"  # off/false/0/no keeps the folder configured but skips uploads
KEY_CLIENT_ID = "DRIVE_CLIENT_ID"  # inline OAuth "Desktop app" client ...
KEY_CLIENT_SECRET = "DRIVE_CLIENT_SECRET"
KEY_CLIENT_FILE = "DRIVE_CLIENT_FILE"  # ... or the client JSON downloaded from the console
KEY_TOKEN_FILE = "DRIVE_TOKEN_FILE"  # cache for the signed-in token

DEFAULT_CLIENT_FILE = ".google/credentials.json"
DEFAULT_TOKEN_FILE = ".google/token.json"
_OFF_VALUES = {"0", "off", "false", "no"}

SCOPES = ["https://www.googleapis.com/auth/drive"]
GOOGLE_SLIDES_MIME = "application/vnd.google-apps.presentation"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

_ENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
# Drive folder URLs come as /drive/folders/<id> or /drive/u/<n>/folders/<id>, with or
# without ?usp=... -- the ID is the only part that matters.
_FOLDER_URL = re.compile(r"drive\.google\.com/.*?/folders/([A-Za-z0-9_-]+)")
_BARE_ID = re.compile(r"[A-Za-z0-9_-]{10,}")
_QUOTES = "\"'"


class DriveConfigError(ValueError):
    """The .env is present but unusable (syntax, bad folder link, no way to sign in)."""


@dataclass(frozen=True)
class DriveConfig:
    """Everything one upload needs. Paths are absolute (resolved against the .env)."""

    folder_id: str
    token_file: Path
    client_file: Path
    client_id: str = ""
    client_secret: str = ""


@dataclass(frozen=True)
class UploadResult:
    file_id: str
    name: str
    link: str


def read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines; blanks, ``#`` comments, quotes and ``export`` allowed.

    A missing file is simply ``{}`` (the feature is off). A malformed line is an
    error rather than silently skipped, so a typo cannot quietly disable the upload.
    """
    path = Path(path)
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if not match:
            raise DriveConfigError(f"{path}:{lineno}: expected KEY=VALUE, got {raw!r}")
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
            value = value[1:-1]  # quoted: verbatim, a '#' inside is not a comment
        else:
            value = value.split(" #", 1)[0].rstrip()  # unquoted: allow a trailing comment
        values[key] = value
    return values


def extract_folder_id(link_or_id: str) -> str:
    """Accept a pasted Drive folder URL or the bare folder ID."""
    text = link_or_id.strip()
    match = _FOLDER_URL.search(text)
    if match:
        return match.group(1)
    if _BARE_ID.fullmatch(text):
        return text
    raise DriveConfigError(f"{KEY_FOLDER} is not a Drive folder link or ID: {link_or_id!r}")


def load_config(env_file: Path, environ: Mapping[str, str] = os.environ) -> DriveConfig | None:
    """Return the upload settings, or ``None`` when the feature is off.

    Off means: no .env, no ``DRIVE_FOLDER``, or ``DRIVE_UPLOAD`` set to off/false/0/no.
    Real environment variables override the file so CI or a one-off shell can steer
    it without editing .env. Relative paths resolve against the .env's folder, so
    the result does not depend on the working directory.
    """
    env_file = Path(env_file)
    values = read_env_file(env_file)

    def get(key: str) -> str:
        return (environ.get(key) or values.get(key, "")).strip()

    if get(KEY_SWITCH).lower() in _OFF_VALUES:
        return None
    folder = get(KEY_FOLDER)
    if not folder:
        return None
    base = env_file.resolve().parent
    return DriveConfig(
        folder_id=extract_folder_id(folder),
        token_file=base / (get(KEY_TOKEN_FILE) or DEFAULT_TOKEN_FILE),
        client_file=base / (get(KEY_CLIENT_FILE) or DEFAULT_CLIENT_FILE),
        client_id=get(KEY_CLIENT_ID),
        client_secret=get(KEY_CLIENT_SECRET),
    )


# --------------------------------------------------------------------------- #
# Sign-in and upload (Google libraries imported lazily, see module doc)
# --------------------------------------------------------------------------- #
def client_config(client_id: str, client_secret: str) -> dict:
    """The "installed app" client description the OAuth flow expects, from an inline ID + secret."""
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def upload_metadata(deck_path: Path, folder_id: str) -> dict:
    """Drive file resource for the deck: same name as the file, converted to Slides on upload."""
    return {"name": Path(deck_path).stem, "parents": [folder_id], "mimeType": GOOGLE_SLIDES_MIME}


def _google_libs():
    """Import the Google client libraries, with an actionable error if they are missing."""
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise DriveConfigError(
            "Drive upload is configured but the Google client libraries are not installed; "
            "run: pip install -r requirements.txt"
        ) from exc
    return build, MediaFileUpload


def _sign_in(cfg: DriveConfig):
    """Cached token -> silent refresh -> one-time browser consent. Returns Google credentials."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if cfg.token_file.exists():
        creds = Credentials.from_authorized_user_file(str(cfg.token_file), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            creds = None  # revoked or stale: fall through to a fresh consent
    if not (creds and creds.valid):
        if cfg.client_id and cfg.client_secret:
            flow = InstalledAppFlow.from_client_config(client_config(cfg.client_id, cfg.client_secret), SCOPES)
        elif cfg.client_file.exists():
            flow = InstalledAppFlow.from_client_secrets_file(str(cfg.client_file), SCOPES)
        else:
            raise DriveConfigError(
                f"no way to sign in: set {KEY_CLIENT_ID} + {KEY_CLIENT_SECRET} in .env, or point "
                f"{KEY_CLIENT_FILE} at the OAuth client JSON (looked for {cfg.client_file})"
            )
        creds = flow.run_local_server(port=0)
    cfg.token_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def upload_deck(deck_path: Path, cfg: DriveConfig, service=None) -> UploadResult:
    """Upload one PPTX into the configured folder as a Google Slides file.

    ``service`` is injectable so tests can pass a stub; callers normally leave it
    ``None`` and get a signed-in Drive v3 client.
    """
    deck_path = Path(deck_path)
    if not deck_path.is_file():
        raise FileNotFoundError(deck_path)
    build, MediaFileUpload = _google_libs()
    if service is None:
        service = build("drive", "v3", credentials=_sign_in(cfg), cache_discovery=False)
    created = (
        service.files()
        .create(
            body=upload_metadata(deck_path, cfg.folder_id),
            media_body=MediaFileUpload(str(deck_path), mimetype=PPTX_MIME),
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )
    return UploadResult(
        file_id=created["id"], name=created.get("name", deck_path.stem), link=created.get("webViewLink", "")
    )
