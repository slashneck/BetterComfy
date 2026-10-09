"""The prompt helper: a small language model that writes and improves prompts. It runs on the processor (llama.cpp),
so the graphics card stays free for ComfyUI, and it only exists after it was set up in the app (an optional download
to a folder of your choice). It listens on 127.0.0.1 only, with a random key, and nothing it is given is logged."""
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import zipfile

from .config import BASE, VERSION, cfg

RUNTIME = {"name": "llama.cpp b11259 (CPU)", "file": "llama-b11259-bin-win-cpu-x64.zip",
           "url": "https://github.com/ggml-org/llama.cpp/releases/download/b11259/llama-b11259-bin-win-cpu-x64.zip",
           "size": 19164803, "sha256": "977c5ddcd24caaaaaf87a9cdcfa46972a1c8ccf2007a013a45c84e8bc31865d9"}

def _hf(repo, rev, file):
    return f"https://huggingface.co/{repo}/resolve/{rev}/{file}"


# role "write": writes and rewrites prompts (sentences, tags, video motion). role "tags": Danbooru tags only.
MODELS = [
    {"key": "free4b", "role": "write", "name": "Qwen3.5 4B uncensored", "size": 2707514688,
     "note": "Uncensored: writes whatever you ask for, explicit content included. Writes video motion, and sentence "
             "prompts when no tag model is set up (or when you pick it in Settings). Around 10 seconds per prompt.",
     "file": "Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf",
     "sha256": "423f10b6ec2d99c3378143d7cd3b80eb4887b3ed92103103ac59173b404f4f7c",
     "url": _hf("mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF", "4a5daa6fbefca5fe822dc65fcb95cc4576fa9720",
                "Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf")},
    {"key": "free2b", "role": "write", "name": "Qwen3.5 2B uncensored", "size": 1270809024,
     "note": "Uncensored and about twice as fast. Simpler prompts, good for older processors.",
     "file": "Huihui-Qwen3.5-2B-abliterated.Q4_K_M.gguf",
     "sha256": "aa25eea787afe56a097268f7ed3460cb623e1901d2e89cd2b654cabb42f80636",
     "url": _hf("mradermacher/Huihui-Qwen3.5-2B-abliterated-GGUF", "f36848fead3fdda244cf60195c46993d23183d4c",
                "Huihui-Qwen3.5-2B-abliterated.Q4_K_M.gguf")},
    {"key": "free9b", "role": "write", "name": "Qwen3.5 9B uncensored", "size": 5627045248,
     "note": "Uncensored, writes the best prompts. Slow on a processor: about half a minute per prompt.",
     "file": "Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf",
     "sha256": "ea1858ef4dc4b648b8dbb44612962a0333e945060dd0545ac0f28d7c4416e4b3",
     "url": _hf("mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF", "9f646d7eda193ddf2348134f3bff3d49eed7a2c6",
                "Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf")},
    {"key": "tipo", "role": "tags", "name": "TIPO v2.1", "size": 1072689600,
     "note": "Recommended. Made for picture prompts: writes Danbooru tags for tag models (Illustrious, Pony, Anima) and "
             "concrete sentences for the others, turns text into tags and back. Uncensored, learned from real captions "
             "and Danbooru. About one second per prompt. Free for personal use (Kohaku License).",
     "file": "TIPO-v2.1-1B-A200M-Q8_0.gguf",
     "sha256": "0847e9e2e667a009bc82ecf534b628425d515b0f74fb5c1dc0705568906c4773",
     "url": _hf("KBlueLeaf/TIPO-v2.1-1B-A200M", "f5a318524a4ab30cdbbf51816cf406170f454e65",
                "gguf/TIPO-v2.1-1B-A200M-Q8_0.gguf")},
    # the standard models of 1.1.0: kept so a download from then still works
    {"key": "2b", "role": "write", "name": "Qwen3.5 2B standard", "size": 1280835840, "old": True,
     "note": "The standard model from 1.1.0. Refuses adult content.",
     "file": "Qwen3.5-2B-Q4_K_M.gguf", "sha256": "aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223",
     "url": _hf("unsloth/Qwen3.5-2B-GGUF", "f6d5376be1edb4d416d56da11e5397a961aca8ae", "Qwen3.5-2B-Q4_K_M.gguf")},
    {"key": "0.8b", "role": "write", "name": "Qwen3.5 0.8B standard", "size": 532517120, "old": True,
     "note": "The smallest standard model from 1.1.0. Refuses adult content.",
     "file": "Qwen3.5-0.8B-Q4_K_M.gguf", "sha256": "bd258782e35f7f458f8aced1adc053e6e92e89bc735ba3be89d38a06121dc517",
     "url": _hf("unsloth/Qwen3.5-0.8B-GGUF", "6ab461498e2023f6e3c1baea90a8f0fe38ab64d0", "Qwen3.5-0.8B-Q4_K_M.gguf")},
    {"key": "4b", "role": "write", "name": "Qwen3.5 4B standard", "size": 2740937888, "old": True,
     "note": "The large standard model from 1.1.0. Refuses adult content.",
     "file": "Qwen3.5-4B-Q4_K_M.gguf", "sha256": "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
     "url": _hf("unsloth/Qwen3.5-4B-GGUF", "e87f176479d0855a907a41277aca2f8ee7a09523", "Qwen3.5-4B-Q4_K_M.gguf")},
]
ROLE_KEY = {"write": "helper_model", "tags": "helper_tag_model"}

