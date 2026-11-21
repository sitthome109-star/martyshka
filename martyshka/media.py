"""
ФОТО И ВИДЕО: Мартышка правит готовые картинки и видео (новые не рисует — генератор убран).

Фото:  убрать / заменить / размыть фон, обрезать, повернуть, отразить, размер, сжать, ч/б, ярче/темнее,
       контраст, резкость, размытие, сепия, другой формат.
Видео: обрезать по времени, убрать звук, вытащить звук в mp3, сжать / 720p, GIF, стоп-кадр, повернуть,
       ускорить / замедлить, заменить фон (медленно).

Какой файл: выделенный в Проводнике → названный путь → «последний скриншот» / «из загрузок» / «нарисованную» →
последний файл, с которым работали → самый свежий подходящий файл в Загрузках / Изображениях / Видео / Рабочем столе.
Оригинал НИКОГДА не перезаписываем: результат кладём рядом с новым именем и сразу открываем.
"""
import os
import re
import time
from fractions import Fraction
from pathlib import Path

from . import config

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic"}
VID_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv"}
HOME = Path.home()
OUT_DIR = HOME / "Pictures" / "Мартышка"
SHOTS = HOME / "Pictures" / "Скриншоты Мартышки"
os.environ.setdefault("U2NET_HOME", str(config.PROJECT_DIR / "models" / "image" / "rembg"))

last_file: Path | None = None   # последний файл, с которым работали (и последний результат)
_last_time = 0.0
_gpu_before = _gpu_after = None  # «освободить видеокарту» / «вернуть мозг» (задаёт главная программа)
_llm = None                      # мозг для перевода описания картинки на английский


def set_hooks(gpu_before=None, gpu_after=None, llm=None) -> None:
    global _gpu_before, _gpu_after, _llm
    _gpu_before, _gpu_after, _llm = gpu_before, gpu_after, llm


# ---------- какой файл ----------
def _explorer_selection() -> list[Path]:
    """Файлы, выделенные в окне Проводника (если оно сейчас активно)."""
    try:
        import ctypes
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        fg = ctypes.windll.user32.GetForegroundWindow()
        shell = comtypes.client.CreateObject("Shell.Application")
        wins = shell.Windows()
        for i in range(wins.Count):
            w = wins.Item(i)
            try:
                if w is None or int(w.HWND) != fg:
                    continue
                items = w.Document.SelectedItems()
                return [Path(items.Item(j).Path) for j in range(items.Count)]
            except Exception:
                continue
    except Exception:
        pass
    return []


def _newest(folders, exts, days=30) -> Path | None:
    best, best_t = None, 0.0
    limit = time.time() - days * 86400
    for d in folders:
        if not d.exists():
            continue
        for p in list(d.glob("*")) + list(d.glob("*/*")):
            try:
                if p.suffix.lower() in exts and p.is_file():
                    t = p.stat().st_mtime
                    if t > best_t and t > limit:
                        best, best_t = p, t
            except OSError:
                continue
    return best


def find_file(hint: str, kind: str) -> Path | None:
    """kind: 'img' или 'vid'."""
    global last_file
    exts = IMG_EXT if kind == "img" else VID_EXT
    hint = (hint or "").strip().strip("«»\"'")
    # 1) выделено в Проводнике
    for p in _explorer_selection():
        if p.suffix.lower() in exts:
            return p
    # 2) прямой путь или имя файла
    if hint:
        p = Path(os.path.expandvars(hint))
        if p.exists() and p.suffix.lower() in exts:
            return p
        h = hint.lower()
        by_place = None
        if re.search(r"скрин", h):
            by_place = _newest([SHOTS, HOME / "Pictures" / "Screenshots"], exts)
        elif re.search(r"загрузк|скачан", h):
            by_place = _newest([HOME / "Downloads"], exts)
        elif re.search(r"рабоч\w*\s+стол", h):
            by_place = _newest([HOME / "Desktop"], exts)
        elif re.search(r"нарисов|сгенер|созданн", h):
            by_place = _newest([OUT_DIR], exts)
        if by_place:
            return by_place
        # по части имени файла
        words = [w for w in re.findall(r"[\wа-яё]{3,}", h)
                 if w not in ("фото", "фотку", "картинку", "видео", "видос", "последн", "последнее", "последнюю",
                              "последний", "скриншот", "загрузок", "из", "этот", "эту", "файл", "мое", "моё", "мою")]
        if words and not by_place and not re.search(r"скрин|загрузк|скачан|рабоч|нарисов|сгенер", h):
            for d in (HOME / "Downloads", HOME / "Desktop", HOME / "Pictures", HOME / "Videos", HOME / "Documents"):
                for p in list(d.glob("*")) + list(d.glob("*/*")) if d.exists() else []:
                    if p.suffix.lower() in exts and all(w in p.stem.lower() for w in words):
                        return p
    # 3) с чем недавно работали
    if last_file and last_file.exists() and last_file.suffix.lower() in exts and time.time() - _last_time < 3600:
        return last_file
    # 4) самый свежий подходящий
    return _newest([HOME / "Downloads", HOME / "Desktop", HOME / "Pictures", SHOTS, OUT_DIR, HOME / "Videos"], exts)


