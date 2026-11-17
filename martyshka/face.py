"""
ЛИЦО МАРТЫШКИ — живой шар из светящихся точек поверх всех окон. Фона нет вообще.

Шар работает в ОТДЕЛЬНОМ процессе (своя «программа»), чтобы уши, мозг и голос
не отнимали у него время — поэтому анимация всегда плавная.

Как он устроен: тысячи точек на сфере («кожа») + точки внутри (мягкое наполнение).
По ним бегут волны, светятся «прожилки», шар медленно вращается.

Поведение:
  • в покое — маленький, в самом углу экрана;
  • позвали — из маленького шара вырываются две волны из частиц (двойная спираль: каждая крутится
    вокруг своей оси, впереди — тонкое острое начало). Они летят к рабочему месту, закручиваются
    кольцами вокруг него и собираются в большой шар;
  • во время разговора шар активно волнуется и пульсирует в такт голосу;
  • через FACE_IDLE_AFTER секунд тишины — так же, волнами, возвращается в угол.

Мышкой: левой кнопкой — перетащить; правый клик — меню (цвет, размер, выключить).
Выбор цвета/размера/места запоминается в data/face.json.
"""
import json
import math
import os
import threading
import time

import numpy as np

from . import config

SETTINGS_FILE = config.DATA_DIR / "face.json"
STATE_CODES = {"sleep": 0, "listen": 1, "think": 2, "game": 3}
STATE_NAMES = {v: k for k, v in STATE_CODES.items()}