_NOWIN = 0x08000000                 # CREATE_NO_WINDOW
_LOW = 0x00004000                   # BELOW_NORMAL_PRIORITY_CLASS: generating and the app always come first


class Cancelled(Exception):
    pass


def default_dir():
    return os.path.join(BASE, "prompt-helper")


def folder():
    return cfg.get("helper_dir") or default_dir()


def server_exe(base=None):
    """llama-server.exe inside the runtime folder (the zip may keep it in a sub folder)."""
    root = os.path.join(base or folder(), "runtime")
    for dirpath, _dirs, files in os.walk(root):
        if "llama-server.exe" in files:
            return os.path.join(dirpath, "llama-server.exe")
    return None


def model_path(role="write"):
    p = cfg.get(ROLE_KEY[role]) or ""
    return p if p and os.path.isfile(p) else None


def ready(role=None):
    """Set up: the runtime and a model for that role (or for any role)."""
    if not server_exe():
        return False
    return bool(model_path(role)) if role else bool(model_path("write") or model_path("tags"))


def entry(key):
    return next((m for m in MODELS if m["key"] == key), None)


def local_path(m, base=None):
    return os.path.join(base or folder(), "models", m["file"])


def installed(m, base=None):
    return _model_ok(local_path(m, base), m)


def model_name(path=None, role="write"):
    path = path or model_path(role) or ""
    base = os.path.basename(path)
    for m in MODELS:
        if m["file"] == base:
            return m["name"]
    return os.path.splitext(base)[0]


# ------------------------------------------------------------------------------------------------ downloading

