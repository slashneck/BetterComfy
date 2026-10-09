"""Pictures and videos on disk: decoding ComfyUI's frames, loop finishing (seamless / ping-pong / crossfade), writing
MP4 / WebM / GIF, PNGs with their settings inside, thumbnails."""
import io
import json
import os

import numpy as np
from PIL import Image, PngImagePlugin

VIDEO_EXT = (".mp4", ".webm", ".gif", ".mov", ".mkv")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def decode(b):
    """PNG / JPEG bytes -> RGB uint8 array."""
    im = Image.open(io.BytesIO(b))
    return np.asarray(im.convert("RGB"))


def read_image(path):
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def png_bytes_of(path_or_arr):
    if isinstance(path_or_arr, np.ndarray):
        buf = io.BytesIO()
        Image.fromarray(path_or_arr).save(buf, "PNG")
        return buf.getvalue()
    with Image.open(path_or_arr) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "PNG")
        return buf.getvalue()


def image_size(path):
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


# ------------------------------------------------------------------------------------------------ loops

def drop_duplicate_end(frames):
    """A first+last-frame loop ends on its first picture: without the last one it repeats without a stutter."""
    return frames[:-1] if len(frames) > 2 else frames


def pingpong(frames):
    return frames + frames[-2:0:-1] if len(frames) > 2 else frames


