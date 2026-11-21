"""Аватарка Мартышки в аниме-стиле: девушка с обезьяньими ушками, наушники, бантик со звездой ✦.
Рисуется кодом (генерации картинок в проекте нет). Запуск: .venv\\Scripts\\python.exe tools\\avatar_anime.py [куда.png]"""
import math
import random
import sys

from PIL import Image, ImageDraw, ImageFilter

S = 1024
OUT = sys.argv[1] if len(sys.argv) > 1 else "tools/martyshka_avatar_anime.png"
random.seed(11)


def blur_layer(draw_fn, radius):
    lay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(lay))
    return lay.filter(ImageFilter.GaussianBlur(radius))


def smooth(points, steps=12):
    """Сглаженный замкнутый контур (Catmull-Rom) — плавные линии вместо углов."""
    out = []
    n = len(points)
    for i in range(n):
        p0, p1, p2, p3 = points[i - 1], points[i], points[(i + 1) % n], points[(i + 2) % n]
        for t in range(steps):
            t /= steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                                     + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in range(2)))
    return out


# ---------- фон: фиолетовый свет и точки, как у шара ----------
img = Image.new("RGBA", (S, S), (16, 10, 32, 255))
img.alpha_composite(blur_layer(lambda d: [d.ellipse((S/2 - r, S/2 - r, S/2 + r, S/2 + r), fill=c) for r, c in
                                          ((560, (70, 34, 130, 255)), (380, (120, 60, 205, 255)))], 140))
dots = Image.new("RGBA", (S, S), (0, 0, 0, 0))
dd = ImageDraw.Draw(dots)
for _ in range(1500):
    a, rr = random.uniform(0, 2 * math.pi), random.gauss(470, 30)
    x, y, s = S/2 + rr * math.cos(a), S/2 + rr * math.sin(a), random.uniform(1.5, 4.5)
    dd.ellipse((x - s, y - s, x + s, y + s), fill=random.choice([(185, 135, 255), (150, 115, 255), (235, 200, 255)])
               + (random.randint(90, 230),))
img.alpha_composite(dots.filter(ImageFilter.GaussianBlur(0.8)))

L = Image.new("RGBA", (S, S), (0, 0, 0, 0))
p = ImageDraw.Draw(L)
cx = S // 2
HAIR, HAIR_D, HAIR_L = (92, 52, 120), (62, 32, 88), (150, 100, 190)
SKIN, SKIN_SH = (255, 228, 214), (240, 198, 186)
FUR, FUR_IN = (150, 98, 72), (245, 175, 175)
PURPLE, PURPLE_L = (125, 72, 228), (178, 132, 255)

# ---------- волосы сзади (длинные) ----------
p.polygon(smooth([(cx - 250, 330), (cx - 330, 560), (cx - 320, 860), (cx - 200, 1000), (cx + 200, 1000),
                  (cx + 320, 860), (cx + 330, 560), (cx + 250, 330), (cx, 210)]), fill=HAIR_D)
# плечи и худи
p.polygon(smooth([(cx - 360, 1024), (cx - 300, 880), (cx - 120, 820), (cx + 120, 820), (cx + 300, 880),
                  (cx + 360, 1024)], 8), fill=PURPLE)
p.polygon([(cx - 70, 820), (cx, 905), (cx + 70, 820)], fill=(90, 45, 170))       # вырез
# шея
p.rounded_rectangle((cx - 55, 690, cx + 55, 850), radius=30, fill=SKIN_SH)

# ---------- лицо: аниме-овал с острым подбородком ----------
face = smooth([(cx - 205, 400), (cx - 210, 560), (cx - 165, 680), (cx - 70, 760), (cx, 778), (cx + 70, 760),
               (cx + 165, 680), (cx + 210, 560), (cx + 205, 400), (cx, 330)])
