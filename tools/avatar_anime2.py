"""Аватарка Мартышки — полуреалистичное аниме: сиреневые волосы, чёлка на один глаз, светящийся глаз,
заколки-звёзды ✦, тёмный наряд с высоким воротником, фон-шар из точек. Рисуется кодом.
Запуск: .venv\\Scripts\\python.exe tools\\avatar_anime2.py [куда.png]"""
import math
import random
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024
OUT = sys.argv[1] if len(sys.argv) > 1 else "tools/martyshka_avatar_anime2.png"
random.seed(5)


def layer():
    return Image.new("RGBA", (S, S), (0, 0, 0, 0))


def smooth(pts, steps=14, closed=True):
    out, n = [], len(pts)
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        p0, p1 = pts[i - 1] if (closed or i) else pts[0], pts[i]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if (closed or i + 2 < n) else pts[-1]
        for t in range(steps):
            t /= steps
            out.append(tuple(0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t * t
                                    + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t ** 3) for k in range(2)))
    if not closed:
        out.append(pts[-1])
    return out


def grad_fill(mask, top, bottom, y0=0, y1=S):
    """Залить маску вертикальным градиентом."""
    g = Image.new("RGBA", (1, S))
    for y in range(S):
        t = min(1, max(0, (y - y0) / max(1, y1 - y0)))
        g.putpixel((0, y), tuple(int(top[k] + (bottom[k] - top[k]) * t) for k in range(3)) + (255,))
    g = g.resize((S, S))
    out = layer()
    out.paste(g, (0, 0), mask)
    return out


def mask_of(draw_fn, blur=0):
    m = Image.new("L", (S, S), 0)
    draw_fn(ImageDraw.Draw(m))
    return m.filter(ImageFilter.GaussianBlur(blur)) if blur else m


def strands(d, x0, y0, x1, y1, n, col_a, col_b, width=(2, 5), bend=40):
    for _ in range(n):
        sx = random.uniform(x0, x1)
        pts = [(sx, y0), (sx + random.uniform(-bend, bend), (y0 + y1) / 2),
               (sx + random.uniform(-bend * 1.5, bend * 1.5), y1 + random.uniform(-60, 40))]
        c = tuple(int(col_a[k] + (col_b[k] - col_a[k]) * random.random()) for k in range(3))
        d.line(smooth(pts, 10, closed=False), fill=c + (random.randint(50, 120),),
               width=random.randint(*width))


cx = S // 2 + 10
# ---------- фон ----------
img = Image.new("RGBA", (S, S), (12, 10, 26, 255))
glow = layer()
gd = ImageDraw.Draw(glow)
for r, c in ((600, (40, 28, 90, 255)), (420, (85, 55, 160, 255)), (250, (140, 100, 220, 255))):
    gd.ellipse((S / 2 - r, S / 2 - r - 40, S / 2 + r, S / 2 + r - 40), fill=c)
img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(150)))
dots = layer()
dd = ImageDraw.Draw(dots)
for _ in range(1600):
    a, rr = random.uniform(0, 2 * math.pi), random.gauss(480, 32)
    x, y, s = S / 2 + rr * math.cos(a), S / 2 + rr * math.sin(a), random.uniform(1.2, 4)
    dd.ellipse((x - s, y - s, x + s, y + s), fill=random.choice([(190, 150, 255), (150, 130, 255), (230, 210, 255)])
               + (random.randint(80, 220),))
img.alpha_composite(dots.filter(ImageFilter.GaussianBlur(0.7)))

HAIR_T, HAIR_B = (232, 232, 250), (168, 160, 222)       # серебристо-сиреневые
HAIR_SH = (120, 112, 190)
SKIN, SKIN_SH = (250, 232, 228), (222, 196, 205)

# ---------- волосы сзади ----------
back = [(cx - 260, 300), (cx - 330, 520), (cx - 350, 820), (cx - 300, 1024), (cx + 300, 1024), (cx + 350, 800),
        (cx + 320, 500), (cx + 250, 290), (cx, 170)]
m_back = mask_of(lambda d: d.polygon(smooth(back), fill=255), 1)
img.alpha_composite(grad_fill(m_back, (200, 198, 238), HAIR_SH, 200, 1000))
st = layer()
strands(ImageDraw.Draw(st), cx - 320, 300, cx + 320, 980, 120, (150, 140, 210), (235, 232, 252), (2, 5), 50)
st.putalpha(ImageChops.multiply(st.split()[3], m_back))
img.alpha_composite(st.filter(ImageFilter.GaussianBlur(0.8)))

