# -*- coding: utf-8 -*-
# Иконка «мозг» для лаунчера neuro12 (чистый Python: PNG внутри ICO).
# Запуск: python make_icon.py  ->  brain.ico (256, 64, 48, 32, 16 px)
import math
import os
import struct
import zlib


def _png(w, h, rgba):
    raw = b"".join(b"\x00" + bytes(rgba[y * w * 4:(y + 1) * w * 4])
                   for y in range(h))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _sample(x, y):
    """Цвет точки (x, y в 0..1) или None — фон."""
    cx, cy = 0.5, 0.52
    # два полушария — эллипсы с «лобной» выпуклостью
    inside, side = False, 0
    for s, ox in ((-1, 0.255), (1, 0.745)):
        dx, dy = (x - ox) / 0.245, (y - cy) / 0.36
        bump = 0.06 * math.sin(6.0 * math.atan2(dy, dx) + s)
        if dx * dx + dy * dy <= (1.0 + bump) ** 2:
            inside, side = True, s
    # мозжечок/ствол снизу
    sx, sy = (x - 0.5) / 0.06, (y - 0.86) / 0.09
    stem = sx * sx + sy * sy <= 1.0
    if not inside and not stem:
        return None
    if stem and not inside:
        return (150, 70, 160)
    # борозда между полушариями
    if abs(x - 0.5) < 0.012 and 0.2 < y < 0.86:
        return (90, 30, 110)
    # извилины: тёмные волнистые линии
    u = (x - (0.255 if side < 0 else 0.745)) * 9.0
    v = (y - cy) * 9.0
    g = math.sin(u * 1.7 + math.sin(v * 1.3) * 1.6) * \
        math.cos(v * 1.5 + math.cos(u * 1.1) * 1.4)
    t = 0.35 + 0.65 * (1.0 - y)                      # градиент сверху вниз
    base = (int(236 * t + 170 * (1 - t)), int(120 * t + 70 * (1 - t)),
            int(214 * t + 200 * (1 - t)))
    if abs(g) < 0.13:
        return (110, 40, 140)
    # блик
    if (x - 0.33) ** 2 + (y - 0.3) ** 2 < 0.004:
        return (255, 225, 250)
    return base


def draw(size, ss=3):
    px = []
    for j in range(size):
        for i in range(size):
            acc = [0, 0, 0, 0]
            for a in range(ss):
                for b in range(ss):
                    c = _sample((i + (a + 0.5) / ss) / size,
                                (j + (b + 0.5) / ss) / size)
                    if c:
                        acc[0] += c[0]
                        acc[1] += c[1]
                        acc[2] += c[2]
                        acc[3] += 255
            n = ss * ss
            k = acc[3] / 255.0
            if k:
                px += [int(acc[0] / k), int(acc[1] / k), int(acc[2] / k),
                       int(acc[3] / n)]
            else:
                px += [0, 0, 0, 0]
    return px


def main(out="brain.ico"):
    sizes = [256, 64, 48, 32, 16]
    imgs = [_png(s, s, draw(s, 2 if s >= 128 else 4)) for s in sizes]
    hdr = struct.pack("<HHH", 0, 1, len(sizes))
    off = 6 + 16 * len(sizes)
    dirs = b""
    for s, data in zip(sizes, imgs):
        dirs += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32,
                            len(data), off)
        off += len(data)
    with open(out, "wb") as f:
        f.write(hdr + dirs + b"".join(imgs))
    print("иконка:", os.path.abspath(out))


if __name__ == "__main__":
    main(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "brain.ico"))