p.polygon(face, fill=SKIN)
# румянец штрихами + мягкий
L.alpha_composite(blur_layer(lambda d: [d.ellipse((cx + sx * 135 - 55, 615, cx + sx * 135 + 55, 660),
                                                  fill=(255, 120, 165, 140)) for sx in (-1, 1)], 12))
p = ImageDraw.Draw(L)
for sx in (-1, 1):
    for k in range(3):
        x0 = cx + sx * (110 + k * 22)
        p.line((x0, 645, x0 + 16, 625), fill=(240, 110, 150), width=5)

# ---------- глаза (большие, аниме) ----------
for sx in (-1, 1):
    ex, ey = cx + sx * 95, 545
    w, h = 78, 92
    p.ellipse((ex - w, ey - h, ex + w, ey + h), fill=(255, 255, 255))
    # радужка с градиентом сверху вниз (тёмно-фиолетовый → светло-сиреневый)
    for i in range(30):
        t = i / 29
        col = (int(70 + 110 * t), int(30 + 90 * t), int(150 + 95 * t))
        rw, rh = 58 - i * 0.3, 80 - i * 0.9
        p.ellipse((ex - rw, ey - 70 + i * 2.2, ex + rw, ey - 70 + i * 2.2 + rh * 1.6), fill=col)
    p.ellipse((ex - 58, ey - 72, ex + 58, ey + 92), outline=(50, 20, 90), width=6)
    p.ellipse((ex - 24, ey - 20, ex + 24, ey + 40), fill=(30, 12, 55))          # зрачок
    p.ellipse((ex - 40 + sx * 6, ey - 52, ex - 6 + sx * 6, ey - 14), fill=(255, 255, 255))   # большой блик
    p.ellipse((ex + 14, ey + 30, ex + 30, ey + 46), fill=(255, 255, 255, 230))   # малый блик
    p.arc((ex - 30, ey + 30, ex + 30, ey + 80), 20, 160, fill=(230, 200, 255), width=5)
    # верхнее веко — толстая линия ресниц с хвостиком наружу
    p.arc((ex - w - 6, ey - h - 4, ex + w + 6, ey + h), 200, 340, fill=(35, 18, 40), width=16)
    p.polygon([(ex + sx * (w - 2), ey - 40), (ex + sx * (w + 38), ey - 70), (ex + sx * (w + 8), ey - 18)],
              fill=(35, 18, 40))
    p.line((ex + sx * (w - 10), ey - 62, ex + sx * (w + 22), ey - 96), fill=(35, 18, 40), width=8)
    # нижнее веко
    p.arc((ex - w + 14, ey - h + 30, ex + w - 14, ey + h + 4), 50, 130, fill=(120, 70, 90), width=5)
    # бровь
    p.arc((ex - 70, ey - 175, ex + 70, ey - 105), 210 if sx < 0 else 200, 340 if sx < 0 else 330,
          fill=HAIR_D, width=9)

# носик и ротик
p.line((cx - 4, 640, cx + 4, 652), fill=(215, 150, 140), width=5)
p.chord((cx - 34, 668, cx + 34, 712), 15, 165, fill=(200, 60, 105))
p.chord((cx - 24, 672, cx + 24, 700), 20, 160, fill=(255, 140, 170))
p.arc((cx - 34, 662, cx + 34, 706), 15, 165, fill=(120, 40, 70), width=4)

# ---------- чёлка (пряди-клинья) ----------
bangs_base = 330
strands = [(-215, 600), (-170, 520), (-110, 500), (-50, 470), (10, 480), (70, 470), (125, 505), (175, 530),
           (215, 600)]
p.polygon(smooth([(cx - 240, 470), (cx - 230, 330), (cx - 120, 230), (cx, 205), (cx + 120, 230), (cx + 230, 330),
                  (cx + 240, 470), (cx, 330)], 10), fill=HAIR)
for i in range(len(strands) - 1):
    (x1, y1), (x2, y2) = strands[i], strands[i + 1]
    top_l, top_r = cx + x1 * 0.8, cx + x2 * 0.8
    tip = (cx + (x1 + x2) / 2, max(y1, y2) - 30 + (25 if i % 2 else 0))
    p.polygon([(top_l, bangs_base - 20), (top_r, bangs_base - 20), tip], fill=HAIR)
