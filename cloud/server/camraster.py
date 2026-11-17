# -*- coding: utf-8 -*-
# camraster.py — кадр камеры -> растр 16x12 серых точек для пина D6 (v3.52).
# Плата (neuro_12 v2.48) шлёт JPEG кадра ESP32-CAM; у неё нет декодера JPEG,
# поэтому уменьшает его облако (нужен Pillow). Ответ: hex, 2 знака на точку,
# уровень 1..254 (0 и 255 — служебные: конец строки / начало-конец кадра).
import base64
import io


def raster(img_b64, w=16, h=12):
    try:
        from PIL import Image, ImageOps
    except Exception:
        return {"ok": False, "error": "на сервере нет Pillow (pip install pillow)"}
    try:
        raw = base64.b64decode(img_b64 or "")
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as e:
        return {"ok": False, "error": "кадр не открылся: %s" % e}
    w, h = max(2, min(64, int(w))), max(2, min(48, int(h)))
    g = ImageOps.autocontrast(im.convert("L").resize((w, h), Image.BOX))
    px = [1 + (v * 253) // 255 for v in g.getdata()]
    return {"ok": True, "w": w, "h": h, "hex": "".join("%02x" % p for p in px)}
