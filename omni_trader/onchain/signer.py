"""Signers. Private material is read from env vars and never logged."""
from __future__ import annotations

import os
from typing import Optional

from .base import Signer


class EnvSigner(Signer):
    """Loads a private key from an environment variable on demand.

    The secret is held in memory only and is never printed or written.
    """

    def __init__(self, env_var: str):
        self._env_var = env_var
        self._key: Optional[str] = None

    def _load(self) -> str:
        if self._key is None:
            val = os.environ.get(self._env_var)
            if not val:
                raise RuntimeError(
                    f"Live signing requested but ${self._env_var} is not set. "
                    f"Refusing to operate without a key."
                )
            self._key = val
        return self._key

    def public_key(self) -> str:
        # Derive pubkey only when a real signing lib is available.
        secret = self._load()
        try:
            from solders.keypair import Keypair  # type: ignore
            import base58  # type: ignore

            kp = Keypair.from_base58_string(secret)
            return str(kp.pubkey())
        except Exception:
            # Cannot derive without deps; return a redacted placeholder.
            return "<pubkey-requires-solders>"

    def sign_raw(self, payload: bytes) -> bytes:
        secret = self._load()
        from solders.keypair import Keypair  # type: ignore
        import base58  # type: ignore

        kp = Keypair.from_base58_string(secret)
        return bytes(kp.sign_message(payload))

    def __repr__(self) -> str:  # never leak the secret
        return f"EnvSigner(env_var={self._env_var!r}, loaded={self._key is not None})"

    def __str__(self) -> str:
        return self.__repr__()