# боковые пряди у лица
for sx in (-1, 1):
    p.polygon(smooth([(cx + sx * 205, 400), (cx + sx * 235, 560), (cx + sx * 215, 760), (cx + sx * 185, 820),
                      (cx + sx * 170, 700), (cx + sx * 180, 520)], 8), fill=HAIR)
# блики на волосах
for dx in (-110, -60, 40, 95):
    p.arc((cx + dx - 70, 250, cx + dx + 70, 330), 200, 260, fill=HAIR_L, width=7)

# ---------- обезьяньи ушки — торчат из волос по бокам, над наушниками ----------
for sx in (-1, 1):
    ex, ey = cx + sx * 318, 360
    p.ellipse((ex - 78, ey - 74, ex + 78, ey + 78), fill=FUR)
    p.ellipse((ex - 46, ey - 42, ex + 46, ey + 50), fill=FUR_IN)
    p.arc((ex - 78, ey - 74, ex + 78, ey + 78), 0, 360, fill=(110, 68, 50), width=5)

# ---------- наушники и микрофон ----------
p.arc((cx - 300, 170, cx + 300, 640), 200, 340, fill=PURPLE, width=40)
for sx in (-1, 1):
    hx = cx + sx * 262
    p.rounded_rectangle((hx - 46, 440, hx + 46, 600), radius=36, fill=PURPLE)
    p.rounded_rectangle((hx - 30, 460, hx + 30, 580), radius=24, fill=PURPLE_L)
p.line((cx - 270, 590, cx - 230, 700, cx - 110, 735), fill=PURPLE, width=13, joint="curve")
p.ellipse((cx - 135, 712, cx - 90, 757), fill=(70, 40, 130))

# ---------- бантик со звездой ----------
bx, by = cx + 150, 245
p.polygon([(bx, by), (bx - 100, by - 65), (bx - 108, by + 58)], fill=(180, 95, 255))
p.polygon([(bx, by), (bx + 100, by - 65), (bx + 108, by + 58)], fill=(180, 95, 255))
p.ellipse((bx - 28, by - 28, bx + 28, by + 28), fill=(220, 170, 255))
p.polygon([(bx + 34 * math.cos(math.radians(-90 + i * 45)) * (1 if i % 2 == 0 else 0.38),
            by + 34 * math.sin(math.radians(-90 + i * 45)) * (1 if i % 2 == 0 else 0.38)) for i in range(8)],
          fill=(255, 255, 255))

# ---------- хвостик мартышки у плеча ----------
tail = [(cx + 300, 1010), (cx + 360, 930), (cx + 395, 840), (cx + 385, 770), (cx + 345, 745), (cx + 315, 770),
        (cx + 325, 805), (cx + 352, 800)]
for i in range(len(tail) - 1):                                   # хвост загибается спиралькой на конце
    w = int(30 - i * 2.5)
    p.line((tail[i], tail[i + 1]), fill=FUR, width=w)
    p.ellipse((tail[i + 1][0] - w / 2, tail[i + 1][1] - w / 2, tail[i + 1][0] + w / 2, tail[i + 1][1] + w / 2),
              fill=FUR)

# сборка: тень, персонаж, светящаяся рамка
sh = Image.new("RGBA", (S, S), (10, 0, 30, 0))
sh.putalpha(L.split()[3].filter(ImageFilter.GaussianBlur(25)).point(lambda v: int(v * 0.45)))
img.alpha_composite(sh, (0, 16))
img.alpha_composite(L)
ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(ring).ellipse((24, 24, S - 24, S - 24), outline=(185, 125, 255, 210), width=10)
img.alpha_composite(ring.filter(ImageFilter.GaussianBlur(3)))
img.convert("RGB").resize((512, 512), Image.LANCZOS).save(OUT)
print("ok", OUT)
