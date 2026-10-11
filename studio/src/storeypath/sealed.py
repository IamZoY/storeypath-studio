"""Files locked with a password (docs/SEALED-FILES.md): a package, a project file or a
backup sent where others could read it, readable again only with the password.

AES-256-GCM, its key made from the password by PBKDF2-HMAC-SHA256 (600,000 rounds, a
salt of its own): what any program reads with its platform's own cryptography (Python's
cryptography, Go's crypto/aes and crypto/pbkdf2, a browser's WebCrypto). The file is
sealed a mebibyte at a time, so that a backup of any size is sealed as it is written,
each piece numbered and the last marked: a piece changed, moved, left out or added is
found, as is a wrong password.

    header  "SPSEALED" · version (1) · KDF (1: PBKDF2-HMAC-SHA256) · rounds (u32, big-endian)
            · salt (16 bytes) · nonce prefix (7 bytes) · piece size (u32, big-endian)
    pieces  each its plaintext (piece size bytes; the last fewer, maybe none) sealed with
            the nonce prefix · its number (u32, big-endian) · 1 for the last else 0, the
            header as associated data: its ciphertext and 16-byte tag
"""

from __future__ import annotations

import hashlib
import os
import struct
import unicodedata
from typing import BinaryIO

MAGIC = b"SPSEALED"
VERSION = 1
KDF_PBKDF2_SHA256 = 1
ROUNDS = 600_000  # PBKDF2-HMAC-SHA256 (as OWASP has it, 2023)
PIECE = 1 << 20  # bytes of plaintext a piece
TAG = 16
SUFFIX = ".enc"
PASSWORD_MIN = 8
_HEADER = struct.Struct(">8sBBI16s7sI")  # magic, version, kdf, rounds, salt, nonce prefix, piece
HEADER_SIZE = _HEADER.size


class SealError(ValueError):
    """A sealed file that cannot be opened: no password, the wrong one, or a damaged file."""

    def __init__(self, message: str, needs_password: bool = False):
        super().__init__(message)
        self.needs_password = needs_password


def is_sealed(data: bytes) -> bool:
    return data[:len(MAGIC)] == MAGIC


def sealed_name(name: str) -> str:
    return name + SUFFIX


def opened_name(name: str) -> str:
    return name[:-len(SUFFIX)] if name.lower().endswith(SUFFIX) else name


def check_password(password) -> str:
    """A password a file may be sealed with: text of PASSWORD_MIN characters or more."""
    if not isinstance(password, str) or len(password) < PASSWORD_MIN:
        raise ValueError(f"a password of at least {PASSWORD_MIN} characters")
    return password


def _key(password: str, salt: bytes, rounds: int) -> bytes:
    # (written the same however it was typed: é as one character or two)
    return hashlib.pbkdf2_hmac("sha256", unicodedata.normalize("NFC", password).encode("utf-8"), salt, rounds, 32)


def _nonce(prefix: bytes, n: int, last: bool) -> bytes:
    return prefix + struct.pack(">I", n) + (b"\x01" if last else b"\x00")


class Sealer:
    """Writes a sealed file to ``out`` as its plaintext is written to it; close() writes
    the last piece (nothing is sealed without it)."""

    def __init__(self, out: BinaryIO, password: str, rounds: int | None = None, piece: int = PIECE):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        rounds = rounds or ROUNDS
        salt, self.prefix = os.urandom(16), os.urandom(7)
        self.header = _HEADER.pack(MAGIC, VERSION, KDF_PBKDF2_SHA256, rounds, salt, self.prefix, piece)
        self.aead = AESGCM(_key(check_password(password), salt, rounds))
        self.out, self.piece, self.n, self.buf = out, piece, 0, bytearray()
        out.write(self.header)

    def write(self, data) -> int:
        self.buf += data
        while len(self.buf) > self.piece:  # (a full piece is sealed once more follows: the last is marked)
            self._seal(bytes(self.buf[:self.piece]), last=False)
            del self.buf[:self.piece]
        return len(data)

    def flush(self) -> None:  # (what is written is sealed a piece at a time)
        pass

    def close(self) -> None:
        self._seal(bytes(self.buf), last=True)
        self.buf = bytearray()

    def _seal(self, plain: bytes, last: bool) -> None:
        if self.n >= 0xFFFFFFFF:
            raise ValueError("too large to seal")
        self.out.write(self.aead.encrypt(_nonce(self.prefix, self.n, last), plain, self.header))
        self.n += 1


def seal(data: bytes, password: str, rounds: int | None = None) -> bytes:
    """``data`` sealed with ``password``."""
    import io

    out = io.BytesIO()
    sealer = Sealer(out, password, rounds)
    sealer.write(data)
    sealer.close()
    return out.getvalue()


def unseal(data: bytes, password: str | None) -> bytes:
    """A sealed file's plaintext. Raises SealError: no password, the wrong one, or damaged."""
    import io

    out = io.BytesIO()
    unseal_stream(io.BytesIO(data), out, password)
    return out.getvalue()


def unseal_stream(src: BinaryIO, out: BinaryIO, password: str | None) -> None:
    """A sealed file read from ``src``, its plaintext written to ``out`` a piece at a time
    (each piece checked before it is written). Raises SealError: no password, the wrong
    one, or a damaged file (what was written of it then is not to be used)."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    header = src.read(HEADER_SIZE)
    if not is_sealed(header):
        raise SealError("not a file sealed with a password")
    if len(header) < HEADER_SIZE:
        raise SealError("the sealed file is cut short")
    magic, version, kdf, rounds, salt, prefix, piece = _HEADER.unpack(header)
    if version != VERSION or kdf != KDF_PBKDF2_SHA256 or not (1000 <= rounds <= 10_000_000) or not (1 <= piece <= 1 << 26):
        raise SealError("a sealed file of a kind this Studio does not read (made by a newer one?)")
    if not password:
        raise SealError("the file is sealed: its password is needed", needs_password=True)
    aead = AESGCM(_key(password, salt, rounds))
    size, n = piece + TAG, 0
    chunk = src.read(size)
    while True:
        after = src.read(size)  # (a piece is the last when none follows it)
        try:
            plain = aead.decrypt(_nonce(prefix, n, not after), chunk, header)
        except InvalidTag:
            if n == 0:
                raise SealError("the password is not this file's (or the file was changed)", needs_password=True) from None
            raise SealError("the sealed file was changed or cut short") from None
        out.write(plain)
        if not after:
            return
        chunk, n = after, n + 1
