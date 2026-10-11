# Files sealed with a password

Studio can encrypt what it lets you download with a password, for sending somewhere
others could read it: a building's package (`.storeypath`), a project file
(`.storeypath-project`) and a backup (`.sql.gz`). The encrypted file is the same name
with `.enc` added. Studio opens one again when it is given the password: opening a
project or package, taking item types from a file, and `storeypath restore`. On the
command line, `storeypath seal FILE` and `storeypath unseal FILE.enc` do the same for any
file.

It is meant for files in transit: whoever has the password reads the file, and Studio
cannot recover a forgotten one. Send the password apart from the file (not in the same
email).

## The format

AES-256-GCM, with a key made from the password by PBKDF2-HMAC-SHA256. These are in every
platform's own cryptography (Python's `cryptography`, Go's `crypto/aes`, `crypto/cipher`
and `crypto/pbkdf2`, a browser's WebCrypto), so a reader needs nothing more. The file is
sealed in pieces of a mebibyte, so that a backup of any size is sealed as it is written
and opened as it is read; each piece is numbered and the last one marked, so a piece
changed, reordered, left out or added is caught, as is a wrong password.

```
header (41 bytes)
  magic        8 bytes   "SPSEALED"
  version      1 byte    1
  kdf          1 byte    1 = PBKDF2-HMAC-SHA256
  rounds       4 bytes   big-endian; 600,000 as Studio writes them
  salt         16 bytes  random
  nonce prefix 7 bytes   random
  piece size   4 bytes   big-endian; 1,048,576 as Studio writes them

pieces, one after another, to the end of the file
  each: AES-256-GCM(key, nonce, plaintext, associated data = the header) → ciphertext ‖ 16-byte tag
  nonce (12 bytes) = nonce prefix ‖ the piece's number (4 bytes, big-endian, from 0) ‖ 1 for the last piece, else 0
  every piece but the last holds exactly `piece size` bytes of plaintext (piece size + 16 bytes
  sealed); the last holds the rest: from 1 up to piece size bytes, or none when the file is empty
```

The key is `PBKDF2-HMAC-SHA256(password, salt, rounds, 32 bytes)`, with the password as
UTF-8 in Unicode normal form C (`é` typed as one character or as two is the same
password).

To read one: read the header and check the magic, version and kdf; derive the key; then
read `piece size + 16` bytes at a time. A piece is the last when nothing follows it.
Open each with its nonce and the header as associated data. If the first piece fails,
the password is wrong (or the file was changed); if a later one fails, the file was
changed or cut short. Anything already written out is then not to be used.

## Reading one elsewhere

StoreyPath Viewer reads them: its 3D world, map and 2D plan (`open(file, { password })`,
`readPackage(file, { password })`: a `SealedError` saying when the password is needed)
and its Go reader (`OpenSealed`, `ReadSealed`, `Unseal`; `ErrNeedsPassword`), as its
spec/SEALED.md says. Anything else can follow this format; in Python, with
`cryptography`:

```python
import hashlib, struct, unicodedata
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def unseal(data: bytes, password: str) -> bytes:
    magic, version, kdf, rounds, salt, prefix, piece = struct.unpack_from(">8sBBI16s7sI", data)
    assert magic == b"SPSEALED" and version == 1 and kdf == 1
    header = data[:41]
    key = hashlib.pbkdf2_hmac("sha256", unicodedata.normalize("NFC", password).encode(), salt, rounds, 32)
    aead, out, at, n = AESGCM(key), [], 41, 0
    while True:
        chunk, last = data[at:at + piece + 16], at + piece + 16 >= len(data)
        out.append(aead.decrypt(prefix + struct.pack(">I", n) + (b"\1" if last else b"\0"), chunk, header))
        if last:
            return b"".join(out)
        at, n = at + piece + 16, n + 1
```

Go (1.24 or later, standard library alone): `pbkdf2.Key(sha256.New, password, salt,
rounds, 32)`, then `cipher.NewGCM(aes.NewCipher(key))` and `gcm.Open(nil, nonce, piece,
header)` piece by piece, as above. A browser: `crypto.subtle.deriveKey` with PBKDF2 and
SHA-256, then `crypto.subtle.decrypt` with AES-GCM and the header as `additionalData`.
