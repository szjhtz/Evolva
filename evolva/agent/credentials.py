from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


SERVICE_NAME = "Evolva"


def credential_backend() -> str:
    """Return the explicitly configured credential backend.

    File storage remains the compatibility default. Production installations can
    opt into the operating-system credential store with
    ``EVOLVA_CREDENTIAL_BACKEND=keyring``.
    """

    backend = os.getenv("EVOLVA_CREDENTIAL_BACKEND", "file").strip().lower()
    if backend not in {"file", "keyring"}:
        raise RuntimeError(f"Unsupported Evolva credential backend: {backend}")
    return backend


def credential_account(path: Path, name: str) -> str:
    scope = hashlib.sha256(str(path.expanduser().resolve()).encode("utf-8")).hexdigest()[:16]
    return f"{scope}:{name}"


def _keyring() -> Any:
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "The keyring credential backend requires `pip install 'evolva[credentials]'`"
        ) from exc
    return keyring


def get_secret(account: str) -> str | None:
    if credential_backend() != "keyring":
        return None
    return _keyring().get_password(SERVICE_NAME, account)


def set_secret(account: str, value: str) -> None:
    if credential_backend() != "keyring":
        raise RuntimeError("set_secret requires the keyring credential backend")
    _keyring().set_password(SERVICE_NAME, account, value)


def delete_secret(account: str) -> None:
    if credential_backend() != "keyring":
        return
    keyring = _keyring()
    try:
        keyring.delete_password(SERVICE_NAME, account)
    except getattr(keyring.errors, "PasswordDeleteError", Exception):
        return