# Как ведёт себя шар в каждом состоянии
#   amp — сила волн, bright — яркость, speed — скорость волн, veins — яркость прожилок, spin — вращение
STATES = {
    "sleep":  dict(amp=0.022, bright=0.60, speed=0.45, veins=1.0, spin=0.20),
    "listen": dict(amp=0.034, bright=1.00, speed=0.85, veins=1.4, spin=0.30),
    "think":  dict(amp=0.030, bright=0.92, speed=1.70, veins=1.9, spin=0.45),
    "speak":  dict(amp=0.048, bright=1.00, speed=1.45, veins=1.9, spin=0.40),
    "game":   dict(amp=0.012, bright=0.30, speed=0.25, veins=0.6, spin=0.10),   # режим игр: тусклый, сонный
}


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _hex_to_rgb(color: str) -> np.ndarray:
    color = config.FACE_COLORS.get(color, color).lstrip("#")
    try:
        return np.array([int(color[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255
    except Exception:
        return np.array([0.16, 0.61, 1.0], np.float32)


def _saturate(c: np.ndarray, k: float | None = None) -> np.ndarray:
    """Делает цвет насыщеннее (отодвигает от серого)."""
    k = config.FACE_SATURATION if k is None else k
    gray = c.mean()
    return np.clip(gray + (c - gray) * k, 0, 1).astype(np.float32)


def _smooth(x):
    """Плавный разгон и торможение (работает и с числами, и с массивами)."""
    x = np.clip(x, 0.0, 1.0)
    return x * x * x * (x * (x * 6 - 15) + 10)


def _fib_sphere(n: int, rng) -> np.ndarray:
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = np.pi * (1 + 5 ** 0.5) * i + rng.uniform(0, 0.3, n)
    return np.stack([np.cos(theta) * np.sin(phi), np.cos(phi), np.sin(theta) * np.sin(phi)], -1)


def _blur(img: np.ndarray, r: int) -> np.ndarray:
    """Быстрое размытие (двойной «ящик» через накопленные суммы) — для свечения."""
    def box1d(a, axis):
        c = np.cumsum(np.pad(a, [(r + 1, r) if i == axis else (0, 0) for i in range(2)]), axis=axis)
        hi = np.take(c, np.arange(2 * r + 1, c.shape[axis]), axis=axis)
        lo = np.take(c, np.arange(0, c.shape[axis] - 2 * r - 1), axis=axis)
        return (hi - lo) / (2 * r + 1)
    for _ in range(2):
        img = box1d(box1d(img, 0), 1)
    return img


class OrbRenderer:
    """Рисует кадр шара (и полёт волн) в картинку с прозрачностью (numpy)."""

    def __init__(self, size: int):
        rng = np.random.default_rng(7)
        k = 12   # мелкая рябь
        d = rng.normal(size=(k, 3)); d /= np.linalg.norm(d, axis=1, keepdims=True)
        self.fine_m = (d.T * rng.uniform(5.0, 11.0, k)).astype(np.float32)
        self.fine_ph = rng.uniform(0, 6.28, k).astype(np.float32)
        self.fine_sp = rng.uniform(0.5, 1.4, k).astype(np.float32)
        self.fine_n = np.float32(1 / np.sqrt(k))
        k2 = 5   # крупные медленные волны
        d2 = rng.normal(size=(k2, 3)); d2 /= np.linalg.norm(d2, axis=1, keepdims=True)
        self.big_m = (d2.T * rng.uniform(1.5, 3.0, k2)).astype(np.float32)
        self.big_ph = rng.uniform(0, 6.28, k2).astype(np.float32)
        self.big_n = np.float32(1 / np.sqrt(k2))
        self.set_size(size)

    def set_size(self, size: int) -> None:
        self.size = int(size)
        rng = np.random.default_rng(11)
        # 1) «кожа»: точки вдоль закрученных параллелей — волокнистая текстура
        k = math.sqrt(config.FACE_DENSITY)    # плотность частиц (больше — тоньше и богаче текстура)
        rings = min(int(170 * k), max(56, int(self.size * 1.25 * k)))
        per = min(int(200 * k), max(64, int(self.size * 1.45 * k)))
        lat = np.linspace(-np.pi / 2 * 0.985, np.pi / 2 * 0.985, rings)[:, None]
        lat = lat + rng.uniform(-0.3, 0.3, (rings, per)) * (np.pi / rings)
        lon = np.linspace(0, 2 * np.pi, per, endpoint=False)[None, :] + lat * 3.0
        skin = np.stack([np.cos(lat) * np.cos(lon), np.sin(lat), np.cos(lat) * np.sin(lon)], -1).reshape(-1, 3)
        skin_w = np.cos(lat).reshape(-1)
        n_skin = len(skin)
        # 2) наполнение: точки внутри шара
        n_in = int(n_skin * 0.32)
        inner = _fib_sphere(n_in, rng)
        inner_r = 0.9 * rng.uniform(0, 1, n_in) ** (1 / 3)

        self.P = np.concatenate([skin, inner]).astype(np.float32)
        self.shell_r = np.concatenate([np.ones(n_skin), inner_r]).astype(np.float32)
        radius = self.size * 0.46
        base = 0.25 * (57200 / 129.6 ** 2) / (n_skin / radius ** 2)
        self.w0 = (np.concatenate([skin_w, np.full(n_in, config.FACE_FILL)]) * base).astype(np.float32)
        self.is_skin = np.concatenate([np.ones(n_skin), np.zeros(n_in)]).astype(bool)
        self.PF = (self.P @ self.fine_m + self.fine_ph).astype(np.float32)
        self.PB = (self.P @ self.big_m + self.big_ph).astype(np.float32)

        # Для полёта: каждая точка принадлежит одной из двух волн и занимает своё место вдоль неё
        n = len(self.P)
        self.strand = (rng.uniform(0, 1, n) < 0.5).astype(np.float32)   # 0 или 1 — какая волна
        self.u = rng.uniform(0, 1, n).astype(np.float32)                 # 0 = острое начало, 1 = хвост
        self.thick = rng.uniform(0.55, 1.0, n).astype(np.float32)        # объём волны (не нитка)
        self.twist = rng.uniform(-0.5, 0.5, n).astype(np.float32)

    def radius_px(self, zoom: float) -> float:
        return self.size * 0.46 * zoom

    def render(self, W: int, H: int, t: float, rot: float, amp: float, bright: float, veins_k: float,
               color: np.ndarray, center: tuple, zoom: float, flight: dict | None = None) -> np.ndarray:
        """Кадр W×H×4 (RGBA 0..255). center — центр шара в пикселях холста.
        flight — если шар летит: {p: 0..1, A: откуда, B: куда, zA, zB: размеры, sgn: закрутка}."""
        color = _saturate(color)
        fine = np.sin(self.PF + t * self.fine_sp).sum(-1) * self.fine_n
        big = np.sin(self.PB + t * 0.3).sum(-1) * self.big_n
        r = self.shell_r * (1 + amp * np.tanh(1.2 * (fine + 0.6 * big)))   # волны ограничены
        x, y, z = self.P[:, 0] * r, self.P[:, 1] * r, self.P[:, 2] * r
        c, s = math.cos(rot), math.sin(rot)
        x, z = x * c + z * s, -x * s + z * c
        w = self.w0 * (0.85 + 1.3 * veins_k * np.exp(-(fine * 5.0) ** 2) * self.is_skin)
        front = np.where(z > 0, 1.0, 0.45)
        glow_k = 1.15

        if flight is None:
            sc = self.radius_px(zoom)
            px, py = center[0] + x * sc, center[1] + y * sc
            w = w * front * zoom * zoom
        else:
            px, py, w, glow_k = self._flight(flight, x, y, w, front, t)

        # --- точки → картинка ---
        # Рисуем в SS раз крупнее (суперсэмплинг) и каждую точку мягко «размазываем» между 4 соседними
        # пикселями по её точному положению — края гладкие, при движении ничего не «прыгает».
        # (в полёте рисуем в обычном разрешении: в движении разницы не видно, а плавность важнее)
        SS = max(1, int(config.FACE_SUPERSAMPLE)) if flight is None else 1
        out = np.zeros((H, W, 4), np.uint8)
        fx, fy = px * SS - 0.5, py * SS - 0.5
        ok = (fx >= 0) & (fx < W * SS - 1) & (fy >= 0) & (fy < H * SS - 1) & (w > 0)
        if not ok.any():
            return out
        fx, fy, w = fx[ok], fy[ok], w[ok] * (SS * SS * 0.75)
        ix, iy = fx.astype(np.int32), fy.astype(np.int32)
        dx, dy = fx - ix, fy - iy

        rb = max(1, int(self.radius_px(zoom if flight is None else max(flight["zA"], flight["zB"])) / 14))
        pad = (rb * 3 + 2) * SS
        x0 = max(0, (ix.min() - pad) // SS * SS); x1 = min(W * SS, (ix.max() + pad + SS) // SS * SS)
        y0 = max(0, (iy.min() - pad) // SS * SS); y1 = min(H * SS, (iy.max() + pad + SS) // SS * SS)
        bw, bh = x1 - x0, y1 - y0
        base = (iy - y0) * bw + (ix - x0)
        wx1, wy1 = w * dx, dy
        w00, w10 = w - wx1, wx1                      # (1-dx)·w, dx·w
        idx4 = np.concatenate([base, base + 1, base + bw, base + bw + 1])
        wts4 = np.concatenate([w00 * (1 - wy1), w10 * (1 - wy1), w00 * wy1, w10 * wy1])
        img = np.bincount(idx4, weights=wts4, minlength=bw * bh)[:bw * bh]
        img = img.reshape(bh, bw).astype(np.float32)

        # свечение считаем в обычном разрешении (оно и так размытое) и растягиваем обратно
        hs, ws = bh // SS, bw // SS
        small = img.reshape(hs, SS, ws, SS).mean(axis=(1, 3)) if SS > 1 else img
        glow = _blur(small, rb).astype(np.float32)
        if SS > 1:
            glow = np.repeat(np.repeat(glow, SS, axis=0), SS, axis=1)
        inten = (img + glow * np.float32(glow_k)) * np.float32(bright)
        a = np.minimum(inten * np.float32(1.9), np.float32(0.97))
        # цвет: тёмный оттенок → яркий → самые яркие искры чуть светлее; сразу «с прозрачностью»
        tone = np.minimum(inten * np.float32(1.2), np.float32(1.0))
        spark = np.clip(inten - np.float32(1.1), 0, 1) * np.float32(0.35)
        mix = (np.float32(0.4) + np.float32(0.6) * tone) * a
        sp_a = spark * a

        if SS > 1:  # уменьшаем обратно с правильным смешиванием прозрачности
            def down(m):
                return m.reshape(hs, SS, ws, SS).mean(axis=(1, 3))
            mix, sp_a, a = down(mix), down(sp_a), down(a)
        inv = 1.0 / np.maximum(a, np.float32(1e-4))
        rgb = np.clip(color[None, None, :] * (mix * inv)[..., None] + (sp_a * inv)[..., None], 0, 1)
        X0, Y0 = x0 // SS, y0 // SS
        out[Y0:Y0 + a.shape[0], X0:X0 + a.shape[1], :3] = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
        out[Y0:Y0 + a.shape[0], X0:X0 + a.shape[1], 3] = (a * 255).astype(np.uint8)
        return out

    def _flight(self, f: dict, x, y, w, front, t):
        """Перелёт шара из A в B «волнами»:
        1) частицы шара A стекают по поверхности к верхнему и нижнему полюсу (воронкой) — шар «сдувается»;
        2) из полюсов выходят две волны: одна сверху, одна снизу;
        3) на середине пути волны встречаются и скручиваются в жгут;
        4) жгут змейкой идёт в ЦЕНТР места B;
        5) из центра частицы расходятся по своим местам — шар B «надувается» изнутри, растёт по мере притока."""
        p = f["p"]
        W, H = f["W"], f["H"]
        A, B = np.array(f["A"], np.float32), np.array(f["B"], np.float32)
        rA, rB = self.radius_px(f["zA"]), self.radius_px(f["zB"])
        n = len(x)

        # --- «конвейер»: у каждой частицы своё место в очереди (u) ---
        D, Ltot, st = 0.35, 1.6, 0.35          # стекание к полюсу, растянутость потока, расхождение в шаре
        total = D + Ltot + 1 + st
        prog = 0.8 * p + 0.2 * _smooth(p)
        sp = prog * total - D - self.u * Ltot  # <-D: сидит в шаре A; -D..0: стекает к полюсу; 0..1: летит; >1: в шаре B
        head = prog * total - D                # самая передняя частица потока

        remain = float(np.mean(sp < 0))        # сколько ещё в шаре A
        arrived = float(np.mean(sp > 1))       # сколько уже в шаре B
        R_src = rA * (0.35 + 0.65 * remain ** (1 / 3))      # шар A «сдувается»
        grow = 0.3 + 0.7 * arrived ** (1 / 3)               # шар B «надувается» (сразу виден маленьким ядром)

        top = self.strand < 0.5
        pole_y = np.where(top, -1.0, 1.0).astype(np.float32)   # верх (-1) или низ (+1)

        # Базовые позиции: кто в шаре A — там (шар немного сжимается), кто в шаре B — там
        px = A[0] + x * R_src
        py = A[1] + y * R_src
        wgt = w * front * (f["zA"] * R_src / max(rA, 1e-3)) ** 2
        in_wave = np.zeros(n, np.float32)

        # --- 1) стекание к полюсам воронкой ---
        dr = np.nonzero((sp > -D) & (sp <= 0))[0]
        if len(dr):
            a = _smooth((sp[dr] + D) / D)
            # направление точки на шаре → к полюсу, с закруткой вокруг вертикали
            vx, vy = x[dr] * (1 - a), y[dr] * (1 - a) + pole_y[dr] * a
            ang = a * 2.2 * np.where(top[dr], 1.0, -1.0)
            vx = vx * np.cos(ang)
            ln = np.sqrt(vx * vx + vy * vy) + 1e-6
            rad = np.minimum(1.0, ln / np.maximum(np.sqrt(x[dr] ** 2 + y[dr] ** 2), 1e-3))  # глубина точки сохраняется
            px[dr] = A[0] + vx / ln * R_src * np.maximum(rad, a)
            py[dr] = A[1] + vy / ln * R_src * np.maximum(rad, a)
            wgt[dr] = w[dr] * (f["zA"] * R_src / max(rA, 1e-3)) ** 2 * (1 + 0.8 * a)   # у воронки ярче

        # --- 2–4) полёт: волна → жгут → змейка в центр B ---
        fl = np.nonzero((sp > 0) & (sp <= 1))[0]
        if len(fl):
            s_ = sp[fl]
            k_top = top[fl]
            AB = B - A
            dist = float(np.linalg.norm(AB)) + 1e-3
            dirAB = AB / dist
            perp = np.array([-dirAB[1], dirAB[0]])
            M = A + AB * 0.5
            h = dist * 0.35
            res = np.zeros((len(fl), 2), np.float32)
            nrm = np.zeros((len(fl), 2), np.float32)
            depth = np.zeros(len(fl), np.float32)

            # первая половина: каждая волна от своего полюса к середине пути (кривая Безье)
            first = s_ <= 0.5
            if first.any():
                v = (s_[first] / 0.5)[:, None]
                py_sign = pole_y[fl][first][:, None]
                P0 = A + np.stack([np.zeros_like(py_sign[:, 0]), py_sign[:, 0]], -1) * R_src
                P1 = np.clip(P0 + np.stack([np.zeros_like(py_sign[:, 0]), py_sign[:, 0]], -1) * h,
                             [12, 12], [W - 12, H - 12])          # сначала вылетает вверх/вниз
                P2 = M - dirAB * h + perp * 0.0
                P3 = M
                b0, b1, b2, b3 = (1 - v) ** 3, 3 * (1 - v) ** 2 * v, 3 * (1 - v) * v ** 2, v ** 3
                res[first] = b0 * P0 + b1 * P1 + b2 * P2 + b3 * P3
                tg = 3 * (1 - v) ** 2 * (P1 - P0) + 6 * (1 - v) * v * (P2 - P1) + 3 * v ** 2 * (P3 - P2)
                tg /= np.linalg.norm(tg, axis=1, keepdims=True) + 1e-6
                nrm[first] = np.stack([-tg[:, 1], tg[:, 0]], -1)
                depth[first] = 0.5

            # вторая половина: волны скручиваются в жгут и змейкой идут в центр B
            second = ~first
            if second.any():
                v = (s_[second] - 0.5) / 0.5
                # плавная змейка: одна мягкая волна, которая «перекатывается» во времени
                snake = dist * 0.16 * np.sin(np.pi * v * 1.2 - t * 2.5) * np.sin(np.pi * v)
                center = M + np.outer(v, B - M) + np.outer(snake, perp)
                # жгут: волны один раз плавно обвивают друг друга и сходятся в центре B
                braid_r = max(rA, rB) * 0.16 * _smooth(v / 0.25) * (1 - _smooth((v - 0.6) / 0.4))
                theta = np.where(k_top[second], 0.0, np.pi) + v * 4.5 + t * 3.0
                res[second] = center + np.outer(braid_r * np.cos(theta), perp)
                nrm[second] = perp
                depth[second] = 0.5 + 0.5 * np.sin(theta)

            # толщина ленты: тонкий нос, дальше ровная (одна лента, без двойной спирали)
            nose = _smooth((head - s_) / 0.25)
            scale_here = R_src + (rB * grow - R_src) * s_
            thick = np.maximum(scale_here, max(rA, rB) * 0.6) * 0.17 * (0.2 + 0.8 * nose) * (self.twist[fl] * 2)
            edge = 6.0
            px[fl] = np.clip(res[:, 0] + nrm[:, 0] * thick, edge, W - edge)
            py[fl] = np.clip(res[:, 1] + nrm[:, 1] * thick, edge, H - edge)
            in_wave[fl] = 1.0
            wgt[fl] = w[fl] * 0.30 * (0.5 + 0.5 * depth) * (1.0 + 0.9 * (1 - nose)) * max(f["zA"], f["zB"]) ** 2

        # --- 5) в шаре B: частица выходит из центра и расходится на своё место, с закруткой ---
        ar = np.nonzero(sp > 1)[0]
        if len(ar):
            e = _smooth((sp[ar] - 1) / st)
            al = (1 - e) * 2.6                                       # расходятся по спирали
            ca, sa = np.cos(al), np.sin(al)
            xa = x[ar] * ca - y[ar] * sa
            ya = x[ar] * sa + y[ar] * ca
            R_now = rB * grow * (0.35 + 0.65 * e)      # из ядра — наружу, на своё место
            px[ar] = B[0] + xa * R_now
            py[ar] = B[1] + ya * R_now
            # яркость НЕ проваливается, пока шар маленький: долетевшие частицы видны сразу
            wgt[ar] = w[ar] * front[ar] * f["zB"] ** 2 * (0.75 + 0.25 * e)

        return px, py, wgt, 1.15 - 0.4 * float(np.mean(in_wave))


# ======================================================================
#  Окно шара — работает в отдельном процессе
# ======================================================================
DEBUG_LOG = os.environ.get("MARTYSHKA_FACE_LOG")
DEBUG_GAPS: list = []


def _face_process(shared: dict, parent_pid: int) -> None:
    import psutil
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QMenu, QWidget

    app = QApplication([])
    # Окно шара для Windows «служебное» — без этого Qt закрывает программу, когда закрыли окно настроек
    app.setQuitOnLastWindowClosed(False)
    screen = QGuiApplication.primaryScreen().availableGeometry()
    st = _load_settings()
    prefs = {"color": st.get("color", config.FACE_COLOR), "size": int(st.get("size", config.FACE_SIZE)),
             "center": st.get("center")}

    def save():
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def corner_point(off: float):
        left, top = screen.left() + off, screen.top() + off
        right, bottom = screen.right() - off, screen.bottom() - off
        return {"лево-верх": (left, top), "право-верх": (right, top),
                "лево-низ": (left, bottom), "право-низ": (right, bottom)}.get(config.FACE_CORNER, (left, top))

    def idle_center():
        return corner_point(config.FACE_IDLE_MARGIN + config.FACE_IDLE_SIZE * 0.5)

    def active_center():
        if prefs["center"]:
            return tuple(prefs["center"])
        return corner_point(config.FACE_MARGIN + prefs["size"] * 0.62)

    class OrbWindow(QWidget):
        def __init__(self):
            super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground)       # фона нет вообще
            self.setAttribute(Qt.WA_ShowWithoutActivating)       # не отбирает фокус у игр/окон
            self.setWindowTitle(config.NAME)
            self.renderer = OrbRenderer(prefs["size"])
            self.frame = None
            self.t = self.rot = 0.0
            self.last = time.perf_counter()
            self.cur = dict(STATES["sleep"])
            self.level = 0.0
            self.out = 0.0              # 0 = в углу, 1 = на рабочем месте
            self.moving = 0             # +1 летим на место, -1 летим в угол, 0 стоим
            self.flight_p = 0.0
            self.last_active = -1e9
            self.drag_from = None
            self.layout_window()
            self.timer = QTimer(self)
            self.timer.setTimerType(Qt.PreciseTimer)
            self.timer.timeout.connect(self.step)
            self.timer.start(int(1000 / config.FACE_FPS))
            self.watch = QTimer(self)
            self.watch.timeout.connect(self.check_parent)
            self.watch.start(1000)

        def check_parent(self):
            if not psutil.pid_exists(parent_pid):   # основная Мартышка закрылась — закрываемся и мы
                os._exit(0)

        def layout_window(self):
            """Окно накрывает и угол, и рабочее место (чтобы волнам было где лететь), но не выходит за экран."""
            ix, iy = idle_center()
            ax, ay = active_center()
            ra = self.renderer.radius_px(1.0) * 1.75          # запас под волны, кольца и свечение
            ri = self.renderer.radius_px(config.FACE_IDLE_SIZE / prefs["size"]) * 1.8
            x0 = max(screen.left(), int(min(ix - ri, ax - ra)))
            y0 = max(screen.top(), int(min(iy - ri, ay - ra)))
            x1 = min(screen.right() + 1, int(max(ix + ri, ax + ra)))
            y1 = min(screen.bottom() + 1, int(max(iy + ri, ay + ra)))
            self.setGeometry(x0, y0, x1 - x0, y1 - y0)
            self.origin = (x0, y0)

        def local(self, pt):
            return (pt[0] - self.origin[0], pt[1] - self.origin[1])

        def step(self):
            now = time.perf_counter()
            dt = min(0.05, now - self.last)
            if DEBUG_LOG:  # отладка плавности (только если включено переменной окружения)
                DEBUG_GAPS.append(now - self.last)
                if len(DEBUG_GAPS) >= 120:
                    g = np.array(DEBUG_GAPS) * 1000; DEBUG_GAPS.clear()
                    with open(DEBUG_LOG, "a", encoding="utf-8") as fh:
                        fh.write(f"{time.time():.1f} state={shared['state'].value} speak={shared['speaking'].value} "
                                 f"moving={self.moving} fps={1000/g.mean():.0f} p95={np.percentile(g,95):.0f}ms max={g.max():.0f}ms\n")
            self.last = now
            if shared["quit"].value:
                os._exit(0)
            speaking = bool(shared["speaking"].value)
            state = "speak" if speaking else STATE_NAMES.get(shared["state"].value, "sleep")

            if state not in ("sleep", "game") or self.drag_from is not None:
                self.last_active = now
            want_out = (now - self.last_active) < config.FACE_IDLE_AFTER

            # начать полёт, если нужно
            if self.moving == 0:
                if want_out and self.out == 0:
                    self.moving, self.flight_p = 1, 0.0
                elif not want_out and self.out == 1:
                    self.moving, self.flight_p = -1, 0.0
            if self.moving:
                self.flight_p = min(1.0, self.flight_p + dt / max(0.3, config.FACE_MOVE_SECONDS))
                if self.flight_p >= 1.0:
                    self.out = 1.0 if self.moving > 0 else 0.0
                    self.moving = 0

            target = STATES.get(state, STATES["sleep"])
            k = min(1.0, dt * 3)
            for key in self.cur:
                self.cur[key] += (target[key] - self.cur[key]) * k
            lvl = float(shared["level"].value) if speaking else 0.0
            self.level += (lvl - self.level) * min(1.0, dt * 12)
            breath = 0.07 * math.sin(now * 1.6) if state == "sleep" and self.out == 0 and not self.moving else 0.0

            self.t += dt * self.cur["speed"]
            self.rot += dt * (self.cur["spin"] + self.level * 0.9)       # на громких словах крутится быстрее
            amp = self.cur["amp"] + self.level * 0.09
            bright = self.cur["bright"] + breath + self.level * 0.5
            pulse = 1 + 0.07 * self.level                                # шар «дышит» в такт голосу

            idle_zoom = config.FACE_IDLE_SIZE / prefs["size"]
            I, Act = self.local(idle_center()), self.local(active_center())
            W, H = self.width(), self.height()
            if self.moving:
                f = dict(p=self.flight_p, sgn=1.0, W=self.width(), H=self.height(),
                         A=I if self.moving > 0 else Act, B=Act if self.moving > 0 else I,
                         zA=idle_zoom if self.moving > 0 else 1.0, zB=1.0 if self.moving > 0 else idle_zoom)
                rgba = self.renderer.render(W, H, self.t, self.rot, amp, bright * 1.15, self.cur["veins"],
                                            _hex_to_rgb(prefs["color"]), f["B"], f["zB"], flight=f)
            else:
                center = Act if self.out == 1 else I
                zoom = (1.0 if self.out == 1 else idle_zoom) * pulse
                rgba = self.renderer.render(W, H, self.t, self.rot, amp, bright, self.cur["veins"],
                                            _hex_to_rgb(prefs["color"]), center, zoom)
            self.frame = QImage(rgba.data, W, H, rgba.strides[0], QImage.Format_RGBA8888).copy()
            self.update()

            calm = self.out == 0 and not self.moving and not want_out
            interval = int(1000 / (30 if calm else config.FACE_FPS))
            if self.timer.interval() != interval:
                self.timer.setInterval(interval)

        def paintEvent(self, _):
            if self.frame is not None:
                p = QPainter(self)
                p.setCompositionMode(QPainter.CompositionMode_Source)
                p.drawImage(0, 0, self.frame)
                p.end()

        # --- мышь ---
        def orb_center_now(self):
            return active_center() if self.out == 1 else idle_center()

        # Левая кнопка: щёлкнул — меню; зажал и потащил — двигаешь шар. Правая кнопка — тоже меню.
        def mousePressEvent(self, e):
            if e.button() == Qt.LeftButton:
                self.press_pos = e.globalPosition()
                self.drag_from = None

        def mouseMoveEvent(self, e):
            p = e.globalPosition()
            if getattr(self, "press_pos", None) is not None and self.drag_from is None:
                if abs(p.x() - self.press_pos.x()) + abs(p.y() - self.press_pos.y()) > 6:   # это перетаскивание
                    c = self.orb_center_now()
                    self.drag_from = (self.press_pos.x() - c[0], self.press_pos.y() - c[1])
                    self.out, self.moving = 1.0, 0
            if self.drag_from is not None:
                prefs["center"] = [p.x() - self.drag_from[0], p.y() - self.drag_from[1]]
                self.layout_window()

        def mouseReleaseEvent(self, e):
            if e.button() != Qt.LeftButton:
                return
            if self.drag_from is not None:      # перетащили — запоминаем место
                self.drag_from = None
                save()
            elif getattr(self, "press_pos", None) is not None:   # просто щёлкнули — меню
                self.show_menu(e.globalPosition().toPoint())
            self.press_pos = None

        def contextMenuEvent(self, e):
            self.show_menu(e.globalPos())

        def show_menu(self, pos):
            self.last_active = time.perf_counter()
            menu = QMenu(self)
            colors = menu.addMenu("Цвет")
            group = QActionGroup(colors)
            for name in config.FACE_COLORS:
                act = QAction(name, colors, checkable=True, checked=(name == prefs["color"]))
                act.triggered.connect(lambda _=False, n=name: (prefs.update(color=n), save()))
                group.addAction(act); colors.addAction(act)
            sizes = menu.addMenu("Размер")
            group2 = QActionGroup(sizes)
            for name, px in config.FACE_SIZES.items():
                act = QAction(name, sizes, checkable=True, checked=(px == prefs["size"]))
                act.triggered.connect(lambda _=False, p=px: self.set_size(p))
                group2.addAction(act); sizes.addAction(act)
            back = menu.addAction("Вернуть на место по умолчанию")
            back.triggered.connect(lambda: (prefs.update(center=None), save(), self.layout_window()))
            menu.addSeparator()
            settings = menu.addAction("Настройки…")
            settings.triggered.connect(self.open_settings)
            in_game = shared["state"].value == STATE_CODES["game"]
            game = QAction("Режим игр (освободить видеокарту)", menu, checkable=True, checked=in_game)
            game.triggered.connect(lambda: setattr(shared["game_req"], "value", shared["game_req"].value + 1))
            menu.addAction(game)
            log = menu.addAction("Журнал разговора")
            log.triggered.connect(self.open_log)
            from . import system
            auto = QAction("Запускать вместе с Windows", menu, checkable=True, checked=system.autostart_enabled())
            auto.triggered.connect(lambda checked: system.set_autostart(checked))
            menu.addAction(auto)
            menu.addSeparator()
            quit_act = menu.addAction("Выключить Мартышку")
            quit_act.triggered.connect(self.quit_all)
            menu.exec(pos)

        def open_settings(self):
            from .settings_ui import open_settings

            def live(key, value):          # цвет и размер шара — меняем сразу
                if key == "FACE_COLOR":
                    prefs["color"] = value; save()
                elif key == "FACE_SIZE":
                    self.set_size(int(value))

            def restart():
                shared["restart"].value = 1   # основная Мартышка перезапустится с новыми настройками

            self.last_active = time.perf_counter()
            open_settings(None, on_live_change=live, on_restart=restart)

        def open_log(self):
            from . import system
            if system.LOG_FILE.exists():
                os.startfile(system.LOG_FILE)

        def set_size(self, px):
            prefs["size"] = int(px)
            self.renderer = OrbRenderer(prefs["size"])
            self.layout_window()
            save()

        def quit_all(self):
            shared["quit"].value = 1          # основная Мартышка увидит и закроется
            time.sleep(0.3)
            os._exit(0)

        # Мышь проходит сквозь прозрачные места окна (там, где нет точек шара)

    win = OrbWindow()
    win.show()
    app.exec()


class Face:
    """Пульт шара для основной программы: запускает шар отдельным процессом и передаёт ему,
    что сейчас происходит (ждёт / слушает / думает) и насколько громко говорит голос."""

    def __init__(self, voice=None):
        import multiprocessing as mp
        self.voice = voice
        self._mp = mp
        ctx = mp.get_context("spawn")
        self.shared = {"state": ctx.Value("i", 0, lock=False), "speaking": ctx.Value("b", 0, lock=False),
                       "level": ctx.Value("d", 0.0, lock=False), "quit": ctx.Value("b", 0, lock=False),
                       "game_req": ctx.Value("i", 0, lock=False),   # нажали «Режим игр» в меню шара
                       "restart": ctx.Value("b", 0, lock=False)}    # «Сохранить и перезапустить» в настройках
        self.on_game_toggle = None    # что делать, когда в меню нажали «Режим игр»
        self.process = ctx.Process(target=_face_process, args=(self.shared, os.getpid()),
                                   daemon=True, name="martyshka-face")

    def start(self) -> None:
        self.process.start()
        threading.Thread(target=self._feed, daemon=True, name="face-feed").start()

    def set_state(self, state: str) -> None:
        self.shared["state"].value = STATE_CODES.get(state, 0)

    def _feed(self) -> None:
        """~100 раз в секунду передаём шару, говорит ли голос и как громко. И следим за кнопками меню."""
        game_req = 0
        while True:
            if self.shared["game_req"].value != game_req:
                game_req = self.shared["game_req"].value
                if self.on_game_toggle:
                    self.on_game_toggle()
            v = self.voice
            speaking = v is not None and v.is_speaking
            self.shared["speaking"].value = 1 if speaking else 0
            self.shared["level"].value = float(getattr(v, "level", 0.0)) if speaking else 0.0
            if self.shared["quit"].value:
                os._exit(0)  # нажали «Выключить Мартышку» в меню шара
            if self.shared["restart"].value:
                from . import system
                system.restart_self()   # запускаем новую Мартышку и закрываемся
            time.sleep(0.01)
