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
import shutil
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
        self.collections = []           # [{"id", "name"}] - kept in the encrypted index like everything else
        self.offline = []               # files on a drive that is not plugged in: kept in the index, not shown
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
            self.offline = []
            self.collections = []

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
        self._mirror()

    def _open(self, key):
        with self._lock:
            self._key = key
            self.entries = self._load_index()
            self.offline = []
            self.collections = self._index_collections
            self.unlocked_at = time.time()
            parked, self.parked = self.parked, []
        self.refresh_stores(save=False)
        self._mirror()
        for data, meta, thumb in parked:
            self.add(data, meta, thumb)

    def park(self, data, meta, thumb):
        with self._lock:
            self.parked.append((data, meta, thumb))

    # ---- the index (encrypted list of what is inside)
    def _load_index(self, key=None):
        self._index_collections = []
        if not os.path.isfile(INDEX):
            return []
        with _reading(INDEX) as fh:
            data = b"".join(decrypt_stream(key or self._key, "index", fh)).rstrip(b"\0")
        d = json.loads(data.decode("utf-8"))
        self._index_collections = list(d.get("collections") or [])
        return d.get("entries", [])

    def _write_index(self, path, key, entries, collections=None):
        os.makedirs(DIR, exist_ok=True)
        cols = self.collections if collections is None else collections
        data = json.dumps({"entries": entries, "collections": cols}, ensure_ascii=False).encode("utf-8")
        with open(path, "wb") as fh:
            encrypt_stream(key, "index", _padded(iter([data]), len(data)), fh)
            fh.flush()
            os.fsync(fh.fileno())
        fix_times(path)

    def _save_index(self):
        self._write_index(INDEX + ".tmp", self._key, self.entries + self.offline)
        os.replace(INDEX + ".tmp", INDEX)
        fix_times(INDEX)
        fix_times(DIR)
        self._mirror()

    # ---- drives (Settings, Drives): vault files kept on a USB stick or another drive
    def _folder(self, sid):
        """Where the encrypted files of a place are: this PC (sid None) or a drive's .vault folder."""
        if not sid:
            return BLOBS
        from . import stores
        if not stores.root(sid):
            raise VaultError("That drive is not plugged in")
        return os.path.join(stores.vault_dir(sid), "files")

    def _blob_path(self, entry, blob):
        return os.path.join(self._folder(entry.get("store")), blob + ".bcv")

    def refresh_stores(self, save=True):
        """Drives plugged in or out: their vault files show or hide. Returns True when something changed."""
        from . import stores
        if not self.is_open():
            return False
        with self._lock:
            show, hide = [], []
            for e in self.entries + self.offline:
                sid = e.get("store")
                ok = not sid or (stores.root(sid) and os.path.isfile(os.path.join(stores.root(sid), ".vault", "files",
                                                                                  e["blob"] + ".bcv")))
                (show if ok else hide).append(e)
            changed = {x["id"] for x in hide} != {x["id"] for x in self.offline}
            show.sort(key=lambda e: e.get("added", ""))
            self.entries, self.offline = show, hide
        if changed:
            self._mirror()
        return changed

    def _mirror(self):
        """Each plugged-in drive with vault files keeps a copy of the (encrypted) header and of the list of what is
        on it, so the drive on its own still holds everything needed with the password - as a backup."""
        from . import stores
        if not self.is_open() or not os.path.isfile(HEADER):
            return
        for s in stores.online():
            mine = [e for e in self.entries if e.get("store") == s["id"]]
            if not mine:
                continue
            try:
                d = stores.vault_dir(s["id"])
                with open(HEADER, "rb") as fh:
                    head = fh.read()
                hp = os.path.join(d, "vault.json")
                old = open(hp, "rb").read() if os.path.isfile(hp) else None
                if old != head:
                    with open(hp + ".tmp", "wb") as fh:
                        fh.write(head)
                    os.replace(hp + ".tmp", hp)
                    fix_times(hp)
                ip = os.path.join(d, "index.bcv")
                self._write_index(ip + ".tmp", self._key, mine)
                os.replace(ip + ".tmp", ip)
                fix_times(ip)
            except (OSError, VaultError):
                pass

    def move(self, entries, sid, progress=None):
        """Moves the encrypted files of entries to a drive (sid) or back to this PC (None). Nothing is decrypted:
        the files are copied as they are, checked byte for byte, and the old ones shredded. Returns how many
        moved."""
        from . import shred
        self._need()
        dest = self._folder(sid)
        os.makedirs(dest, exist_ok=True)
        moved = 0
        for i, e in enumerate(entries):
            if (e.get("store") or None) == (sid or None):
                continue
            blobs = [b for b in (e.get("blob"), e.get("thumb_blob")) if b]
            olds, news = [], []
            try:
                for b in blobs:
                    src = self._blob_path(e, b)
                    dst = os.path.join(dest, b + ".bcv")
                    shutil.copyfile(src, dst + ".tmp")
                    if not _same(src, dst + ".tmp"):
                        raise VaultError("The copy came out different")
                    os.replace(dst + ".tmp", dst)
                    fix_times(dst)
                    olds.append(src)
                    news.append(dst)
            except (OSError, VaultError):
                for f in news + [os.path.join(dest, b + ".bcv.tmp") for b in blobs]:
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                continue
            with self._lock:
                e["store"] = sid or None
                if not sid:
                    e.pop("store", None)
                self._save_index()
            for f in olds:
                shred.shred_file(f)
            moved += 1
            if progress:
                progress(i + 1, len(entries))
        fix_times(dest)
        return moved

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

    def _put_blob(self, chunks, size, key=None, folder=None):
        blob = uuid.uuid4().hex
        folder = folder or BLOBS
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, blob + ".bcv")
        with open(path + ".tmp", "wb") as fh:
            encrypt_stream(key or self._key, blob, _padded(chunks, size), fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(path + ".tmp", path)
        fix_times(path)
        fix_times(folder)
        return blob

    def add(self, data, meta, thumb=None, store=None):
        """Puts bytes (a picture or video) into the vault with its details. Returns the new entry."""
        return self._add(iter([data]), meta, thumb, len(data), store)

    def add_file(self, path, meta, thumb=None, store=None):
        def chunks():
            with open(path, "rb") as fh:
                while True:
                    b = fh.read(CHUNK)
                    if not b:
                        return
                    yield b
        return self._add(chunks(), meta, thumb, os.path.getsize(path), store)

    def _add(self, chunks, meta, thumb, size, store=None):
        with self._lock:
            self._need()
            folder = self._folder(store)
            e = dict(meta)
            e.update(id=uuid.uuid4().hex[:12], vault=True, blob=self._put_blob(chunks, size, folder=folder), size=size,
                     added=time.strftime("%Y-%m-%d %H:%M:%S"), pad=1)
            if store:
                e["store"] = store
            e.setdefault("created", e["added"])
            e["thumb_blob"] = self._put_blob(iter([thumb]), len(thumb), folder=folder) if thumb else None
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
        with _reading(self._blob_path(entry, blob)) as fh:
            data = b"".join(decrypt_stream(key or self._key, blob, fh))
        n = self._real_len(entry, which)
        return data if n is None else data[:n]

    def export(self, entry, dest):
        """Decrypts an entry into a file (for taking it out of the vault)."""
        self._need()
        blob = entry["blob"]
        left = self._real_len(entry, "blob")
        with _reading(self._blob_path(entry, blob)) as fh, open(dest + ".part", "wb") as out:
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
            if self.offline:
                from . import stores
                names = sorted({stores.label(stores.get(e.get("store")) or {"id": ""}, False) for e in self.offline})
                raise VaultError(f"{len(self.offline)} vault files are on a drive that is not plugged in "
                                 f"({', '.join(names)}). Plug it in first.")
            new_key = secrets.token_bytes(32)
            entries, made, old = [], [], []
            total = len(self.entries)
            try:
                for i, e in enumerate(self.entries):
                    e2 = dict(e, pad=1)
                    data = self.read(e, "blob")
                    folder = self._folder(e.get("store"))
                    e2["blob"] = self._put_blob(iter([data]), len(data), new_key, folder)
                    made.append((folder, e2["blob"]))
                    if e.get("thumb_blob"):
                        th = self.read(e, "thumb_blob")
                        e2["thumb_blob"] = self._put_blob(iter([th]), len(th), new_key, folder)
                        e2["thumb_len"] = len(th)
                        made.append((folder, e2["thumb_blob"]))
                    old += [os.path.join(folder, b + ".bcv") for b in (e.get("blob"), e.get("thumb_blob")) if b]
                    entries.append(e2)
                    if progress:
                        progress(i + 1, total)
            except Exception:
                for folder, b in made:                  # nothing changed: the new copies go again
                    try:
                        os.remove(os.path.join(folder, b + ".bcv"))
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
        self._mirror()
        for f in old:
            shred.shred_file(f)
        return rk

    def get(self, eid):
        return next((e for e in self.entries if e["id"] == eid), None)

    def update(self, eid, **fields):
        with self._lock:
            e = self.get(eid)
            if e is not None:
                e.update(fields)
                self._save_index()

    def update_many(self, eids, **fields):
        with self._lock:
            ids = set(eids)
            for e in self.entries:
                if e["id"] in ids:
                    e.update(fields)
            self._save_index()

    def file_of(self, entry):
        """Where an entry's encrypted file is (for Show in folder)."""
        return self._blob_path(entry, entry["blob"])

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
                    try:
                        shred.shred_file(self._blob_path(e, b))
                    except VaultError:
                        pass
        return len(gone)

    # ---- collections (only while open; their names are as private as the rest)
    def collection(self, cid):
        return next((c for c in self.collections if c["id"] == cid), None)

    def add_collection(self, name, parent=None):
        from . import folders
        with self._lock:
            self._need()
            c = {"id": uuid.uuid4().hex[:10], "name": name.strip()[:80] or "Collection", "parent": parent}
            self.collections = folders.ordered(self.collections + [c])
            self._save_index()
            return c

    def set_collections(self, cols):
        with self._lock:
            self._need()
            self.collections = list(cols)
            self._save_index()

    def rename_collection(self, cid, name):
        with self._lock:
            c = self.collection(cid)
            if c and name.strip():
                c["name"] = name.strip()[:80]
                self._save_index()

    def remove_collection(self, cid):
        """The collection goes, everything that was in it stays in the vault."""
        with self._lock:
            self._need()
            from . import folders
            self.collections = [c for c in self.collections if c["id"] != cid]
            folders.promote_children(self.collections, cid)
            for e in self.entries + self.offline:
                if cid in (e.get("cols") or []):
                    e["cols"] = [x for x in e["cols"] if x != cid]
            self._save_index()

    def set_in_collection(self, eids, cid, on):
        with self._lock:
            self._need()
            ids = set(eids)
            for e in self.entries:
                if e["id"] in ids:
                    cols = [x for x in (e.get("cols") or []) if x != cid]
                    e["cols"] = cols + [cid] if on else cols
            self._save_index()

    def recent(self, kind=None):
        out = [e for e in self.entries if kind is None or e.get("kind") == kind]
        return sorted(out, key=lambda e: e.get("created", ""), reverse=True)


def _same(a, b):
    """Two files with the same bytes (read back from the drive, not from the cache of what was written)."""
    import hashlib
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    hs = []
    for p in (a, b):
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        hs.append(h.digest())
    return hs[0] == hs[1]


vault = Vault()
