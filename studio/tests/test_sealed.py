"""Files sealed with a password (sealed.py, docs/SEALED-FILES.md): AES-256-GCM a piece at a
time, its key from the password by PBKDF2; what is changed, cut short or opened with the
wrong password is refused. Packages, project files and backups downloaded sealed; a sealed
file opened, its password asked for; the command line's backup, restore, seal and unseal."""

import gzip
import io
import json
import os
import struct
import zipfile

import pytest
from typer.testing import CliRunner

from sessions import call
from storeypath import sealed
from storeypath.cli import app
from storeypath.sealed import (HEADER_SIZE, MAGIC, PIECE, SealError, Sealer, is_sealed, opened_name, seal, sealed_name,
                               unseal, unseal_stream)
from test_item_types import _exported, studio  # noqa: F401 (a Studio served, its people logged in)

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(sealed, "ROUNDS", 1000)  # (600,000 in use: a test need not wait)


def _sealed(data: bytes, piece: int) -> bytes:
    out = io.BytesIO()
    s = Sealer(out, PASSWORD, piece=piece)
    s.write(data)
    s.close()
    return out.getvalue()


# ---- the file ----------------------------------------------------------------------------------

@pytest.mark.parametrize("size", [0, 1, 99, 100, 101, 1000])
def test_a_file_sealed_opens_with_its_password_whatever_its_size(size):
    data = os.urandom(size)
    locked = _sealed(data, piece=100)  # (pieces of 100 bytes: many, an exact number, or none full)
    pieces = max(1, -(-size // 100))  # (the last marked so: a full one, or what is left, or none at all)
    assert is_sealed(locked) and len(locked) == HEADER_SIZE + size + 16 * pieces
    assert unseal(locked, PASSWORD) == data
    out = io.BytesIO()
    unseal_stream(io.BytesIO(locked), out, PASSWORD)
    assert out.getvalue() == data


def test_each_seal_is_its_own_and_its_header_says_how_it_was_made():
    a, b = seal(b"the same", PASSWORD), seal(b"the same", PASSWORD)
    assert a != b  # a salt and a nonce of its own each time
    magic, version, kdf, rounds, salt, prefix, piece = struct.unpack(">8sBBI16s7sI", a[:HEADER_SIZE])
    assert (magic, version, kdf, rounds, piece) == (MAGIC, 1, 1, 1000, PIECE)


def test_the_wrong_password_a_changed_file_or_one_cut_short_is_refused():
    data = os.urandom(450)
    locked = _sealed(data, piece=100)
    with pytest.raises(SealError, match="password is needed") as no:
        unseal(locked, "")
    assert no.value.needs_password
    with pytest.raises(SealError, match="not this file's") as wrong:
        unseal(locked, "wrong horse battery")
    assert wrong.value.needs_password
    changed = bytearray(locked)
    changed[HEADER_SIZE + 116 * 2 + 5] ^= 1  # a byte of its third piece
    with pytest.raises(SealError, match="changed or cut short"):
        unseal(bytes(changed), PASSWORD)
    with pytest.raises(SealError, match="changed or cut short"):  # cut after a whole piece: its last is missing
        unseal(locked[:HEADER_SIZE + 116 * 2], PASSWORD)
    swapped = locked[:HEADER_SIZE] + locked[HEADER_SIZE + 116:HEADER_SIZE + 232] + locked[HEADER_SIZE:HEADER_SIZE + 116] \
        + locked[HEADER_SIZE + 232:]
    with pytest.raises(SealError):  # pieces put in another order
        unseal(swapped, PASSWORD)
    header = bytearray(locked)
    header[12] ^= 1  # its rounds changed: the key is another, and the header is sealed with every piece
    with pytest.raises(SealError):
        unseal(bytes(header), PASSWORD)
    with pytest.raises(SealError, match="not a file sealed"):
        unseal(b"PK\x03\x04 a package", PASSWORD)


def test_the_viewers_sealed_sample_opens_here_as_the_package_it_was():
    # StoreyPath Viewer's readers (3D, 2D, Go) open the same sample (spec/SEALED.md): what
    # Studio seals, they read, and the other way round
    from storeypath.assets import asset_dir

    corpus = asset_dir("spec") / "conformance"
    v = json.loads((corpus / "sealed.json").read_text())
    plain = unseal((corpus / v["sealed"]).read_bytes(), v["password"])
    assert plain == (corpus / v["plain"]).read_bytes()
    with pytest.raises(SealError, match="not this file's"):
        unseal((corpus / v["sealed"]).read_bytes(), "another password")


def test_a_password_is_one_however_its_letters_were_typed_and_long_enough():
    assert unseal(seal(b"x", "café au lait"), "café au lait") == b"x"  # é as one character or two
    with pytest.raises(ValueError, match="at least 8"):
        seal(b"x", "short")
    assert (sealed_name("a.storeypath"), opened_name("a.storeypath.enc"), opened_name("a.zip")) == \
        ("a.storeypath.enc", "a.storeypath", "a.zip")


# ---- through Studio ------------------------------------------------------------------------------

def _post(port, path, body, token):
    """A POST whose answer is a file: its status and bytes (call reads JSON when it can)."""
    return call(port, "POST", path, body, token=token)


def test_a_package_and_the_project_file_are_downloaded_sealed_and_open_with_the_password(studio):  # noqa: F811
    port, _, _, tokens, code = studio
    hq = f"{code}-DEMO-HQ"
    plain = _exported(port, code, hq, tokens["boss"])
    files = call(port, "GET", f"/api/projects/{code}", token=tokens["boss"])[1]["exports"]
    status, locked, res = _post(port, f"/api/projects/{code}/exports/{files[0]}", {"password": PASSWORD}, tokens["boss"])
    assert status == 200 and is_sealed(locked) and unseal(locked, PASSWORD) == plain
    assert f'filename="{files[0]}.enc"' in res.getheader("Content-Disposition", "")
    status, said, _ = _post(port, f"/api/projects/{code}/exports/{files[0]}", {"password": "short"}, tokens["boss"])
    assert status == 400 and "at least 8" in said["error"]
    assert _post(port, f"/api/projects/{code}/exports/{files[0]}", {"password": PASSWORD}, tokens["bob"])[0] in (403, 404)

    # the project file sealed: opened again only with its password (Studio asks for it)
    status, locked, _ = _post(port, f"/api/projects/{code}/project.storeypath-project", {"password": PASSWORD}, tokens["boss"])
    assert status == 200 and is_sealed(locked)
    with zipfile.ZipFile(io.BytesIO(unseal(locked, PASSWORD))) as z:
        assert z.namelist()
    status, said, _ = call(port, "PUT", "/api/open", raw=locked, token=tokens["boss"])
    assert status == 400 and said["password"] is True and "password is needed" in said["error"]
    status, said, _ = call(port, "PUT", "/api/open", raw=locked, token=tokens["boss"],
                           headers={"X-StoreyPath-Password": "wrong%20horse"})
    assert status == 400 and said["password"] is True and "not this file's" in said["error"]
    status, said, _ = call(port, "PUT", "/api/open", raw=locked, token=tokens["boss"],
                           headers={"X-StoreyPath-Password": "correct%20horse%20battery"})
    assert status == 409 and said["code"] == code  # opened: the project is here already (it would be replaced)


def test_item_types_are_taken_from_a_sealed_file_and_a_backup_is_downloaded_sealed(studio):  # noqa: F811
    port, _, _, tokens, _ = studio
    sent = seal(json.dumps({"types": [{"code": "PLANTER", "name_en": "Planter", "shape": "box"}]}).encode(), PASSWORD)
    assert call(port, "PUT", "/api/catalogue/types-in", raw=sent, token=tokens["kim"])[1]["password"] is True
    status, got, _ = call(port, "PUT", "/api/catalogue/types-in", raw=sent, token=tokens["kim"],
                          headers={"X-StoreyPath-Password": "correct%20horse%20battery"})
    assert status == 200 and [t["code"] for t in got["types"]] == ["PLANTER"]

    status, locked, res = _post(port, "/api/backup", {"password": PASSWORD}, tokens["boss"])
    assert status == 200 and is_sealed(locked) and ".sql.gz.enc" in res.getheader("Content-Disposition", "")
    assert gzip.decompress(unseal(locked, PASSWORD)).startswith(b"--")
    assert _post(port, "/api/backup", {"password": PASSWORD}, tokens["kim"])[0] == 403


# ---- the command line ------------------------------------------------------------------------------

def test_the_command_line_seals_a_backup_restores_it_and_seals_and_opens_any_file(tmp_path, monkeypatch):
    from storeypath import accounts as acc
    from storeypath.accounts import Accounts

    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    data = tmp_path / "data"
    Accounts(data).add_user("boss", "a long password", role="admin", must_change_password=False)
    out = tmp_path / "copy.sql.gz.enc"
    runner = CliRunner()
    r = runner.invoke(app, ["backup", "--data", str(data), "--out", str(out), "--seal", "--password-stdin"], input=PASSWORD + "\n")
    assert r.exit_code == 0, r.output
    assert out.read_bytes().startswith(MAGIC) and oct(out.stat().st_mode & 0o777) == "0o600"
    r = runner.invoke(app, ["restore", str(out), "--data", str(tmp_path / "new"), "--password-stdin"], input="wrong horse\n")
    assert r.exit_code != 0 and "not this file's" in r.output
    r = runner.invoke(app, ["restore", str(out), "--data", str(tmp_path / "new"), "--password-stdin"], input=PASSWORD + "\n")
    assert r.exit_code == 0, r.output
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".storeypath-restore-")]  # (what was opened, gone)

    plain = tmp_path / "hq.storeypath"
    plain.write_bytes(os.urandom(3 << 20))
    r = runner.invoke(app, ["seal", str(plain), "--password-stdin"], input=PASSWORD + "\n")
    assert r.exit_code == 0, r.output
    locked = tmp_path / "hq.storeypath.enc"
    plain.rename(tmp_path / "before")
    r = runner.invoke(app, ["unseal", str(locked), "--password-stdin"], input=PASSWORD + "\n")
    assert r.exit_code == 0, r.output
    assert plain.read_bytes() == (tmp_path / "before").read_bytes()
    r = runner.invoke(app, ["seal", str(plain), "--out", str(tmp_path / "x.enc"), "--password-stdin"], input="short\n")
    assert r.exit_code != 0 and "at least 8" in r.output
