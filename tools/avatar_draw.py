"""Аватарка Мартышки для Говорилки: девушка-мартышка в фиолетовой гамме, наушники, бантик со звездой."""
import math, random, sys
from PIL import Image, ImageDraw, ImageFilter

S = 1024
OUT = sys.argv[1] if len(sys.argv) > 1 else "tools/martyshka_avatar.png"   # потом: в data/govorilka_bot/appdata/Govorilka/avatar.jpg (256×256)
random.seed(7)

# ---------- фон: тёмно-фиолетовый с мягким светом и точками, как у шара ----------
bg = Image.new("RGB", (S, S), (14, 10, 28))
glow = Image.new("RGB", (S, S), (0, 0, 0))
g = ImageDraw.Draw(glow)
for r, col in ((520, (60, 30, 110)), (420, (90, 45, 160)), (300, (120, 60, 200))):
    g.ellipse((S/2 - r, S/2 - r + 30, S/2 + r, S/2 + r + 30), fill=col)
glow = glow.filter(ImageFilter.GaussianBlur(120))
bg = Image.blend(bg, glow, 0.75)
dots = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(dots)
for _ in range(1400):                                     # точки по кольцу вокруг головы
    a = random.uniform(0, 2 * math.pi)
    rr = random.gauss(450, 28)
    x, y = S/2 + rr * math.cos(a), S/2 + 20 + rr * math.sin(a)
    s = random.uniform(1.5, 4.5)
    c = random.choice([(180, 130, 255), (140, 110, 255), (230, 190, 255)])
    d.ellipse((x - s, y - s, x + s, y + s), fill=c + (random.randint(90, 230),))
dots = dots.filter(ImageFilter.GaussianBlur(0.8))
img = bg.convert("RGBA")
img.alpha_composite(dots)

# ---------- персонаж ----------
L = Image.new("RGBA", (S, S), (0, 0, 0, 0))
p = ImageDraw.Draw(L)
fur, fur_dark = (150, 96, 70), (112, 68, 50)
face, face_sh = (247, 214, 190), (232, 188, 165)
cx, cy = S // 2, 560

# дужка наушников (за головой)
p.arc((cx - 330, cy - 390, cx + 330, cy + 260), 195, 345, fill=(120, 70, 220), width=46)
# уши
for sx in (-1, 1):
    ex = cx + sx * 300
    p.ellipse((ex - 105, cy - 95, ex + 105, cy + 105), fill=fur)
    p.ellipse((ex - 62, cy - 55, ex + 62, cy + 65), fill=(240, 180, 175))