def _out_path(src: Path, suffix: str, ext: str) -> Path:
    folder = src.parent if os.access(src.parent, os.W_OK) else OUT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{src.stem}_{suffix}{ext}"
    n = 2
    while p.exists():
        p = folder / f"{src.stem}_{suffix}_{n}{ext}"
        n += 1
    return p


def _done(path: Path, text: str, open_it: bool = True):
    global last_file, _last_time
    from .hands import Result, remember_file
    last_file, _last_time = path, time.time()
    remember_file(path)
    if open_it:
        try:
            os.startfile(str(path))
        except Exception:
            pass
    return Result(f"{text} Сохранил: {path.name} (в папке «{path.parent.name}»).")


# ---------- фото ----------
_rembg_session = None


def _cutout(img):
    """Вырезать объект (RGBA с прозрачным фоном)."""
    global _rembg_session
    import rembg
    if _rembg_session is None:
        _rembg_session = rembg.new_session("birefnet-general-lite")
    return rembg.remove(img, session=_rembg_session)


COLORS = {"бел": (255, 255, 255), "черн": (0, 0, 0), "чёрн": (0, 0, 0), "зел": (0, 177, 64), "син": (30, 90, 220),
          "красн": (220, 30, 40), "сер": (128, 128, 128), "жёлт": (250, 210, 0), "желт": (250, 210, 0),
          "розов": (255, 150, 200), "голуб": (110, 190, 255), "фиолет": (140, 80, 200), "оранж": (255, 140, 0)}


def _color_in(t: str):
    for k, c in COLORS.items():
        if k in t:
            return c
    return None


