"""Small eWeLink AES-CBC helpers used by the local research tools."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def _key(devicekey: str) -> bytes:
    return hashlib.md5(devicekey.encode("utf-8")).digest()


def encrypt_data(data: dict[str, Any], devicekey: str, iv: bytes | None = None) -> tuple[str, str]:
    iv = os.urandom(16) if iv is None else iv
    if len(iv) != 16:
        raise ValueError("eWeLink AES-CBC IV must be 16 bytes")
    plaintext = json.dumps(data).encode("utf-8")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_key(devicekey)), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii"), base64.b64encode(iv).decode("ascii")


def decrypt_data(ciphertext_b64: str, iv_b64: str, devicekey: str) -> dict[str, Any]:
    ciphertext = base64.b64decode(ciphertext_b64)
    iv = base64.b64decode(iv_b64)
    decryptor = Cipher(algorithms.AES(_key(devicekey)), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return json.loads(plaintext)