def crossfade(frames, k):
    """Blend the last k frames into the first k: the clip wraps around smoothly. Returns len - k frames."""
    n = len(frames)
    k = int(max(0, min(k, n // 3)))
    if k < 1:
        return frames
    out = list(frames[:n - k])
    tail = frames[n - k:]
    for i in range(k):
        t = (i + 1) / (k + 1)               # 0 -> mostly tail, 1 -> mostly head
        a = tail[i].astype(np.float32)
        b = frames[i].astype(np.float32)
        out[i] = np.clip(a * (1 - t) + b * t + 0.5, 0, 255).astype(np.uint8)
    return out


def seam_blend(frames, k):
    """For loops that already meet: soften the last k frames toward the first one (hides a small jump)."""
    n = len(frames)
    k = int(max(0, min(k, n // 4)))
    if k < 1:
        return frames
    out = list(frames)
    first = frames[0].astype(np.float32)
    for i in range(k):
        idx = n - k + i
        t = (i + 1) / (k + 1) * 0.6
        out[idx] = np.clip(frames[idx].astype(np.float32) * (1 - t) + first * t + 0.5, 0, 255).astype(np.uint8)
    return out


def stabilize(frames, strength=1.0):
    """WAN drifts in brightness / colour (most at the very end of first+last-frame videos): every frame is pulled to
    the colour statistics (per channel mean + spread) of the steady middle of the clip."""
    n = len(frames)
    if n < 6 or strength <= 0:
        return frames
    mid = frames[n // 4: max(n // 4 + 1, 3 * n // 4)]
    ref = np.stack([f.reshape(-1, 3).astype(np.float32)[::7] for f in mid])
    rm, rs = ref.mean(axis=(0, 1)), ref.std(axis=(0, 1)) + 1e-3
    out = []
    for f in frames:
        x = f.astype(np.float32)
        flat = x.reshape(-1, 3)[::7]
        m, s = flat.mean(axis=0), flat.std(axis=0) + 1e-3
        y = (x - m) * (rs / s) + rm
        out.append(np.clip(x + (y - x) * strength + 0.5, 0, 255).astype(np.uint8))
    return out


def _small(f, w=64):
    h = max(1, int(f.shape[0] * w / f.shape[1]))
    return np.asarray(Image.fromarray(f).resize((w, h), Image.BILINEAR), np.float32)


def best_loop(frames, head=0.12, tail=0.3, keep=0.65):
    """The cut that loops best: start s near the beginning, end e near the end, frames[s:e] played round and round
    with frame e-1 -> frame s as close as two neighbouring frames. Returns (s, e, seam difference, typical step)."""
    n = len(frames)
    if n < 8:
        return 0, n, 0.0, 0.0
    sm = [_small(f) for f in frames]
    step = float(np.median([np.abs(sm[i + 1] - sm[i]).mean() for i in range(n - 1)]))
    best = (0, n, float(np.abs(sm[-1] - sm[0]).mean()))
    for s in range(0, max(1, int(n * head)) + 1):
        for e in range(max(s + int(n * keep), n - int(n * tail)), n + 1):
            if e - s < 4:
                continue
            nxt = sm[e] if e < n else None
            # e is not played: frame e-1 should flow into s like it would into e
            d = float(np.abs(sm[e - 1] - sm[s]).mean())
            if nxt is not None:
                d = min(d, float(np.abs(nxt - sm[s]).mean()) + step * 0.5)
            d += 0.02 * step * (n - (e - s))      # prefer keeping more of the clip
            if d < best[2]:
                best = (s, e, d)
    return best[0], best[1], best[2], step


def smart_loop(frames, blend=0):
    """Seamless loop finishing: colours steadied, the best loop point found, the seam cross-blended."""
    frames = stabilize(frames)
    s, e, _d, _step = best_loop(frames)
    clip = frames[s:e]
    k = int(blend) if blend else max(2, min(8, len(clip) // 10))
    k = min(k, len(clip) // 4)
    if k < 1:
        return clip
    out = list(clip)
    first = [f.astype(np.float32) for f in clip[:k]]
    # the last k frames lean into the first ones, so the jump back is spread over k frames
    for i in range(k):
        t = (i + 1) / (k + 1)
        a = clip[len(clip) - k + i].astype(np.float32)
        b = first[0] if i == k - 1 else first[0] * 0.5 + a * 0.5
        out[len(clip) - k + i] = np.clip(a * (1 - t) + b * t + 0.5, 0, 255).astype(np.uint8)
    return out


def seam_score(frames):
    """(jump at the loop point, typical step between frames) - a loop is seamless when they are close."""
    sm = [_small(f) for f in frames]
    step = float(np.median([np.abs(sm[i + 1] - sm[i]).mean() for i in range(len(sm) - 1)]))
    return float(np.abs(sm[0] - sm[-1]).mean()), step


def finish_loop(frames, loop, seam=0, interp=1, steady=True):
    if loop == "loop":
        return smart_loop(drop_duplicate_end(frames), seam)
    if steady:
        frames = stabilize(frames)
    if loop == "pingpong":
        return pingpong(frames)
    if loop == "crossfade":
        return crossfade(frames, max(int(seam) or 0, int(0.75 * 16 * max(1, interp))))
    return frames


# ------------------------------------------------------------------------------------------------ writing

def _even(frames):
    h, w = frames[0].shape[:2]
    return w - w % 2, h - h % 2


def write_mp4(frames, fps, path, crf=16):
    import imageio_ffmpeg
    w2, h2 = _even(frames)
    wr = imageio_ffmpeg.write_frames(path, (w2, h2), fps=float(fps), codec="libx264", quality=None,
                                     output_params=["-crf", str(int(crf)), "-preset", "slow", "-movflags", "+faststart"],
                                     pix_fmt_out="yuv420p", macro_block_size=1)
    wr.send(None)
    for f in frames:
        wr.send(np.ascontiguousarray(f[:h2, :w2]))
    wr.close()
    return path


def write_webm(frames, fps, path, crf=24):
    import imageio_ffmpeg
    w2, h2 = _even(frames)
    wr = imageio_ffmpeg.write_frames(path, (w2, h2), fps=float(fps), codec="libvpx-vp9", quality=None,
                                     output_params=["-crf", str(int(crf) + 8), "-b:v", "0"], pix_fmt_out="yuv420p",
                                     macro_block_size=1)
    wr.send(None)
    for f in frames:
        wr.send(np.ascontiguousarray(f[:h2, :w2]))
    wr.close()
    return path


def write_gif(frames, fps, path, max_side=720):
    ims = []
    for f in frames:
        im = Image.fromarray(f)
        if max(im.size) > max_side:
            s = max_side / max(im.size)
            im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
        ims.append(im)
    # one palette for the whole clip, from frames across it (a palette from one frame loses the others' colours)
    pick = ims[::max(1, len(ims) // 8)][:8]
    tw = min(160, pick[0].width)
    th = max(1, int(pick[0].height * tw / pick[0].width))
    mosaic = Image.new("RGB", (tw * len(pick), th))
    for i, im in enumerate(pick):
        mosaic.paste(im.resize((tw, th), Image.BILINEAR), (i * tw, 0))
    pal = mosaic.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    qs = [im.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG) for im in ims]
    qs[0].save(path, format="GIF", save_all=True, append_images=qs[1:], duration=max(20, int(round(1000 / float(fps)))),
               loop=0,
               disposal=1, optimize=False)
    return path


def write_video(frames, fps, path, fmt="mp4", crf=16):
    if fmt == "gif":
        return write_gif(frames, fps, path)
    if fmt == "webm":
        return write_webm(frames, fps, path, crf)
    return write_mp4(frames, fps, path, crf)


def _ffmpeg_pipe(args, data_in=None, feed=None):
    """Runs FFmpeg with stdin and stdout as pipes (nothing touches the disk). feed: an iterator of input bytes."""
    import subprocess
    import threading
    import imageio_ffmpeg
    p = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error"] + args,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=0x08000000)
    out, err = [], []

    def reader():
        out.append(p.stdout.read())

    def ereader():
        err.append(p.stderr.read())
    t1, t2 = threading.Thread(target=reader, daemon=True), threading.Thread(target=ereader, daemon=True)
    t1.start()
    t2.start()
    try:
        if data_in is not None:
            p.stdin.write(data_in)
        for b in feed or ():
            p.stdin.write(b)
        p.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    p.wait()
    t1.join()
    t2.join()
    if p.returncode != 0:
        raise RuntimeError("FFmpeg: " + (err[0] if err else b"").decode("utf-8", "replace").strip()[:300])
    return out[0] if out else b""


def encode_video_bytes(frames, fps, fmt="mp4", crf=16):
    """A video in memory (for the vault): the same quality as write_video, nothing written to disk."""
    if fmt == "gif":
        buf = io.BytesIO()
        write_gif(frames, fps, buf)
        return buf.getvalue()
    w2, h2 = _even(frames)
    feed = (np.ascontiguousarray(f[:h2, :w2]).tobytes() for f in frames)
    src = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w2}x{h2}", "-r", f"{float(fps)}", "-i", "pipe:0"]
    if fmt == "webm":
        enc = ["-c:v", "libvpx-vp9", "-crf", str(int(crf) + 8), "-b:v", "0", "-pix_fmt", "yuv420p", "-f", "webm"]
    else:
        enc = ["-c:v", "libx264", "-crf", str(int(crf)), "-preset", "slow", "-pix_fmt", "yuv420p",
               "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4"]
    return _ffmpeg_pipe(src + enc + ["pipe:1"], feed=feed)


def read_video_bytes(data, w, h, max_h=900, fps=16.0):
    """Frames of a video that is only in memory (vault). w / h: its size (frames come out at that size, scaled
    down to max_h)."""
    if data[:6] in (b"GIF87a", b"GIF89a"):
        im = Image.open(io.BytesIO(data))
        frames, dur = [], []
        try:
            while True:
                f = im.convert("RGB")
                if f.height > max_h:
                    s = max_h / f.height
                    f = f.resize((int(f.width * s), max_h), Image.LANCZOS)
                frames.append(np.asarray(f))
                dur.append(im.info.get("duration", 62) or 62)
                im.seek(im.tell() + 1)
        except EOFError:
            pass
        return frames, 1000.0 / (sum(dur) / max(1, len(dur)))
    w, h = int(w) - int(w) % 2, int(h) - int(h) % 2
    if h > max_h:
        w, h = int(w * max_h / h) // 2 * 2, max_h
    raw = _ffmpeg_pipe(["-i", "pipe:0", "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                       data_in=data)
    n = len(raw) // (w * h * 3)
    arr = np.frombuffer(raw[:n * w * h * 3], np.uint8).reshape(n, h, w, 3)
    return [arr[i] for i in range(n)], fps


def thumbnail_bytes(src, side=320):
    """A small JPEG in memory of an array or picture bytes."""
    im = Image.fromarray(src) if isinstance(src, np.ndarray) else Image.open(io.BytesIO(src)).convert("RGB")
    im.thumbnail((side, side), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=88)
    return buf.getvalue()


def png_with_text(data, text=None):
    """PNG bytes with text chunks added, in memory."""
    im = Image.open(io.BytesIO(data))
    info = PngImagePlugin.PngInfo()
    for k, v in (text or {}).items():
        if v is not None:
            info.add_text(k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    buf = io.BytesIO()
    im.save(buf, "PNG", pnginfo=info, compress_level=4)
    return buf.getvalue(), im.size


def read_video(path, max_h=900, limit=2000):
    """(frames as RGB arrays, fps) of an MP4 / WebM / GIF."""
    if path.lower().endswith(".gif"):
        im = Image.open(path)
        frames, dur = [], []
        try:
            while True:
                f = im.convert("RGB")
                if f.height > max_h:
                    s = max_h / f.height
                    f = f.resize((int(f.width * s), max_h), Image.LANCZOS)
                frames.append(np.asarray(f))
                dur.append(im.info.get("duration", 62) or 62)
                im.seek(im.tell() + 1)
                if len(frames) >= limit:
                    break
        except EOFError:
            pass
        return frames, 1000.0 / (sum(dur) / max(1, len(dur)))
    import imageio_ffmpeg
    rd = imageio_ffmpeg.read_frames(path, pix_fmt="rgb24")
    meta = rd.__next__()
    w, h = meta["size"]
    fps = float(meta.get("fps") or 16)
    frames = []
    for b in rd:
        f = np.frombuffer(b, np.uint8).reshape(h, w, 3)
        if h > max_h:
            im = Image.fromarray(f)
            s = max_h / h
            f = np.asarray(im.resize((int(w * s), max_h), Image.BILINEAR))
        frames.append(f)
        if len(frames) >= limit:
            break
    return frames, fps


def resample(frames, src_fps, dst_fps):
    """Frames at another frame rate (nearest frame - no new pictures are made)."""
    if not frames or abs(src_fps - dst_fps) < 0.01:
        return frames
    n = max(1, int(round(len(frames) * dst_fps / src_fps)))
    return [frames[min(len(frames) - 1, int(i * src_fps / dst_fps))] for i in range(n)]


def fit(frames, w, h):
    """Frames at w x h (scaled to cover, centred) - so clips of other sizes can be joined."""
    out = []
    for f in frames:
        if f.shape[1] == w and f.shape[0] == h:
            out.append(f)
            continue
        im = Image.fromarray(f)
        s = max(w / im.width, h / im.height)
        im = im.resize((max(w, int(round(im.width * s))), max(h, int(round(im.height * s)))), Image.LANCZOS)
        x, y = (im.width - w) // 2, (im.height - h) // 2
        out.append(np.asarray(im.crop((x, y, x + w, y + h))))
    return out


def join_clips(paths, tail=None, tail_fps=None, head=None):
    """Clips one after the other (+ new frames at the end) as one: the first clip's size and frame rate. When a clip
    starts on the last frame of the one before (Extend), that repeated frame is left out."""
    allf, fps = [], None
    parts = ([head] if head else []) + [(read_video(p, max_h=100000)) for p in paths]
    if tail is not None:
        parts.append((tail, tail_fps))
    for frames, f in parts:
        if not frames:
            continue
        if fps is None:
            fps = f
            h, w = frames[0].shape[:2]
        frames = fit(resample(frames, f, fps), w, h)
        if allf and np.abs(frames[0].astype(np.int16) - allf[-1].astype(np.int16)).mean() < 6:
            frames = frames[1:]
        allf.extend(frames)
    return allf, fps or 16.0


def last_frame(path):
    frames, _ = read_video(path, max_h=100000)
    return frames[-1] if frames else None


# ------------------------------------------------------------------------------------------------ pictures

def a1111_text(pos, neg, meta):
    """The 'parameters' text most tools read (A1111 / Civitai)."""
    parts = [f"Steps: {meta.get('steps')}", f"Sampler: {meta.get('sampler')}", f"Schedule type: {meta.get('scheduler')}",
             f"CFG scale: {meta.get('cfg')}", f"Seed: {meta.get('seed')}", f"Size: {meta.get('w')}x{meta.get('h')}",
             f"Model: {os.path.splitext(os.path.basename(meta.get('model') or ''))[0]}"]
    if meta.get("loras"):
        parts.append("Lora hashes: \"" + ", ".join(f"{os.path.splitext(os.path.basename(x))[0]}" for x in meta["loras"]) + "\"")
    return f"{pos}\nNegative prompt: {neg}\n" + ", ".join(parts) + ", Version: Better Comfy"


def parse_a1111(text):
    """Prompt, words to avoid, steps, guidance, seed and size from an A1111 / Civitai 'parameters' text."""
    import re
    out = {}
    lines = text.strip().splitlines()
    params_line = lines[-1] if re.search(r"Steps: \d+", lines[-1]) else ""
    body = chr(10).join(lines[:-1] if params_line else lines)
    if "Negative prompt:" in body:
        pos, neg = body.split("Negative prompt:", 1)
        out["prompt"], out["negative"] = pos.strip(), neg.strip()
    else:
        out["prompt"] = body.strip()
    for key, pat, conv in (("steps", r"Steps: (\d+)", int), ("cfg", r"CFG scale: ([\d.]+)", float),
                           ("seed", r"Seed: (\d+)", int)):
        m = re.search(pat, params_line)
        if m:
            out[key] = conv(m.group(1))
    m = re.search(r"Size: (\d+)x(\d+)", params_line)
    if m:
        out.update(size_mode="custom", width=int(m.group(1)), height=int(m.group(2)))
    out["auto_negative"] = False
    out["quality_tags"] = False
    return out


def save_png(data, path, text=None):
    """Save ComfyUI's PNG bytes with our text chunks (a ComfyUI 'prompt' chunk lets ComfyUI open its workflow)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im = Image.open(io.BytesIO(data))
    info = PngImagePlugin.PngInfo()
    for k, v in (text or {}).items():
        if v is not None:
            info.add_text(k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    im.save(path, "PNG", pnginfo=info, compress_level=4)
    return im.size


def read_png_text(path):
    try:
        with Image.open(path) as im:
            return dict(getattr(im, "text", {}) or {})
    except Exception:
        return {}


def thumbnail(src, dst, side=320):
    """A small JPEG of a picture or array (for the gallery)."""
    try:
        if isinstance(src, np.ndarray):
            im = Image.fromarray(src)
        else:
            im = Image.open(src).convert("RGB")
        im.thumbnail((side, side), Image.LANCZOS)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        im.convert("RGB").save(dst, "JPEG", quality=88)
        return dst
    except Exception:
        return None


def unique(path):
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(path):
        path = f"{base}_{i}{ext}"
        i += 1
    return path