def photo(task: str, file_hint: str = "") -> "Result":
    from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps
    from .hands import Result
    t = (task or "").lower().replace("ё", "е")
    src = find_file(file_hint or task, "img")
    if src is None:
        return Result("Не нашёл картинку: выдели её в проводнике или скажи, где лежит.", ok=False)
    img = Image.open(src)
    img = ImageOps.exif_transpose(img)
    done, ext, quality = [], None, None


    if "фон" in t:
        cut = _cutout(img.convert("RGB"))
        if re.search(r"размы|размо|блюр|размаж|боке", t):
            back = img.convert("RGB").filter(ImageFilter.GaussianBlur(radius=max(img.size) // 60 + 4))
            back.paste(cut, (0, 0), cut)
            img = back; done.append("размыл фон")
        elif _color_in(t) and re.search(r"замени|поменяй|сделай|на\s|цвет|залей", t):
            c = _color_in(t)
            back = Image.new("RGB", cut.size, c); back.paste(cut, (0, 0), cut)
            img = back; done.append("поменял фон на однотонный")
        else:
            img = cut; ext = ".png"; done.append("убрал фон")

    m = re.search(r"(\d+)\s*[:x×/]\s*(\d+)", t)
    if re.search(r"обреж|обрез|кадрир|crop", t):
        w, h = img.size
        if m and int(m.group(1)) < 50:                         # соотношение 16:9, 1:1 …
            rw, rh = int(m.group(1)), int(m.group(2))
            if w / h > rw / rh:
                nw = int(h * rw / rh); img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
            else:
                nh = int(w * rh / rw); img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
            done.append(f"обрезал под {rw}:{rh}")
        elif "квадрат" in t:
            s = min(w, h); img = img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
            done.append("обрезал в квадрат")
        else:                                                   # убрать лишние поля / по объекту
            rgb = img.convert("RGB")
            bg = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
            box = ImageChops.difference(rgb, bg).point(lambda v: 255 if v > 18 else 0).getbbox()
            if not box or (box[2] - box[0]) * (box[3] - box[1]) > 0.92 * w * h:
                a = _cutout(rgb).getchannel("A").point(lambda v: 255 if v > 30 else 0)
                box = a.getbbox()
            if box:
                pad = int(0.04 * max(w, h))
                box = (max(0, box[0] - pad), max(0, box[1] - pad), min(w, box[2] + pad), min(h, box[3] + pad))
                img = img.crop(box); done.append("обрезал лишнее вокруг")
    if re.search(r"поверн|разверн|переверн", t):
        ang = 180 if re.search(r"180|вверх\s+ногами|переверн", t) else (90 if re.search(r"против|влево|налево", t) else -90)
        img = img.rotate(ang, expand=True); done.append("повернул")
    if re.search(r"отраз|зеркал", t):
        img = ImageOps.mirror(img) if not re.search(r"вертикал|сверху", t) else ImageOps.flip(img)
        done.append("отразил")
    if re.search(r"черно|ч/б|чб\b|сер(ым|ое|ую)|без\s+цвета|монохром", t):
        img = ImageOps.grayscale(img).convert("RGB"); done.append("сделал чёрно-белым")
    if "сепи" in t:
        g = ImageOps.grayscale(img); img = ImageOps.colorize(g, (40, 26, 13), (255, 240, 192)); done.append("сепия")
    if re.search(r"ярче|светлее|осветл", t):
        img = ImageEnhance.Brightness(img).enhance(1.3); done.append("сделал ярче")
    if re.search(r"темнее|затемн", t):
        img = ImageEnhance.Brightness(img).enhance(0.75); done.append("затемнил")
    if "контраст" in t:
        img = ImageEnhance.Contrast(img).enhance(1.35); done.append("добавил контраста")
    if re.search(r"насыщ|сочн|ярк\w+\s+цвет", t):
        img = ImageEnhance.Color(img).enhance(1.4); done.append("сделал цвета сочнее")
    if re.search(r"резк|четче|четкост", t):
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=140, threshold=2)); done.append("добавил резкости")
    if re.search(r"размой|размыть|размыт", t) and "фон" not in t:
        img = img.filter(ImageFilter.GaussianBlur(radius=6)); done.append("размыл")
    # размер
    pct = re.search(r"(\d+)\s*(%|процент)", t)
    px = re.search(r"(\d{3,4})\s*(px|пикс|пкс|точек)?", t) if re.search(r"размер|ширин|уменьш|увелич|px|пикс", t) else None
    if re.search(r"в\s+(два|2)\s+раза\s+меньше|вдвое\s+меньше|половин|уменьш\w*\s+(в\s+(два|2)\s+раза|вдвое)", t):
        img = img.resize((img.width // 2, img.height // 2)); done.append("уменьшил вдвое")
    elif re.search(r"в\s+(два|2)\s+раза\s+больше|вдвое\s+больше|увелич\w*\s+(в\s+(два|2)\s+раза|вдвое)", t):
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS); done.append("увеличил вдвое")
    elif pct and re.search(r"размер|уменьш|увелич", t):
        k = int(pct.group(1)) / 100; img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))))
        done.append(f"размер {pct.group(1)}%")
    elif px:
        nw = int(px.group(1)); img = img.resize((nw, max(1, int(img.height * nw / img.width))), Image.LANCZOS)
        done.append(f"ширина {nw} пикс")
    if re.search(r"сжать|сожми|сжми|меньше\s+весил|легче|для\s+(телеграм|вотсап|ватсап|дискорд|почт)", t):
        if max(img.size) > 1920:
            img.thumbnail((1920, 1920))
        ext, quality = ".jpg", 78; done.append("сжал")
    for e, name in ((".png", "png"), (".jpg", "jpg|jpeg|джпег|жпег"), (".webp", "webp|вебп")):
        if re.search(rf"\b(в|во|формат\w*)\s+({name})\b", t):
            ext = e; done.append(f"перевёл в {e[1:].upper()}")
    if not done:
        return Result("Не понял, что сделать с фото. Можно: убрать фон, обрезать, повернуть, размер, сжать, "
                      "чёрно-белым, ярче, в другой формат…", ok=False)
    ext = ext or (src.suffix.lower() if src.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ".png")
    if ext in (".jpg", ".jpeg") and img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    out = _out_path(src, "мартышка", ext)
    img.save(out, quality=quality or 92) if ext in (".jpg", ".jpeg", ".webp") else img.save(out)
    return _done(out, f"С фото «{src.name}»: {', '.join(done)}.")


