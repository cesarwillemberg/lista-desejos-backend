"""Public short codes for shared lists."""

from __future__ import annotations

import secrets

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CODE_LENGTH = 7


def new_short_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
