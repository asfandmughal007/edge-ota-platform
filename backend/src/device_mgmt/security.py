"""Credential generation, hashing and comparison.

Device tokens are 256-bit random values, so a fast unsalted hash (SHA-256) is
enough to protect them at rest: there is nothing to brute-force, unlike a
human-chosen password. A database leak therefore does not reveal usable tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_PREFIX = "edt_"


def generate_device_token() -> str:
    # The prefix makes leaked tokens easy to spot in logs and secret scanners.
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def keys_match(provided: str, expected: str) -> bool:
    """Constant-time comparison. Comparing digests hides length as well as content."""
    return hmac.compare_digest(hash_token(provided), hash_token(expected))