# ---------- генерации картинок и видео больше нет (убрано 20.11.2026 по просьбе брата) ----------
def draw(description: str) -> "Result":
    from .hands import Result
    return Result("Рисовать картинки я больше не умею — генератор убран.", ok=False)


def illustrations(topic: str, n: int = 2) -> list[Path]:
    return []


# ---------- видео ----------
def _secs(s: str) -> float:
    s = s.strip()
    if ":" in s:
        parts = [float(x) for x in s.split(":")]
        return sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
    return float(s.replace(",", "."))


def _time_range(t: str, duration: float):
    T = r"(\d+(?::\d{1,2}){0,2}(?:[.,]\d+)?)"
    unit = lambda u: 60 if u and u.startswith("мин") else 1
    m = re.search(rf"перв\w+\s+{T}\s*(сек|мин)?", t)
    if m:
        return 0.0, _secs(m.group(1)) * unit(m.group(2))
    m = re.search(rf"последн\w+\s+{T}\s*(сек|мин)?", t)
    if m:
        return max(0.0, duration - _secs(m.group(1)) * unit(m.group(2))), duration
    m = re.search(rf"(?:с|от)\s+{T}\s*(сек\w*|мин\w*)?\s+(?:по|до)\s+{T}\s*(сек|мин)?", t)
    if m:
        u = unit(m.group(4) or m.group(2))
        return _secs(m.group(1)) * u, _secs(m.group(3)) * u
    m = re.search(rf"(?:с|от)\s+{T}\s*(сек|мин)?", t)
    if m:
        return _secs(m.group(1)) * unit(m.group(2)), duration
    m = re.search(rf"(?:до|по)\s+{T}\s*(сек|мин)?", t)
    if m:
        return 0.0, _secs(m.group(1)) * unit(m.group(2))
    return None


def _encoder():
    import av
    return "h264_nvenc" if "h264_nvenc" in av.codecs_available else "libx264"