# голова
p.ellipse((cx - 280, cy - 300, cx + 280, cy + 250), fill=fur)
# чёлка-хохолок
for dx, h in ((-60, 70), (0, 95), (60, 70)):
    p.ellipse((cx + dx - 70, cy - 330 - h // 3, cx + dx + 70, cy - 230), fill=fur_dark)
# лицо (сердечком: два круга сверху + овал снизу)
p.ellipse((cx - 225, cy - 200, cx - 5, cy + 30), fill=face)
p.ellipse((cx + 5, cy - 200, cx + 225, cy + 30), fill=face)
p.ellipse((cx - 190, cy - 60, cx + 190, cy + 225), fill=face)
# глаза
for sx in (-1, 1):
    ex, ey = cx + sx * 105, cy - 70
    p.ellipse((ex - 62, ey - 72, ex + 62, ey + 72), fill=(255, 255, 255))
    p.ellipse((ex - 46, ey - 52, ex + 46, ey + 58), fill=(110, 60, 210))      # фиолетовая радужка
    p.ellipse((ex - 26, ey - 26, ex + 26, ey + 36), fill=(30, 16, 60))
    p.ellipse((ex - 30 + sx * 8, ey - 42, ex - 6 + sx * 8, ey - 18), fill=(255, 255, 255))
    p.ellipse((ex + 10, ey + 14, ex + 22, ey + 26), fill=(255, 255, 255, 220))
    # ресницы — три изогнутые сверху снаружи
    p.arc((ex - 66, ey - 78, ex + 66, ey + 70), 200, 340, fill=(40, 22, 40), width=10)
    for k, ang in enumerate((-35, -15, 5)):
        a = math.radians(ang if sx > 0 else 180 - ang)
        bx, by = ex + sx * 50, ey - 50 + k * 14
        p.line((bx, by, bx + sx * 34, by - 26 + k * 6), fill=(40, 22, 40), width=9)
    # бровь
    p.arc((ex - 55, ey - 125, ex + 55, ey - 70), 205, 335, fill=fur_dark, width=11)
# румянец
blush = Image.new("RGBA", (S, S), (0, 0, 0, 0))
bd = ImageDraw.Draw(blush)
for sx in (-1, 1):
    bx = cx + sx * 150
    bd.ellipse((bx - 55, cy + 30, bx + 55, cy + 80), fill=(255, 120, 160, 150))
blush = blush.filter(ImageFilter.GaussianBlur(14))
L.alpha_composite(blush)
p = ImageDraw.Draw(L)
# носик
p.ellipse((cx - 16, cy + 20, cx - 4, cy + 34), fill=(120, 70, 60))
p.ellipse((cx + 4, cy + 20, cx + 16, cy + 34), fill=(120, 70, 60))
# губки-улыбка
p.chord((cx - 70, cy + 40, cx + 70, cy + 140), 10, 170, fill=(226, 70, 130))
p.chord((cx - 52, cy + 40, cx + 52, cy + 100), 20, 160, fill=face)
p.ellipse((cx - 22, cy + 108, cx + 6, cy + 122), fill=(255, 150, 190))
# наушники-чашки
for sx in (-1, 1):
    hx = cx + sx * 318
    p.rounded_rectangle((hx - 52, cy - 120, hx + 52, cy + 40), radius=40, fill=(130, 80, 235))
    p.rounded_rectangle((hx - 34, cy - 98, hx + 34, cy + 18), radius=28, fill=(170, 125, 255))
# микрофон к губам (слева)
p.line((cx - 330, cy + 10, cx - 250, cy + 150, cx - 120, cy + 175), fill=(120, 70, 220), width=16, joint="curve")
p.ellipse((cx - 150, cy + 150, cx - 98, cy + 200), fill=(60, 35, 120))
# бантик со звездой
bx, by = cx + 170, cy - 300
p.polygon([(bx, by), (bx - 105, by - 70), (bx - 115, by + 60)], fill=(170, 90, 255))
p.polygon([(bx, by), (bx + 105, by - 70), (bx + 115, by + 60)], fill=(170, 90, 255))
p.ellipse((bx - 30, by - 30, bx + 30, by + 30), fill=(215, 160, 255))
star = [(bx + 36 * math.cos(math.radians(-90 + i * 45)) * (1 if i % 2 == 0 else 0.38),
         by + 36 * math.sin(math.radians(-90 + i * 45)) * (1 if i % 2 == 0 else 0.38)) for i in range(8)]
p.polygon(star, fill=(255, 255, 255))

# мягкая тень персонажа и сборка
shadow = L.split()[3].filter(ImageFilter.GaussianBlur(25)).point(lambda v: int(v * 0.5))
sh = Image.new("RGBA", (S, S), (10, 0, 30, 0)); sh.putalpha(shadow)
img.alpha_composite(sh, (0, 18))
img.alpha_composite(L)

# круглая рамка-свечение
ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(ring).ellipse((24, 24, S - 24, S - 24), outline=(180, 120, 255, 200), width=10)
img.alpha_composite(ring.filter(ImageFilter.GaussianBlur(3)))

img.convert("RGB").resize((512, 512), Image.LANCZOS).save(OUT)
print("ok", OUT)