def download(url, dest, size, sha256, progress=None, cancel=None):
    """Downloads `url` to `dest` (resuming a .part from an earlier try) and checks its SHA-256.
    progress(done_bytes, total_bytes) is called now and then; cancel is a threading.Event."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    h = hashlib.sha256()
    done = 0
    if os.path.isfile(part):
        with open(part, "rb") as fh:
            while True:
                b = fh.read(1 << 20)
                if not b:
                    break
                h.update(b)
                done += len(b)
        if done > size:
            os.remove(part)
            h, done = hashlib.sha256(), 0
    if done < size:
        req = urllib.request.Request(url, headers={"User-Agent": f"BetterComfy/{VERSION}"})
        if done:
            req.add_header("Range", f"bytes={done}-")
        with urllib.request.urlopen(req, timeout=60) as r:
            if done and r.status != 206:               # the server started over
                h, done = hashlib.sha256(), 0
                mode = "wb"
            else:
                mode = "ab" if done else "wb"
            last = 0.0
            with open(part, mode) as out:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    b = r.read(1 << 20)
                    if not b:
                        break
                    out.write(b)
                    h.update(b)
                    done += len(b)
                    if progress and time.time() - last > 0.2:
                        last = time.time()
                        progress(done, size)
    if progress:
        progress(done, size)
    if done != size or h.hexdigest() != sha256:
        os.remove(part)
        raise RuntimeError("The download came out damaged (its checksum does not match). Please try again.")
    os.replace(part, dest)
    return dest


def install(model, base, progress=None, cancel=None):
    """The runtime (if missing) and the model into `base`. model: an entry of MODELS, or the path of a .gguf file
    already on this PC (then nothing but the runtime is downloaded). Returns the model path."""
    os.makedirs(base, exist_ok=True)
    need_model = isinstance(model, dict) and not _model_ok(os.path.join(base, "models", model["file"]), model)
    total = (0 if server_exe(base) else RUNTIME["size"]) + (model["size"] if need_model else 0)
    offset = [0]

    def prog(d, _t):
        if progress:
            progress(offset[0] + d, total)
    if not server_exe(base):
        z = os.path.join(base, RUNTIME["file"])
        download(RUNTIME["url"], z, RUNTIME["size"], RUNTIME["sha256"], prog, cancel)
        rt = os.path.join(base, "runtime")
        shutil.rmtree(rt, ignore_errors=True)
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():                    # never outside the folder
                if os.path.isabs(n) or ".." in n.replace("\\", "/").split("/"):
                    raise RuntimeError("The runtime archive looks wrong.")
            zf.extractall(rt)
        os.remove(z)
        if not server_exe(base):
            raise RuntimeError("The runtime archive did not contain llama-server.exe.")
        offset[0] += RUNTIME["size"]
    if isinstance(model, dict):
        dest = os.path.join(base, "models", model["file"])
        if need_model:
            download(model["url"], dest, model["size"], model["sha256"], prog, cancel)
        return dest
    return model


def _model_ok(path, m):
    return os.path.isfile(path) and os.path.getsize(path) == m["size"]


def remove(base=None):
    """Deletes what the setup put into the folder (the runtime and the models it downloaded), nothing else."""
    base = base or folder()
    stop_all()
    shutil.rmtree(os.path.join(base, "runtime"), ignore_errors=True)
    for m in MODELS:
        for p in (os.path.join(base, "models", m["file"]), os.path.join(base, "models", m["file"] + ".part")):
            if os.path.isfile(p):
                os.remove(p)
    for p in (os.path.join(base, "models"), base):
        try:
            os.rmdir(p)                                # only when empty
        except OSError:
            pass
    for key in ROLE_KEY.values():
        if (cfg.get(key) or "").startswith(os.path.join(base, "models")):
            cfg.set(key, "")


def remove_model(m, base=None):
    """Deletes one downloaded model (and stops it if it runs)."""
    p = local_path(m, base)
    for h in (helper, tagger):
        if h.model and os.path.normcase(h.model) == os.path.normcase(p):
            h.stop()
    for f in (p, p + ".part"):
        if os.path.isfile(f):
            os.remove(f)
    key = ROLE_KEY[m["role"]]
    if os.path.normcase(cfg.get(key) or "") == os.path.normcase(p):
        cfg.set(key, "")


# ------------------------------------------------------------------------------------------------ the server

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # 127.0.0.1, never through a proxy


def _kill_with_us(proc):
    """Puts the server into a job that Windows ends together with this app, so a crash leaves nothing running."""
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        class BASIC(ctypes.Structure):
            _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                        ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                        ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in "abcdef"]

        class EXT(ctypes.Structure):
            _fields_ = [("basic", BASIC), ("io", IO), ("p1", ctypes.c_size_t), ("p2", ctypes.c_size_t),
                        ("p3", ctypes.c_size_t), ("p4", ctypes.c_size_t)]
        job = k.CreateJobObjectW(None, None)
        info = EXT()
        info.basic.LimitFlags = 0x2000                 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        k.AssignProcessToJobObject(job, int(proc._handle))
        return job                                     # kept open for as long as the app runs
    except Exception:
        return None


class Helper:
    def __init__(self, role):
        self.role = role
        self.proc = None
        self.port = None
        self.key = None
        self.model = None
        self.last_used = 0.0
        self.last_finish = None
        self._job = None
        self._lock = threading.Lock()

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def threads(self):
        n = int(cfg.get("helper_threads", 0) or 0)
        return n if n > 0 else max(2, (os.cpu_count() or 4) // 2)

    def start(self, wait=180.0):
        """Starts the server (if it is not running with the chosen model) and waits until the model is loaded."""
        with self._lock:
            model, exe = model_path(self.role), server_exe()
            if not model or not exe:
                raise RuntimeError("The prompt helper is not set up yet." if self.role == "write" else
                                   "No tag model is set up yet.")
            if self.running() and self.model == model:
                return
            self.stop()
            self.port, self.key, self.model = _free_port(), secrets.token_hex(24), model
            args = [exe, "-m", model, "--host", "127.0.0.1", "--port", str(self.port), "--api-key", self.key,
                    "-c", "4096", "-np", "1", "-t", str(self.threads()), "-ngl", "0", "--jinja",
                    "--reasoning-budget", "0", "--no-webui", "--no-slots", "--log-disable"]
            self.proc = subprocess.Popen(args, cwd=os.path.dirname(exe), stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=_NOWIN | _LOW)
            self._job = _kill_with_us(self.proc)
            t0 = time.time()
            while time.time() - t0 < wait:
                if self.proc.poll() is not None:
                    self.proc = None
                    raise RuntimeError("The prompt helper could not start. The model file may be damaged or "
                                       "not supported.")
                try:
                    with _NO_PROXY.open(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                        if r.status == 200:
                            self.last_used = time.time()
                            return
                except Exception:
                    pass
                time.sleep(0.25)
            self.stop()
            raise RuntimeError("The prompt helper took too long to start.")

    def stop(self):
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except Exception:
                p.kill()

    def idle_stop(self):
        mins = int(cfg.get("helper_free_min", 5) or 0)
        if self.running() and mins > 0 and time.time() - self.last_used > mins * 60:
            self.stop()

    def chat(self, messages, on_text=None, cancel=None, **opts):
        """Streams an answer; on_text(text_so_far) is called as it grows. Returns the whole answer."""
        self.start()
        self.last_used = time.time()
        body = {"messages": messages, "stream": True, "temperature": 0.6, "top_p": 0.9, "max_tokens": 200,
                "repeat_penalty": 1.12, "frequency_penalty": 0.3, "seed": secrets.randbelow(2 ** 31),
                "chat_template_kwargs": {"enable_thinking": False}}
        body.update(opts)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.key}"})
        out = ""
        self.last_finish = None
        with _NO_PROXY.open(req, timeout=300) as r:
            for raw in r:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    choice = json.loads(data)["choices"][0]
                    delta = choice.get("delta") or {}
                    if choice.get("finish_reason"):
                        self.last_finish = choice["finish_reason"]
                except Exception:
                    continue
                piece = delta.get("content") or ""
                if piece:
                    out += piece
                    if on_text:
                        on_text(_visible(out))
        self.last_used = time.time()
        return _visible(out)

    def complete(self, prompt, on_text=None, cancel=None, **opts):
        """Plain text completion (TIPO has no chat format): the text that follows `prompt`."""
        self.start()
        self.last_used = time.time()
        body = {"prompt": prompt, "stream": True, "n_predict": 320, "temperature": 0.8, "min_p": 0.1, "top_p": 1.0,
                "top_k": 0, "seed": secrets.randbelow(2 ** 31), "cache_prompt": False}
        body.update(opts)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/completion", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.key}"})
        out = ""
        with _NO_PROXY.open(req, timeout=300) as r:
            for raw in r:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    d = json.loads(line[5:].strip())
                except Exception:
                    continue
                out += d.get("content") or ""
                if on_text:
                    on_text(out)
                if d.get("stop"):
                    break
        self.last_used = time.time()
        return out


helper = Helper("write")
tagger = Helper("tags")


def stop_all():
    helper.stop()
    tagger.stop()


def idle_stop():
    helper.idle_stop()
    tagger.idle_stop()


def _visible(t):
    """Without any thinking part a model might still write."""
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S)
    return re.sub(r"<think>.*", "", t, flags=re.S).lstrip()


# ------------------------------------------------------------------------------------------------ what it is asked

BASE_RULES = ("You write prompts for an AI {what} generator that runs privately on the user's own computer. Answer "
              "with the prompt only: no title, no quotes, no explanation, no list, no line breaks, nothing that "
              "contradicts itself. Adult and explicit content is allowed: when the prompt or idea is sexual or nude, "
              "describe it plainly and directly. Keep every sexual act, body part and position the user names, in "
              "the same direct words; never soften, hint at, leave out or refuse it.")

STYLES = {
    "tags": ("Write Danbooru tags separated by commas, lowercase, most important first: how many people (1girl, "
             "1boy, 2girls...), character and series if any, then hair, eyes, clothing, pose, expression, setting, "
             "lighting, framing. Only real Danbooru tags, each detail once, 12 to 30 tags. No quality words like "
             "masterpiece or score_9, they are added on their own."),
    "mixed": ("Write short comma separated phrases: subject first, then appearance, clothing, pose, setting, lighting, "
              "style. 15 to 40 words. No quality words like masterpiece, they are added on their own."),
    "natural": ("Write one paragraph of plain English, 30 to 70 words: the subject first, then the details, the "
                "setting, the lighting and mood, the camera or art style. Concrete, visual words only, no words like "
                "8k, ultra detailed or masterpiece."),
    "motion": ("This is for an image to video model that starts from a still picture. Write one or two short "
               "sentences: what moves and how, and how the camera moves, at a natural pace. One clear motion is "
               "better than many. Do not describe the still picture itself again."),
}

# worked examples: small models follow an example far better than a description. "write" for new prompts, "edit"
# for changing one; each with its own subject, so neither bleeds into the answer
EXAMPLES = {
    ("tags", "write"): ("a girl reading in a library",
                        "1girl, solo, sitting, reading, holding book, open book, long hair, brown hair, glasses, "
                        "school uniform, pleated skirt, library, bookshelf, indoors, window, sunlight, from side"),
    ("tags", "edit"): ("1girl, red dress, beach",
                       "1girl, solo, red dress, sundress, standing, beach, ocean, sand, blue sky, sunlight, smile, "
                       "looking at viewer, wind, floating hair"),
    ("mixed", "write"): ("a girl reading in a library",
                         "a girl sitting in an old library, long brown hair, glasses, school uniform, reading an open "
                         "book, tall bookshelves, warm sunlight through the window, calm mood"),
    ("mixed", "edit"): ("girl, red dress, beach",
                        "a girl in a flowing red dress standing on a sunny beach, waves behind her, wind in her hair, "
                        "bright blue sky, warm light"),
    ("natural", "write"): ("man reading at a kitchen table",
                           "A middle-aged man in a grey sweater sits at a wooden kitchen table reading a paperback, a "
                           "mug of coffee beside him. Soft morning light from the window, warm muted colors, candid "
                           "photo."),
    ("natural", "edit"): ("a cat on a windowsill",
                          "A fluffy orange cat curled up on a sunny windowsill, looking out at a quiet street, soft "
                          "morning light, warm colors, cozy mood, shallow depth of field."),
    ("motion", "write"): ("a woman in a red coat on a bridge",
                          "She turns her head toward the camera as her coat moves gently in the wind, while the camera "
                          "slowly pushes in."),
    ("motion", "edit"): ("woman on bridge, wind",
                         "Wind lifts her hair and coat as she slowly turns toward the camera, which drifts to the "
                         "side."),
}

TASKS = {
    "improve": "Improve this prompt. Keep everything it says, including who and what is in it. Make unclear parts "
               "clear and add a little useful detail:\n{text}",
    "detail": "Describe what is already in this prompt in more visual detail (colors, materials, light, the "
              "surroundings). Do not add new objects, people or events:\n{text}",
    "shorter": "Shorten this prompt to at most {limit} words. Keep only the most important parts:\n{text}",
    "idea": "Write a full prompt for this idea. Keep its place and what happens, add no other places, people or "
            "events:\n{text}",
    "convert": "Rewrite this prompt in the style described above, keep its content:\n{text}",
    "surprise": "Write a full prompt for this idea:\n{text}",
    "motion": "Write the motion prompt for this. What the picture shows or what should happen:\n{text}",
}
KIND = {"improve": "edit", "detail": "edit", "shorter": "edit"}
TEMPS = {"surprise": 0.8, "idea": 0.7}
LIMITS = {"tags": 130, "mixed": 120, "natural": 220, "motion": 100}

_PROTECT = re.compile(r"<lora:[^>]+>|\{[^{}]*\|[^{}]*\}[^,<>(){}\n]*|\([^()]+:\s*-?\d+(?:\.\d+)?\)")
# words the model likes to add that mean nothing to the image model, or are added by the app on its own
_FILLER = {"composition", "lighting", "expression", "setting", "atmosphere", "pose", "body", "hair", "eyes",
           "clothing", "framing", "character", "series", "background", "style", "mood", "details", "detail"}
_QUALITY = re.compile(r"^\(?(masterpiece|best quality|high quality|highest quality|quality|amazing quality|very "
                      r"aesthetic|absurdres|highres|ultra detailed|extremely detailed|8k|4k|hdr|score_\d(_up)?)"
                      r"(:[\d.]+)?\)?$", re.I)


def protected(text):
    """What the helper must never change: LoRA tags, (word:1.2) weights and {a|b} choices."""
    return [m.strip() for m in _PROTECT.findall(text or "")]


_REFUSAL = re.compile(r"(?i)\b(i can(?:not|'t)|i'm (?:sorry|unable)|i am (?:sorry|unable)|as an ai|i won't|"
                      r"i will not|not able to (?:help|create|generate|write)|against (?:my|the) (?:guidelines|policy)|"
                      r"inappropriate|explicit content is not)")


def refused(t):
    return bool(_REFUSAL.search((t or "")[:240]))


def ask(task, text, style, on_text=None, cancel=None, what="image"):
    """One job for the helper. Tag work goes to the tag model (TIPO) when one is set up, everything else to the
    writing model. Returns the cleaned up prompt."""
    tipo = bool(model_path("tags"))
    to_tags = (style == "tags" and task in ("improve", "detail", "idea", "surprise")) or \
        (task == "convert" and style == "tags" and (text or "").strip() and not _looks_tags(text))
    if task == "shorter" and style == "tags":
        return shorter_tags(text)
    if tipo and to_tags:
        return _ask_tipo(task, text, on_text, cancel)
    if tipo and style in ("natural", "mixed") and task in ("improve", "detail", "idea", "surprise", "convert") and \
            (cfg.get("sentences_by", "auto") == "auto" or not model_path("write")):
        return _ask_tipo(task, text, on_text, cancel, natural=True)
    if not model_path("write"):
        raise RuntimeError("This needs a writing model. Set one up in Settings, Prompt helper.")
    out = _ask_writer(task, text, style, on_text, cancel, what)
    if style == "tags":
        out = merge_tags(out, text if task in ("convert", "idea") else "")
        out = with_protected(out, protected(text), "tags")     # the tidy up must not lose LoRAs or weights
    return out


def _looks_tags(text):
    """A tag list rather than sentences (LoRAs, weights and choices left aside)."""
    from . import tags
    parts = [p.strip() for p in _PROTECT.sub("", text or "").split(",") if p.strip()]
    if not parts:
        return False
    if len(parts) == 1:
        known, alias, _c = tags.lookup()
        k = tags.norm(parts[0])
        return k in known or k in alias
    return sum(len(p.split()) <= 4 for p in parts) >= len(parts) * 0.8


def _ask_writer(task, text, style, on_text=None, cancel=None, what="image"):
    text = (text or "").strip()
    keep = protected(text)
    if task == "surprise":
        # first a short idea in plain words (free and varied), then the prompt for it in the right style
        text = helper.chat([{"role": "system", "content": "You invent ideas for pictures."},
                            {"role": "user", "content": "One original, striking idea for a picture in one short "
                                                        "sentence: a random subject, setting and mood. Only the "
                                                        "sentence."}],
                           None, cancel, temperature=1.0, max_tokens=50).strip().strip('"')
    system = BASE_RULES.format(what="video" if style == "motion" else what) + " " + STYLES[style]
    ex_in, ex_out = EXAMPLES[(style, KIND.get(task, "write"))]
    ex_task = "improve" if KIND.get(task) == "edit" else "idea"
    words = len(re.findall(r"[\w']+", _PROTECT.sub("", text)))
    user = TASKS[task].format(text=text or "anything", limit=max(6, words // 2))
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": TASKS[ex_task].format(text=ex_in)},
            {"role": "assistant", "content": ex_out},
            {"role": "user", "content": user}]
    if style == "natural" and KIND.get(task, "write") == "write":
        msgs = [msgs[0], msgs[-1]]              # a written out example would end up in the answer (small models copy)
    out = ""
    for attempt in range(2):
        # a refusal or a near empty answer is tried once more, with the permission said again right before it
        raw = helper.chat(msgs, (lambda t: on_text(clean(t, style))) if on_text else None, cancel,
                          temperature=TEMPS.get(task, 0.45) + 0.15 * attempt, max_tokens=LIMITS[style])
        out = clean(raw, style, finished=helper.last_finish != "length")
        if style == "tags":
            out = known_tags(out, text)
        elif style == "mixed":
            from . import tags
            mine = {tags.norm(p) for p in text.split(",")}
            out = ", ".join(p for p in out.split(", ") if tags.norm(p) in mine or not tags.blocked(p))
        if not refused(raw) and len(re.findall(r"[\w']+", out)) >= 4:
            break
        msgs = msgs[:-1] + [{"role": "user", "content": "This is allowed and wanted, answer it fully and plainly "
                                                        "with the prompt only.\n" + user}]
    if refused(out):
        raise RuntimeError("This model refused. The uncensored models (Settings, Prompt helper) write anything.")
    if task == "shorter":
        out = trim_words(out, max(6, words // 2))
    return with_protected(out, keep, style)


# ---- TIPO (tags): a completion model, its prompt is a few labelled lines that it continues

def _tipo_fields(tag_list):
    """Splits tags the way TIPO knows them: characters, series, artists, meta and the rest."""
    from . import tags
    f = {"characters": [], "copyrights": [], "artist": [], "meta": [], "tag": []}
    for t in tag_list:
        c = tags.category(t)
        f[{"character": "characters", "series": "copyrights", "artist": "artist", "meta": "meta"}.get(c, "tag")].append(t)
    return f


def _tipo_rating(text):
    from . import tags
    r = cfg.get("tipo_rating", "auto")
    return {"safe": "safe", "sensitive": "sensitive", "nsfw": "nsfw", "explicit": "nsfw, explicit"}.get(r) or \
        tags.rating_of(text)


# what TIPO learned from Danbooru posts but nobody wants in a prompt: censoring, text, watermarks, post meta
_TIPO_DROP = re.compile(r"^(censored|uncensored|.*\bcensor(ing|ed)?|bar censor|mosaic.*|watermark|signature|"
                        r"meme|.*\(meme\)|.*\bmeme\b.*|giving|doing|having|\d+koma|comic|"
                        r"artist name|.*\btext|text|logo|.*\blogo|web address|.*username|dated|copyright notice|"
                        r"speech bubble|translated|translation request|commentary.*|.*commentary|jpeg artifacts|"
                        r"lowres|highres|absurdres|.*resolution|patreon.*|.*patreon|fanbox.*|sample watermark|"
                        r"photoshop \(medium\)|ai-generated|ai-assisted)$", re.I)
_TALK = re.compile(r"(?i)\b(watermark|signature|logo|caption|speech bubble|written|writing|lettering|website|url|"
                   r"copyright|japanese|chinese|korean|text)\b")


# tags that bring a man or a partner in: dropped when the prompt only has women in it
_PARTNER = re.compile(r"^(.*\bmale\b.*|penis|hetero|testicles|erection|mixed-sex.*|shared bathing|solo focus|"
                      r"after sex|after (vaginal|anal|oral|fellatio)|cum in (pussy|ass|mouth)|creampie|cumdrip|"
                      r"sex|sex from behind|vaginal|anal|fellatio|paizuri|handjob|doggystyle|missionary|"
                      r"cowgirl position|.*\bboys?\b.*|netorare|faceless.*|interracial|pov.*)$", re.I)


# words and acts that bring a man into a prompt that only names a woman
_NEEDS_MAN = re.compile(r"\b(blow ?jobs?|fellatio|handjobs?|paizuri|titjob|sex|fuck\w*|riding (a|the|his) (man|guy|cock|"
                        r"dick)|cowgirl|doggy\w*|missionary|penis|cock|dick|man|men|guy|boyfriend|husband|couple|"
                        r"hetero)\b")


def _people_ok(tag_list, people, mine=()):
    """Keeps the people of the prompt: TIPO's own other counts go, and with only women in the prompt nothing that
    brings a man or a partner in (unless the prompt itself says it)."""
    from . import tags
    if not people:
        return tag_list
    want = {tags.norm(p) for p in people}
    own = {tags.norm(t) for t in mine}
    women_only = not any("boy" in w or "other" in w for w in want)
    out = []
    for t in tag_list:
        k = tags.norm(t)
        if k in own:
            out.append(t)
            continue
        if tags.PEOPLE.match(k) and k not in want and not (k == "solo" and len(want) == 1):
            continue
        if women_only and _PARTNER.match(k) and "female" not in k:
            continue
        out.append(t)
    return out


def _no_talk(text):
    """Without sentences about text, watermarks or logos in the picture (TIPO learned them from real captions)."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    keep = [p for p in parts if not _TALK.search(p)]
    return " ".join(keep or parts[:1])