def _transcode(src: Path, dst: Path, start=0.0, end=None, audio=True, height=None, speed=1.0, rotate=0,
               bitrate=None, frame_fn=None):
    import av
    import numpy as np
    inp = av.open(str(src))
    vin = inp.streams.video[0]
    ain = inp.streams.audio[0] if (audio and inp.streams.audio and speed == 1.0) else None
    fps = float(vin.average_rate or vin.guessed_rate or 30)
    out = av.open(str(dst), "w")
    w, h = vin.codec_context.width, vin.codec_context.height
    if rotate in (90, 270):
        w, h = h, w
    if height and height < h:
        w, h = int(w * height / h), height
    w, h = w // 2 * 2, h // 2 * 2
    enc = _encoder()
    rate = Fraction(round(fps * 1000), 1000)
    vout = out.add_stream(enc, rate=rate)
    vout.width, vout.height, vout.pix_fmt = w, h, "yuv420p"
    vout.bit_rate = bitrate or (5_000_000 if h >= 1000 else 2_500_000 if h >= 700 else 1_200_000)
    aout = None
    if ain is not None:
        aout = out.add_stream("aac", rate=ain.codec_context.sample_rate or 48000)
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=aout.rate)
    if start > 0:
        inp.seek(int(start / vin.time_base), stream=vin, backward=True)
    streams = [s for s in (vin, ain) if s is not None]
    last_pts = -1
    for pkt in inp.demux(*streams):
        for frame in pkt.decode():
            t = float(frame.time or 0)
            if t < start - 1e-3:
                continue
            if end is not None and t > end:
                continue
            if pkt.stream is vin:
                if speed != 1.0:
                    pts = int(round((t - start) / speed * fps))
                    if pts <= last_pts:
                        continue          # ускорение: лишние кадры пропускаем
                else:
                    pts = int(round((t - start) * fps))
                    if pts <= last_pts:
                        pts = last_pts + 1
                last_pts = pts
                if rotate or frame_fn:
                    arr = frame.to_ndarray(format="rgb24")
                    if rotate:
                        arr = np.ascontiguousarray(np.rot90(arr, k={90: 3, 180: 2, 270: 1}[rotate]))
                    if frame_fn:
                        arr = frame_fn(arr)
                    frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
                nf = frame.reformat(width=w, height=h, format="yuv420p")
                nf.pts, nf.time_base = pts, 1 / rate
                for p in vout.encode(nf):
                    out.mux(p)
            elif aout is not None:
                frame.pts = None
                for rf in resampler.resample(frame):
                    for p in aout.encode(rf):
                        out.mux(p)
        if end is not None and pkt.stream is vin and pkt.pts is not None and float(pkt.pts * vin.time_base) > end + 1:
            break
    for p in vout.encode():
        out.mux(p)
    if aout is not None:
        for p in aout.encode():
            out.mux(p)
    out.close()
    inp.close()