# ---------- наряд: тёмный, высокий воротник, фиолетовая окантовка ----------
body = smooth([(cx - 380, 1024), (cx - 330, 880), (cx - 150, 800), (cx + 150, 800), (cx + 330, 880), (cx + 380, 1024)], 10)
m_body = mask_of(lambda d: d.polygon(body, fill=255))
img.alpha_composite(grad_fill(m_body, (52, 44, 84), (22, 18, 40), 780, 1024))
neck = mask_of(lambda d: d.rounded_rectangle((cx - 52, 640, cx + 52, 820), radius=26, fill=255))
img.alpha_composite(grad_fill(neck, SKIN_SH, (200, 170, 185), 640, 820))
collar = smooth([(cx - 110, 760), (cx - 95, 690), (cx, 715), (cx + 95, 690), (cx + 110, 760), (cx, 800)], 8)
m_col = mask_of(lambda d: d.polygon(collar, fill=255))
img.alpha_composite(grad_fill(m_col, (60, 50, 96), (30, 24, 52), 690, 800))
trim = layer()
td = ImageDraw.Draw(trim)
td.line(collar + [collar[0]], fill=(165, 120, 255, 230), width=5)
td.line(smooth([(cx - 330, 900), (cx - 160, 830), (cx, 860), (cx + 160, 830), (cx + 330, 900)], 8, closed=False),
        fill=(150, 105, 240, 200), width=6)
img.alpha_composite(trim)
# брошь-звезда ✦ на воротнике (светится)
def star(d, x, y, r, col, ratio=0.36):
    d.polygon([(x + r * math.cos(math.radians(-90 + i * 45)) * (1 if i % 2 == 0 else ratio),
                y + r * math.sin(math.radians(-90 + i * 45)) * (1 if i % 2 == 0 else ratio)) for i in range(8)], fill=col)
br = layer()
star(ImageDraw.Draw(br), cx, 790, 44, (200, 160, 255, 255))
img.alpha_composite(br.filter(ImageFilter.GaussianBlur(10)))
br2 = layer()
star(ImageDraw.Draw(br2), cx, 790, 30, (250, 240, 255, 255))
img.alpha_composite(br2)

# ---------- лицо ----------
face = smooth([(cx - 175, 380), (cx - 180, 520), (cx - 140, 620), (cx - 60, 690), (cx, 705), (cx + 60, 690),
               (cx + 140, 620), (cx + 180, 520), (cx + 175, 380), (cx, 320)])
m_face = mask_of(lambda d: d.polygon(face, fill=255), 1)
img.alpha_composite(grad_fill(m_face, (250, 236, 232), (240, 214, 214), 350, 700))
# мягкая тень под чёлкой и сбоку
sh = layer()
ImageDraw.Draw(sh).polygon(smooth([(cx - 180, 380), (cx + 180, 380), (cx + 160, 470), (cx - 160, 470)], 6),
                           fill=SKIN_SH + (170,))
sh = sh.filter(ImageFilter.GaussianBlur(28))
sh.putalpha(ImageChops.multiply(sh.split()[3], m_face))
img.alpha_composite(sh)
blush = layer()
bd = ImageDraw.Draw(blush)
for bx in (cx - 105, cx + 105):
    bd.ellipse((bx - 50, 575, bx + 50, 615), fill=(255, 150, 175, 110))
img.alpha_composite(blush.filter(ImageFilter.GaussianBlur(16)))

f = layer()
d = ImageDraw.Draw(f)
# видимый глаз (справа от зрителя): миндалевидный, светящаяся радужка розово-фиолетовая
ex, ey = cx + 82, 520
eye = smooth([(ex - 72, ey + 4), (ex - 40, ey - 34), (ex + 20, ey - 42), (ex + 70, ey - 18), (ex + 62, ey + 20),
              (ex + 10, ey + 38), (ex - 45, ey + 30)], 8)
d.polygon(eye, fill=(248, 244, 252))
m_eye = mask_of(lambda dd: dd.polygon(eye, fill=255))
iris = layer()
idr = ImageDraw.Draw(iris)
for i in range(26):
    t = i / 25
    c = (int(120 + 120 * t), int(40 + 40 * t), int(150 + 60 * (1 - t)))
    idr.ellipse((ex - 38 + i * 0.4, ey - 44 + i * 1.6, ex + 38 - i * 0.4, ey + 40), fill=c + (255,))
idr.ellipse((ex - 16, ey - 18, ex + 16, ey + 18), fill=(60, 10, 50, 255))
idr.ellipse((ex - 26, ey - 30, ex - 6, ey - 10), fill=(255, 255, 255, 255))
idr.ellipse((ex + 10, ey + 12, ex + 20, ey + 22), fill=(255, 230, 245, 230))
iris.putalpha(ImageChops.multiply(iris.split()[3], m_eye))
f.alpha_composite(iris)
d = ImageDraw.Draw(f)
d.line(smooth([(ex - 78, ey + 2), (ex - 42, ey - 38), (ex + 22, ey - 47), (ex + 78, ey - 22)], 10, closed=False),
       fill=(50, 34, 70), width=10)                                        # верхнее веко