def _ask_tipo(task, text, on_text=None, cancel=None, natural=False):
    from . import tags
    text = (text or "").strip()
    keep = protected(text)
    plain = _PROTECT.sub("", text)
    mine = [p.strip() for p in plain.split(",") if p.strip()] if _looks_tags(plain) else []
    f = _tipo_fields([t.replace("\\(", "(").replace("\\)", ")") for t in mine])
    head = "".join(f"{k}: {', '.join(f[k])}\n" for k in ("meta",) if f[k])
    head += f"rating: {_tipo_rating(text)}\n"
    head += "".join(f"{k}: {', '.join(f[k])}\n" for k in ("artist", "characters", "copyrights") if f[k])
    head += "quality: masterpiece, newest\n"
    if natural or (task == "convert" and mine):
        # sentences: first tags that keep to the prompt, then the text from the words and those tags (TIPO writes
        # far more faithful sentences from tags than from loose words alone)
        if mine:
            prompt = head + f"target: <|long|> <|tag_to_long|>\ntag: {', '.join(f['tag'])}\nlong:"
        else:
            if on_text:
                on_text("…")
            tag_line = _ask_tipo("idea", plain, None, cancel)
            prompt = head + (f"target: <|long|> <|short_to_tag_to_long|>\nshort: {plain}\ntag: {tag_line}\nlong:")
        raw = tagger.complete(prompt, (lambda t: on_text(t.split("\n")[0].strip())) if on_text else None, cancel,
                              n_predict=260, temperature=0.65, stop=["\n"])
        out = _no_talk(raw.split("\n")[0].strip())
        if _tipo_rating(text + " " + out) != "safe":
            # Danbooru's "1girl" only means one female character; in adult prompts the sentence says woman
            out = re.sub(r"\b(?:young |little )?girl(s?)\b", lambda m: "women" if m.group(1) else "woman", out)
        return with_protected(out, keep, "natural")
    people = [t for t in (mine or tags.extract(plain)) if tags.PEOPLE.match(tags.norm(t)) and tags.norm(t) != "solo"]
    if people and not any("boy" in tags.norm(p) or "other" in tags.norm(p) for p in people) and \
            _NEEDS_MAN.search(" " + plain.lower() + " "):
        people.append("1boy")                   # the act or the words bring a man in
    if mine or not plain:
        # grow the tag list: it is handed over unfinished and TIPO goes on writing it
        length = {"detail": "<|long|>", "surprise": "<|long|>"}.get(task, "<|short|>")
        given = ", ".join(f["tag"])
        prompt = head + f"target: {length}\ntag: {given}" + ("," if given else "")
        lead = [t for t in mine]
    else:
        # words to tags: the text as it is, then an empty tag line for TIPO to fill
        short = len(plain.split()) <= 25
        size = "<|long|>" if task in ("detail", "convert") else "<|short|>"
        prompt = head + (f"target: {size} <|short_to_tag|>\nshort: {plain}\ntag:" if short else
                         f"target: {size} <|long_to_tag|>\nlong: {plain}\ntag:")
        lead = [t for t in tags.extract(plain) if not _TIPO_DROP.match(t)]
        if "1boy" in people and "1boy" not in lead:
            lead.insert(1 if lead and tags.PEOPLE.match(tags.norm(lead[0])) else 0, "1boy")
    shown = []

    def live(t):
        shown[:] = [x.strip() for x in t.split("\n")[0].split(",") if x.strip()]
        on_text(", ".join(lead + shown))
    raw = tagger.complete(prompt, live if on_text else None, cancel, n_predict=220,
                          temperature=0.9 if task == "surprise" else 0.65, stop=["\n"])
    new = [x.strip() for x in raw.split("\n")[0].split(",") if x.strip()]
    new = _people_ok([t for t in new if not _TIPO_DROP.match(t.strip()) or tags.norm(t) in
                      {tags.norm(x) for x in mine}], people, lead)
    return with_protected(merge_tags(", ".join(lead + new), ""), keep, "tags")


