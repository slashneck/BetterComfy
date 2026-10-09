"""The vault: pictures and videos only you can open.

Your password goes through Argon2id (256 MiB of memory, 3 passes) and unlocks a random 256-bit vault key; a recovery
key unlocks it too. Every file gets its own key (HKDF from the vault key and the file's random id) and is encrypted with
AES-256-GCM in 1 MiB chunks: each chunk is authenticated with its number and whether it is the last one, so a changed,
reordered or cut off file is refused instead of shown. The list of what is inside (names, prompts, settings, sizes)
is encrypted the same way. Files are only ever decrypted into memory, never to disk.

From the outside the vault shows little: every file is padded to a size step (Padme, at most about 12 % larger), so
its size says little about what it is, and all of its files carry the same fixed date, so nobody can tell when
something was added. "Re-encrypt" (with a password change) moves everything to a new key and shreds the old files.

Limits, said plainly: while the vault is open its key and what you look at are in memory, and Windows may page memory
to disk. Malware running as you, or someone with your password or recovery key, gets in."""
import base64
import json
import os
import secrets
import struct
import threading
import time
import uuid

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import BASE

DIR = os.path.join(BASE, "vault")
HEADER = os.path.join(DIR, "vault.json")
INDEX = os.path.join(DIR, "index.bcv")
BLOBS = os.path.join(DIR, "files")
MAGIC = b"BCV1"
CHUNK = 1 << 20
KDF = {"alg": "argon2id", "m": 262144, "t": 3, "p": 4}


class VaultError(Exception):
    pass


class WrongPassword(VaultError):
    pass


def _b64(b):
    return base64.b64encode(b).decode("ascii")


def _unb64(s):
    return base64.b64decode(s.encode("ascii"))


def _kdf(secret, salt, params=KDF):
    return hash_secret_raw(secret.encode("utf-8") if isinstance(secret, str) else secret, salt,
                           time_cost=params["t"], memory_cost=params["m"], parallelism=params["p"], hash_len=32,
                           type=Type.ID)


def _wrap(kek, key, label):
    nonce = secrets.token_bytes(12)
    return _b64(nonce + AESGCM(kek).encrypt(nonce, key, label))


def _unwrap(kek, blob, label):
    raw = _unb64(blob)
    return AESGCM(kek).decrypt(raw[:12], raw[12:], label)


def new_recovery_key():
    """20 random bytes as 8 groups of 4 letters / digits (Crockford base32, no 0/O or 1/I mix-ups)."""
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    n = int.from_bytes(secrets.token_bytes(20), "big")
    chars = []
    for _ in range(32):
        n, r = divmod(n, 32)
        chars.append(alphabet[r])
    s = "".join(chars)
    return "-".join(s[i:i + 4] for i in range(0, 32, 4))


def _norm_recovery(rk):
    rk = rk.upper().replace(" ", "").replace("-", "").replace("O", "0").replace("I", "1").replace("L", "1")
    return "-".join(rk[i:i + 4] for i in range(0, len(rk), 4))


# ------------------------------------------------------------------------------------------------ file format

def padme(n):
    """The padded size for n bytes (Padme: sizes leak only a few bits, at most about 12 % overhead)."""
    n = max(n, 4096)
    e = n.bit_length() - 1
    s_ = e.bit_length()
    low = e - s_
    mask = (1 << low) - 1 if low > 0 else 0
    return (n + mask) & ~mask


def _padded(chunks, size):
    """The chunks, then zeros up to the padded size."""
    for c in chunks:
        yield c
    left = padme(size) - size
    while left > 0:
        k = min(left, CHUNK)
        yield bytes(k)
        left -= k