d.line([(ex + 74, ey - 22), (ex + 96, ey - 34)], fill=(50, 34, 70), width=7)
d.line(smooth([(ex - 40, ey + 32), (ex + 10, ey + 40), (ex + 58, ey + 22)], 8, closed=False),
       fill=(170, 120, 150), width=3)                                      # нижнее веко
d.line(smooth([(ex - 60, ey - 82), (ex, ey - 96), (ex + 62, ey - 82)], 8, closed=False), fill=(150, 140, 200), width=6)
# глаз под чёлкой (виден чуть-чуть сквозь пряди)
ex2 = cx - 90
d.line(smooth([(ex2 - 70, ey - 20), (ex2 - 20, ey - 46), (ex2 + 40, ey - 40), (ex2 + 72, ey)], 8, closed=False),
       fill=(50, 34, 70, 140), width=9)
# нос и губы
d.line([(cx + 8, 590), (cx + 2, 606)], fill=(210, 165, 175), width=4)
d.line(smooth([(cx - 26, 648), (cx, 654), (cx + 26, 646)], 8, closed=False), fill=(190, 110, 130), width=5)
d.ellipse((cx - 8, 655, cx + 10, 662), fill=(235, 160, 175))
img.alpha_composite(f)

# ---------- чёлка: длинная, закрывает левый глаз ----------
bang = smooth([(cx - 250, 470), (cx - 245, 300), (cx - 120, 200), (cx + 40, 185), (cx + 190, 240), (cx + 250, 380),
               (cx + 220, 420), (cx + 150, 360), (cx + 60, 430), (cx + 10, 400), (cx - 20, 560), (cx - 70, 640),
               (cx - 150, 600), (cx - 200, 560)], 12)
m_bang = mask_of(lambda dd: dd.polygon(bang, fill=255), 1)
img.alpha_composite(grad_fill(m_bang, HAIR_T, HAIR_B, 180, 650))
st2 = layer()
s2 = ImageDraw.Draw(st2)
strands(s2, cx - 240, 200, cx - 20, 620, 60, (165, 155, 220), (250, 248, 255), (2, 4), 30)
strands(s2, cx - 40, 190, cx + 230, 420, 45, (165, 155, 220), (250, 248, 255), (2, 4), 25)
st2.putalpha(ImageChops.multiply(st2.split()[3], m_bang))
img.alpha_composite(st2.filter(ImageFilter.GaussianBlur(0.7)))
# боковые пряди вдоль лица
for sx, pts in ((1, [(cx + 175, 380), (cx + 215, 520), (cx + 205, 700), (cx + 230, 880), (cx + 190, 760),
                     (cx + 170, 560)]),
                (-1, [(cx - 200, 520), (cx - 230, 700), (cx - 220, 900), (cx - 260, 760), (cx - 255, 560)])):
    m_side = mask_of(lambda dd, p=pts: dd.polygon(smooth(p, 10), fill=255), 1)
    img.alpha_composite(grad_fill(m_side, HAIR_T, HAIR_SH, 380, 900))
# блик-нимб на волосах
hl = layer()
ImageDraw.Draw(hl).arc((cx - 200, 200, cx + 200, 420), 205, 330, fill=(255, 255, 255, 170), width=14)
hl = hl.filter(ImageFilter.GaussianBlur(5))
hl.putalpha(ImageChops.multiply(hl.split()[3], ImageChops.lighter(m_bang, m_back)))
img.alpha_composite(hl)

# ---------- заколки: треугольные серебряные + звезда ✦ ----------
cl = layer()
c = ImageDraw.Draw(cl)
for (x, y, a) in ((cx - 170, 300, -30), (cx - 128, 262, -25), (cx + 175, 330, 30)):
    r = 26
    pts = [(x + r * math.cos(math.radians(a + k * 120)), y + r * math.sin(math.radians(a + k * 120))) for k in range(3)]
    c.polygon(pts, fill=(70, 66, 96), outline=(230, 230, 250), width=4)
star(c, cx + 140, 255, 34, (255, 250, 255, 255))
img.alpha_composite(cl)
sg = layer()
star(ImageDraw.Draw(sg), cx + 140, 255, 50, (190, 150, 255, 255))
img.alpha_composite(sg.filter(ImageFilter.GaussianBlur(12)))
img.alpha_composite(cl)
# серёжки-подвески
for x in (cx - 190, cx + 196):
    c2 = layer()
    cd = ImageDraw.Draw(c2)
    cd.line([(x, 600), (x, 650)], fill=(220, 220, 240, 255), width=3)
    cd.polygon([(x - 14, 650), (x + 14, 650), (x, 690)], fill=(200, 170, 255, 255))
    img.alpha_composite(c2)

# ---------- рамка ----------
ring = layer()
ImageDraw.Draw(ring).ellipse((20, 20, S - 20, S - 20), outline=(185, 140, 255, 200), width=9)
img.alpha_composite(ring.filter(ImageFilter.GaussianBlur(3)))
img.convert("RGB").resize((512, 512), Image.LANCZOS).save(OUT)
print("ok", OUT)