def merge_tags(answer, source_text="", cap=45):
    """Tidies a tag answer, puts the people count first, and adds every real tag found in `source_text` that the
    model left out (so turning text into tags never comes back half done)."""
    from . import tags
    got = [t.strip() for t in known_tags(answer, "", cap=200).split(",") if t.strip()] if answer else []
    if source_text:
        have = {tags.norm(t) for t in got}
        got += [t for t in tags.extract(source_text) if tags.norm(t) not in have]
    people = [t for t in got if tags.PEOPLE.match(tags.norm(t))]
    rest = [t for t in got if t not in people]
    seen, out = set(), []
    for t in people[:2] + rest:
        k = tags.norm(t)
        if k not in seen:
            seen.add(k)
            out.append(t)
    return ", ".join(out[:cap])


def shorter_tags(text):
    """Shorter without any model: the most important half of the tags stays (people, characters, the most used)."""
    from . import tags
    keep = protected(text)
    parts = [p.strip() for p in _PROTECT.sub("", text or "").split(",") if p.strip()]
    out = tags.trim(parts, max(4, (len(parts) + 1) // 2))
    return with_protected(", ".join(out), keep, "tags")


_DANGLING = {"a", "an", "the", "and", "or", "with", "in", "on", "at", "of", "to", "by", "for", "from", "into",
             "her", "his", "their", "its", "while", "as"}


def trim_words(t, limit):
    """At most about `limit` words (small models overshoot), cut where a sentence or a comma ends and never on a
    word like "on" or "the"."""
    if len(t.split()) <= limit * 1.6:
        return t
    cut = " ".join(t.split()[:limit])
    for mark in (".", ","):
        k = cut.rfind(mark)
        if k > len(cut) * 0.5:
            return cut[:k + 1].rstrip(",").rstrip()
    words = cut.rstrip(",").split()
    while words and words[-1].lower() in _DANGLING:
        words.pop()
    return " ".join(words)


def clean(t, style, finished=True):
    t = (t or "").strip().replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    t = re.sub(r"^(?:\*\*)?(?:prompt|tags|motion)(?:\*\*)?\s*:\s*", "", t, flags=re.I)
    t = t.strip().strip('"').strip("'").strip("`").strip()
    if style in ("tags", "mixed"):
        parts = [p.strip() for p in re.split(r"[,\n]+", t) if p.strip()]
        if style == "tags":
            parts = [re.sub(r"(?<=[a-z0-9])_(?=[a-z0-9(])", " ", p) for p in parts]
        seen, out = set(), []
        for p in parts:
            k = p.lower()
            if k not in seen:
                seen.add(k)
                out.append(p.rstrip("."))
        return ", ".join(out)
    t = re.sub(r"\s*\n+\s*", " ", t)
    if not finished and not t.endswith((".", "!", "?")) and t.count(". ") >= 1:
        t = t[:t.rfind(". ") + 1]                      # cut off at the length limit: the unfinished sentence goes
    return t


def known_tags(t, original="", cap=35):
    """Tidies a tag answer: known tags (and their aliases) in Danbooru's spelling, short phrases kept, long sentences,
    filler words and quality words (the app adds those itself) left out."""
    from . import tags
    known, alias, cats = tags.lookup()
    mine = {p.strip().lower() for p in re.split(r"[,\n]+", original or "") if p.strip()}
    out, seen = [], set()
    for p in [x.strip() for x in t.split(",") if x.strip()]:
        k = p.lower().replace("\\(", "(").replace("\\)", ")")
        if k in mine:                           # the user's own (weights included) stay as they are
            tag = p
        elif tags.blocked(k, exact=cats.get(tags.norm(alias.get(k, k)), "general") != "general"):
            continue                            # the tag blacklist (Settings): never added by the helper
        elif _QUALITY.match(k) or k in _FILLER:
            continue
        elif k in known:
            tag = tags.for_prompt(known[k])
        elif k in alias:
            tag = tags.for_prompt(alias[k])
        elif 2 <= len(k.split()) <= 4 and re.fullmatch(r"[a-z0-9 '\-]+", k):     # a short phrase
            tag = p
        else:
            continue
        if tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return ", ".join(out[:cap])


def with_protected(out, keep, style):
    """Puts back any LoRA tag, weight or choice the model left out."""
    missing = [k for k in keep if k not in out]
    if not missing:
        return out
    lora = [k for k in missing if k.startswith("<lora:")]
    rest = [k for k in missing if k not in lora]
    if rest:
        out = ", ".join(rest + ([out] if out else []))
    if lora:
        out = (out + ", " if out else "") + ", ".join(lora)
    return out


def style_for(family, video=False):
    if video:
        return "motion"
    if family in ("illustrious", "pony", "anima"):
        return "tags"
    if family == "sd15":
        return "mixed"
    return "natural"