def video(task: str, file_hint: str = "") -> "Result":
    import av
    from .hands import Result
    t = (task or "").lower().replace("ё", "е")
    src = find_file(file_hint or task, "vid")
    if src is None:
        return Result("Не нашёл видео: выдели его в проводнике или скажи, где лежит.", ok=False)
    with av.open(str(src)) as c:
        duration = float(c.duration / 1_000_000) if c.duration else 0.0
        has_audio = bool(c.streams.audio)
        src_bitrate = int(c.bit_rate or 0)

    # стоп-кадр
    if re.search(r"кадр|скриншот|стоп.?кадр|фото\s+из", t):
        m = re.search(r"(\d+(?::\d{1,2})?)\s*(сек|мин)?", t)
        at = (_secs(m.group(1)) * (60 if m.group(2) and m.group(2).startswith("мин") else 1)) if m else duration / 2
        with av.open(str(src)) as c:
            v = c.streams.video[0]
            c.seek(int(at / v.time_base), stream=v)
            for f in c.decode(v):
                if float(f.time or 0) >= at - 0.05:
                    out = _out_path(src, f"кадр_{int(at)}с", ".png")
                    f.to_image().save(out)
                    return _done(out, f"Сохранил кадр на {int(at)}-й секунде.")
        return Result("Не смог достать кадр.", ok=False)

    # звук отдельно
    if re.search(r"(вытащ|достан|извлек|сохрани|отдельно|только)\w*\s+(звук|аудио|музык)|в\s+mp3|в\s+мп3", t):
        if not has_audio:
            return Result("В этом видео нет звука.", ok=False)
        out = _out_path(src, "звук", ".mp3")
        with av.open(str(src)) as c, av.open(str(out), "w") as o:
            a = c.streams.audio[0]
            ao = o.add_stream("mp3", rate=a.codec_context.sample_rate or 44100)
            rs = av.AudioResampler(format="s16p", layout="stereo", rate=ao.rate)
            for f in c.decode(a):
                f.pts = None
                for rf in rs.resample(f):
                    for p in ao.encode(rf):
                        o.mux(p)
            for p in ao.encode():
                o.mux(p)
        return _done(out, "Вытащил звук в MP3.")

    # GIF
    if re.search(r"гиф|gif", t):
        rng = _time_range(t, duration) or (0.0, min(duration, 8.0))
        start, end = rng[0], min(rng[1], rng[0] + 15)
        frames = []
        with av.open(str(src)) as c:
            v = c.streams.video[0]
            if start:
                c.seek(int(start / v.time_base), stream=v)
            nxt = start
            for f in c.decode(v):
                tt = float(f.time or 0)
                if tt < nxt:
                    continue
                if tt > end:
                    break
                im = f.to_image(); im.thumbnail((480, 480)); frames.append(im.convert("P", palette=1, colors=255))
                nxt += 1 / 12
        if not frames:
            return Result("Не получилось сделать GIF.", ok=False)
        out = _out_path(src, "gif", ".gif")
        frames[0].save(out, save_all=True, append_images=frames[1:], duration=83, loop=0, optimize=True)
        return _done(out, f"Сделал GIF ({len(frames)} кадров).")

    opts, done = {}, []
    rng = _time_range(t, duration) if re.search(r"обреж|обрез|вырежи|вырез|оставь|кусок|отрезок|перв|последн|с\s+\d|до\s+\d", t) else None
    if rng:
        opts["start"], opts["end"] = rng
        done.append(f"оставил с {int(rng[0])} по {int(rng[1])} сек")
    if re.search(r"(убери|убрать|удали|без|выключи)\w*\s+(звук|аудио|музык)|без\s+звука|немое", t):
        opts["audio"] = False; done.append("убрал звук")
    hm = re.search(r"\b(480|720|1080)\s*p?\b", t)
    if hm:
        opts["height"] = int(hm.group(1)); done.append(f"{hm.group(1)}p")
    if re.search(r"сжать|сожми|сжми|меньше\s+весил|легче|для\s+(телеграм|вотсап|ватсап|дискорд|почт)", t):
        opts.setdefault("height", 720)
        opts["bitrate"] = 900_000 if "дискорд" in t else 1_800_000
        if src_bitrate:   # сжатие всегда легче оригинала
            opts["bitrate"] = max(150_000, min(opts["bitrate"], int(src_bitrate * 0.6)))
        done.append("сжал")
    if re.search(r"поверн|разверн", t):
        opts["rotate"] = 180 if "180" in t else (270 if re.search(r"против|влево|налево", t) else 90)
        done.append("повернул")
    sm = re.search(r"(ускор|быстрее|замедл|медленнее)\w*(?:\s+в\s+(\d+(?:[.,]\d+)?|два|три))?", t)
    if sm:
        k = {"два": 2, "три": 3}.get(sm.group(2) or "", None) or float((sm.group(2) or "2").replace(",", "."))
        opts["speed"] = k if sm.group(1).startswith(("ускор", "быстр")) else 1 / k
        done.append(("ускорил" if opts["speed"] > 1 else "замедлил") + f" в {k:g} раза (без звука)")
    if "фон" in t:
        dur = (opts.get("end", duration) - opts.get("start", 0))
        if dur > 10:
            return Result("Фон в видео меняю только у коротких кусков (до 10 секунд): это долго — примерно 40 секунд "
                          "работы на каждую секунду видео. Скажи, например: «замени фон в видео с 0 по 5 секунду».",
                          ok=False)
        from PIL import Image
        import numpy as np
        color = _color_in(t) or (0, 177, 64)
        state = {"n": 0, "mask": None}

        def bg(arr):
            im = Image.fromarray(arr)
            # маску считаем на каждом 3-м кадре в уменьшенном виде — иначе очень долго (модель работает на процессоре)
            if state["mask"] is None or state["n"] % 3 == 0:
                small = im.copy(); small.thumbnail((384, 384))
                state["mask"] = _cutout(small).getchannel("A")
            state["n"] += 1
            back = Image.new("RGB", im.size, color); back.paste(im, (0, 0), state["mask"].resize(im.size))
            return np.asarray(back)
        opts["frame_fn"] = bg
        opts.setdefault("height", 720)
        done.append("заменил фон")
    if not done:
        return Result("Не понял, что сделать с видео. Можно: обрезать (с 10 по 30 секунду), убрать звук, вытащить "
                      "звук, сжать, 720p, GIF, кадр, повернуть, ускорить, замедлить, заменить фон.", ok=False)
    out = _out_path(src, "мартышка", ".mp4")
    _transcode(src, out, **opts)
    return _done(out, f"С видео «{src.name}»: {', '.join(done)}.")