class _reading:
    """Opens a vault file for reading and gives it its fixed date back afterwards (Windows notes every read)."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.fh = open(self.path, "rb")
        return self.fh

    def __exit__(self, *exc):
        self.fh.close()
        fix_times(self.path)
        fix_times(os.path.dirname(self.path))
        return False


FIXED_TIME = 1577836800                 # 2020-01-01: every vault file carries this date, so none says when it came


def fix_times(path):
    """Creation, change and access date all set to FIXED_TIME (folders too)."""
    try:
        os.utime(path, (FIXED_TIME, FIXED_TIME))
    except OSError:
        pass
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.HANDLE]
        k.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k.CreateFileW(path, 0x100, 7, None, 3, 0x02000000, None)    # WRITE_ATTRIBUTES, BACKUP_SEMANTICS
        if h in (None, wintypes.HANDLE(-1).value):
            return
        ft = ctypes.c_uint64((FIXED_TIME + 11644473600) * 10_000_000)
        k.SetFileTime(h, ctypes.byref(ft), ctypes.byref(ft), ctypes.byref(ft))
        k.CloseHandle(h)
    except Exception:
        pass

def _file_key(vault_key, blob_id):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=blob_id.encode("ascii"),
                info=b"bettercomfy file").derive(vault_key)


def _aad(blob_id, i, last):
    return MAGIC + blob_id.encode("ascii") + struct.pack(">IB", i, 1 if last else 0)


def encrypt_stream(vault_key, blob_id, chunks, out):
    """chunks: an iterator of plaintext pieces (any size). Writes header + encrypted chunks to the open file `out`."""
    key = AESGCM(_file_key(vault_key, blob_id))
    prefix = secrets.token_bytes(8)
    out.write(MAGIC + struct.pack(">I", CHUNK) + prefix)
    buf = b""
    i = 0
    pending = None

    def emit(data, last):
        nonlocal i
        nonce = prefix + struct.pack(">I", i)
        out.write(key.encrypt(nonce, data, _aad(blob_id, i, last)))
        i += 1
    for piece in chunks:
        buf += piece
        while len(buf) >= CHUNK:
            if pending is not None:
                emit(pending, False)
            pending, buf = buf[:CHUNK], buf[CHUNK:]
    if pending is not None and buf:
        emit(pending, False)
        emit(buf, True)
    elif pending is not None:
        emit(pending, True)
    else:
        emit(buf, True)                 # also an empty file: one (empty) last chunk


def decrypt_stream(vault_key, blob_id, f):
    """Yields the plaintext chunks of an open encrypted file - refusing anything that was changed, reordered or cut."""
    head = f.read(16)
    if len(head) != 16 or head[:4] != MAGIC:
        raise VaultError("Not a vault file")
    size = struct.unpack(">I", head[4:8])[0]
    prefix = head[8:16]
    key = AESGCM(_file_key(vault_key, blob_id))
    i = 0
    block = f.read(size + 16)
    while True:
        nxt = f.read(size + 16)
        last = not nxt
        try:
            yield key.decrypt(prefix + struct.pack(">I", i), block, _aad(blob_id, i, last))
        except InvalidTag:
            raise VaultError("This vault file is damaged or was changed") from None
        if last:
            return
        block = nxt
        i += 1


# ------------------------------------------------------------------------------------------------ the vault

class Vault:
    def __init__(self):
        self._key = None
        self.entries = []
        self._lock = threading.RLock()
        self.unlocked_at = 0.0
        self.parked = []                # results made while the vault was locked: only in memory until it opens

    # ---- state
    @staticmethod
    def exists():
        return os.path.isfile(HEADER)

    def is_open(self):
        return self._key is not None

    def lock(self):
        with self._lock:
            self._key = None
            self.entries = []

    def destroy(self):
        """Deletes the whole vault. The header with the wrapped keys is shredded first: without it nothing in there
        can ever be decrypted again, with or without the password."""
        import shutil
        from . import shred
        with self._lock:
            self.lock()
            self.parked = []
            for f in (HEADER, INDEX):
                if os.path.isfile(f):
                    shred.shred_file(f, 1)
            shutil.rmtree(DIR, ignore_errors=True)

    # ---- create / open
    def create(self, password):
        """Makes a new vault. Returns its recovery key (shown once)."""
        if self.exists():
            raise VaultError("There is a vault already")
        if len(password) < 8:
            raise VaultError("Use at least 8 characters")
        os.makedirs(BLOBS, exist_ok=True)
        key = secrets.token_bytes(32)
        rk = new_recovery_key()
        salt, rsalt = secrets.token_bytes(16), secrets.token_bytes(16)
        head = {"version": 1, "kdf": dict(KDF, salt=_b64(salt)), "key": _wrap(_kdf(password, salt), key, b"password"),
                "recovery": {"kdf": dict(KDF, salt=_b64(rsalt)),
                             "key": _wrap(_kdf(_norm_recovery(rk), rsalt), key, b"recovery")},
                "created": time.strftime("%Y-%m-%d")}
        self._write_json(HEADER, head)
        with self._lock:
            self._key = key
            self.entries = []
            self._save_index()
            self.unlocked_at = time.time()
        return rk

    def _head(self):
        self._finish_swap()
        try:
            with open(HEADER, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            raise VaultError("The vault can't be read") from None

    def unlock(self, password):
        head = self._head()
        k = head["kdf"]
        try:
            key = _unwrap(_kdf(password, _unb64(k["salt"]), k), head["key"], b"password")
        except InvalidTag:
            raise WrongPassword("Wrong password") from None
        self._open(key)

    def unlock_with_recovery(self, recovery_key, new_password):
        """The recovery key opens the vault and sets a new password."""
        head = self._head()
        r = head["recovery"]
        try:
            key = _unwrap(_kdf(_norm_recovery(recovery_key), _unb64(r["kdf"]["salt"]), r["kdf"]), r["key"],
                          b"recovery")
        except InvalidTag:
            raise WrongPassword("That recovery key doesn't fit this vault") from None
        self._open(key)
        self.set_password(new_password)

    def set_password(self, new_password):
        if not self.is_open():
            raise VaultError("The vault is locked")
        if len(new_password) < 8:
            raise VaultError("Use at least 8 characters")
        head = self._head()
        salt = secrets.token_bytes(16)
        head["kdf"] = dict(KDF, salt=_b64(salt))
        head["key"] = _wrap(_kdf(new_password, salt), self._key, b"password")
        self._write_json(HEADER, head)

    def _open(self, key):
        with self._lock:
            self._key = key
            self.entries = self._load_index()
            self.unlocked_at = time.time()
            parked, self.parked = self.parked, []
        for data, meta, thumb in parked:
            self.add(data, meta, thumb)

    def park(self, data, meta, thumb):
        with self._lock:
            self.parked.append((data, meta, thumb))

    # ---- the index (encrypted list of what is inside)
    def _load_index(self, key=None):
        if not os.path.isfile(INDEX):
            return []
        with _reading(INDEX) as fh:
            data = b"".join(decrypt_stream(key or self._key, "index", fh)).rstrip(b"\0")
        return json.loads(data.decode("utf-8")).get("entries", [])

    def _write_index(self, path, key, entries):
        os.makedirs(DIR, exist_ok=True)
        data = json.dumps({"entries": entries}, ensure_ascii=False).encode("utf-8")
        with open(path, "wb") as fh:
            encrypt_stream(key, "index", _padded(iter([data]), len(data)), fh)
            fh.flush()
            os.fsync(fh.fileno())
        fix_times(path)

    def _save_index(self):
        self._write_index(INDEX + ".tmp", self._key, self.entries)
        os.replace(INDEX + ".tmp", INDEX)
        fix_times(INDEX)
        fix_times(DIR)

    @staticmethod
    def _write_json(path, obj, final=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        fix_times(path)

    @staticmethod
    def _finish_swap():
        """A re-encryption that was cut off (power loss) between its last two steps is finished here."""
        if os.path.isfile(HEADER + ".new") and os.path.isfile(INDEX + ".new"):
            os.replace(INDEX + ".new", INDEX)
            os.replace(HEADER + ".new", HEADER)
        else:
            for f in (HEADER + ".new", INDEX + ".new"):
                if os.path.isfile(f):
                    os.remove(f)

    # ---- files
    def _need(self):
        if not self.is_open():
            raise VaultError("The vault is locked")

    def _put_blob(self, chunks, size, key=None):
        blob = uuid.uuid4().hex
        os.makedirs(BLOBS, exist_ok=True)
        path = os.path.join(BLOBS, blob + ".bcv")
        with open(path + ".tmp", "wb") as fh:
            encrypt_stream(key or self._key, blob, _padded(chunks, size), fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(path + ".tmp", path)
        fix_times(path)
        fix_times(BLOBS)
        return blob

    def add(self, data, meta, thumb=None):
        """Puts bytes (a picture or video) into the vault with its details. Returns the new entry."""
        return self._add(iter([data]), meta, thumb, len(data))

    def add_file(self, path, meta, thumb=None):
        def chunks():
            with open(path, "rb") as fh:
                while True:
                    b = fh.read(CHUNK)
                    if not b:
                        return
                    yield b
        return self._add(chunks(), meta, thumb, os.path.getsize(path))

    def _add(self, chunks, meta, thumb, size):
        with self._lock:
            self._need()
            e = dict(meta)
            e.update(id=uuid.uuid4().hex[:12], vault=True, blob=self._put_blob(chunks, size), size=size,
                     added=time.strftime("%Y-%m-%d %H:%M:%S"), pad=1)
            e.setdefault("created", e["added"])
            e["thumb_blob"] = self._put_blob(iter([thumb]), len(thumb)) if thumb else None
            e["thumb_len"] = len(thumb) if thumb else 0
            for k in ("file", "thumb"):
                e.pop(k, None)          # no paths of the plain copy inside the vault
            self.entries.append(e)
            self._save_index()
            return e

    @staticmethod
    def _real_len(entry, which):
        """The size without padding (None: an entry from before padding)."""
        if not entry.get("pad"):
            return None
        return entry.get("size") if which == "blob" else entry.get("thumb_len")

    def read(self, entry, which="blob", key=None):
        """The decrypted bytes of an entry (which: 'blob' or 'thumb_blob'), in memory."""
        if key is None:
            self._need()
        blob = entry.get(which)
        if not blob:
            return None
        with _reading(os.path.join(BLOBS, blob + ".bcv")) as fh:
            data = b"".join(decrypt_stream(key or self._key, blob, fh))
        n = self._real_len(entry, which)
        return data if n is None else data[:n]

    def export(self, entry, dest):
        """Decrypts an entry into a file (for taking it out of the vault)."""
        self._need()
        blob = entry["blob"]
        left = self._real_len(entry, "blob")
        with _reading(os.path.join(BLOBS, blob + ".bcv")) as fh, open(dest + ".part", "wb") as out:
            for b in decrypt_stream(self._key, blob, fh):
                if left is not None:
                    b = b[:left]
                    left -= len(b)
                out.write(b)
        os.replace(dest + ".part", dest)
        return dest

    def rekey(self, new_password, progress=None):
        """Re-encrypts everything with a brand new key under a new password, and shreds the old files, so an old
        copy of the vault and the old password (or recovery key) are of no use any more. Returns the new recovery
        key. progress(done, total) is called along the way."""
        from . import shred
        if len(new_password) < 8:
            raise VaultError("Use at least 8 characters")
        with self._lock:
            self._need()
            new_key = secrets.token_bytes(32)
            entries, made, old = [], [], []
            total = len(self.entries)
            try:
                for i, e in enumerate(self.entries):
                    e2 = dict(e, pad=1)
                    data = self.read(e, "blob")
                    e2["blob"] = self._put_blob(iter([data]), len(data), new_key)
                    made.append(e2["blob"])
                    if e.get("thumb_blob"):
                        th = self.read(e, "thumb_blob")
                        e2["thumb_blob"] = self._put_blob(iter([th]), len(th), new_key)
                        e2["thumb_len"] = len(th)
                        made.append(e2["thumb_blob"])
                    old += [b for b in (e.get("blob"), e.get("thumb_blob")) if b]
                    entries.append(e2)
                    if progress:
                        progress(i + 1, total)
            except Exception:
                for b in made:                          # nothing changed: the new copies go again
                    try:
                        os.remove(os.path.join(BLOBS, b + ".bcv"))
                    except OSError:
                        pass
                raise
            rk = new_recovery_key()
            salt, rsalt = secrets.token_bytes(16), secrets.token_bytes(16)
            head = dict(self._head(), version=1, kdf=dict(KDF, salt=_b64(salt)),
                        key=_wrap(_kdf(new_password, salt), new_key, b"password"),
                        recovery={"kdf": dict(KDF, salt=_b64(rsalt)),
                                  "key": _wrap(_kdf(_norm_recovery(rk), rsalt), new_key, b"recovery")})
            self._write_index(INDEX + ".new", new_key, entries)
            self._write_json(HEADER + ".new", head)
            # the old header holds the old key: overwritten where it lies before the new one takes its place
            for f in (HEADER, INDEX):
                try:
                    shred.overwrite(f, 1)
                except OSError:
                    pass
            self._finish_swap()
            self._key, self.entries = new_key, entries
        for b in old:
            shred.shred_file(os.path.join(BLOBS, b + ".bcv"))
        return rk

    def get(self, eid):
        return next((e for e in self.entries if e["id"] == eid), None)

    def update(self, eid, **fields):
        with self._lock:
            e = self.get(eid)
            if e is not None:
                e.update(fields)
                self._save_index()

    def remove(self, eids):
        """Takes entries out and deletes their encrypted files (they are useless without the key anyway)."""
        from . import shred
        with self._lock:
            self._need()
            gone = [e for e in self.entries if e["id"] in set(eids)]
            self.entries = [e for e in self.entries if e["id"] not in set(eids)]
            self._save_index()
        for e in gone:
            for b in (e.get("blob"), e.get("thumb_blob")):
                if b:
                    shred.shred_file(os.path.join(BLOBS, b + ".bcv"))
        return len(gone)

    def recent(self, kind=None):
        out = [e for e in self.entries if kind is None or e.get("kind") == kind]
        return sorted(out, key=lambda e: e.get("created", ""), reverse=True)


vault = Vault()
