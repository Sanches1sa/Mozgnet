# -*- coding: utf-8 -*-
# ===========================================================================
# imgsense.py — ПОНИМАНИЕ КАРТИНОК (v3.30)
# ---------------------------------------------------------------------------
# Заявка владельца: «научи бота и платформу понимать картинки».
# ЧЕСТНО о возможностях без нейросети распознавания:
#   1) всегда (нужен Pillow): размер и ориентация, яркость и контраст,
#      основные цвета словами, «что где» по областям (сверху голубое и
#      светлое — похоже на небо, снизу зелёное — на траву/лес), похоже ли
#      это на скриншот/документ с текстом или на фотографию, много ли
#      мелких деталей, есть ли тона кожи (ВОЗМОЖНО, люди);
#   2) текст на картинке — если на сервере стоит Tesseract (pytesseract);
#   3) настоящее описание сцены («на фото кот на диване») — только если
#      владелец подключил локальную модель со зрением (Ollama + llava и
#      т.п.: vision_llm_on + llm_answer_url) — облако её не скачивает.
# Описание сохраняется статьёй базы знаний (облако помнит, что видело).
# ===========================================================================
import base64
import io
import json
import urllib.request

import store

_NAMES = [  # (имя, проверка по HSV)
    ("чёрный", lambda h, s, v: v < 0.16),
    ("белый", lambda h, s, v: v > 0.88 and s < 0.12),
    ("серый", lambda h, s, v: s < 0.14),
    ("коричневый", lambda h, s, v: (h < 40 or h > 345) and v < 0.55),
    ("красный", lambda h, s, v: h < 15 or h >= 345),
    ("оранжевый", lambda h, s, v: h < 40),
    ("жёлтый", lambda h, s, v: h < 70),
    ("зелёный", lambda h, s, v: h < 165),
    ("голубой", lambda h, s, v: h < 200),
    ("синий", lambda h, s, v: h < 255),
    ("фиолетовый", lambda h, s, v: h < 290),
    ("розовый", lambda h, s, v: True),
]


def _hsv(r, g, b):
    import colorsys
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return h * 360.0, s, v


def _cname(rgb):
    h, s, v = _hsv(*rgb)
    for name, ok in _NAMES:
        if ok(h, s, v):
            return name
    return "розовый"


def _region(px, w, y0, y1):
    cnt = {}
    n = 0
    for y in range(y0, y1):
        for x in range(w):
            c = _cname(px[y * w + x])
            cnt[c] = cnt.get(c, 0) + 1
            n += 1
    top = sorted(cnt.items(), key=lambda kv: -kv[1])
    return top, n


def analyze(raw):
    """Байты картинки -> dict признаков | {"error"}."""
    try:
        from PIL import Image, ImageFilter, ImageStat
    except Exception:
        return {"error": "на сервере нет Pillow (pip install pillow) — "
                         "картинки разобрать нечем"}
    try:
        im = Image.open(io.BytesIO(raw))
        if im.width * im.height > 20_000_000:
            return {"error": "изображение больше 20 миллионов пикселей"}
        im.load()
    except Exception as e:
        return {"error": "картинка не открылась: %s" % e}
    W, H = im.size
    if W * H > 20_000_000:
        return {"error": "изображение больше 20 миллионов пикселей"}
    rgb = im.convert("RGB")
    small = rgb.resize((48, 48))
    px = list(small.getdata())
    gray = rgb.convert("L")
    st = ImageStat.Stat(gray)
    bright, contrast = st.mean[0] / 255.0, st.stddev[0] / 255.0
    top, n = _region(px, 48, 0, 48)
    colors = [(c, k / float(n)) for c, k in top[:4] if k / float(n) >= 0.06]
    up, nu = _region(px, 48, 0, 16)
    down, nd = _region(px, 48, 32, 48)
    edges = gray.resize((min(W, 320), max(1, int(H * min(W, 320) / W)))) \
        .filter(ImageFilter.FIND_EDGES)
    ep = list(edges.getdata())
    detail = sum(1 for v in ep if v > 60) / float(len(ep) or 1)
    # «чернила» — по уменьшенной до 320 px копии: тонкие буквы на 48×48
    # усредняются в серое и пропадают
    g320 = list(gray.resize((min(W, 320), max(1, int(H * min(W, 320) / W))))
                .getdata())
    ink = sum(1 for v in g320 if v < 110) / float(len(g320) or 1)
    paper = sum(1 for v in g320 if v > 225) / float(len(g320) or 1)
    whites = sum(1 for p in px if min(p) > 225) / float(len(px))
    darks = sum(1 for p in px if max(p) < 70) / float(len(px))
    skin = 0
    for r, g, b in px:
        if r > 95 and g > 40 and b > 20 and r > g and r > b and \
                abs(r - g) > 15 and max(r, g, b) - min(r, g, b) > 15:
            skin += 1
    skin /= float(len(px))
    uniq = len(set((r // 32, g // 32, b // 32) for r, g, b in px))
    out = {"w": W, "h": H, "bright": bright, "contrast": contrast,
           "colors": colors, "top": up[:2], "bottom": down[:2],
           "detail": detail, "whites": whites, "darks": darks,
           "skin": skin, "palette": uniq, "text": "", "ink": ink,
           "paper": paper}
    # The own head is preferred once trained; never use Tesseract as its labels.
    out['ocr_engine'] = ''
    try:
        import photo_ocr
        own=photo_ocr.read(raw)
        if own:
            out['text']=own['text'];out['ocr_engine']='own_pixel_mlp'
            out['ocr_confidence']=own['confidence']
            return out
    except Exception as exc:
        out['own_ocr_error']=str(exc)[:200]
    try:                                    # текст на картинке — если есть
        import pytesseract
        out["text"] = " ".join(pytesseract.image_to_string(
            rgb, lang="rus+eng", timeout=20).split())[:1500]
        if out['text']:out['ocr_engine']='tesseract'
    except Exception:
        pass
    return out


def _llm_vision(raw, question=""):
    """Описание сцены локальной моделью со зрением (если подключена)."""
    s = store.settings()
    url = str(s.get("llm_answer_url") or "").rstrip("/")
    if not (s.get("vision_llm_on") and url):
        return ""
    mime = "image/png" if raw.startswith(b"\x89PNG") else "image/webp" if raw[:4] == b"RIFF" else "image/gif" if raw.startswith(b"GIF8") else "image/jpeg"
    body = {"model": s.get("llm_answer_model") or "llava",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": question or
                 "Опиши по-русски, что на картинке, коротко и по делу."},
                {"type": "image_url", "image_url": {
                    "url": "data:" + mime + ";base64," +
                    base64.b64encode(raw).decode()}}]}],
            "temperature": 0.2}
    try:
        req = urllib.request.Request(
            url + ("/chat/completions" if not url.endswith("completions")
                   else ""), data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return str(d["choices"][0]["message"]["content"]).strip()[:3000]
    except Exception as e:
        store.log("КАРТИНКИ", "модель со зрением не ответила: %s" % e)
        return ""


def describe(raw, name="картинка", question="", learn=True, structured=False):
    """Картинка -> текст для человека (+ статья в базе знаний)."""
    a = analyze(raw)
    if a.get("error"):
        message = "Картинку «%s» разобрать не смогла: %s." % (name, a["error"])
        return {"ok": False, "error": a["error"], "text": message, "saved": False} if structured else message
    lines = []
    orient = "горизонтальная" if a["w"] > a["h"] * 1.15 else (
        "вертикальная" if a["h"] > a["w"] * 1.15 else "почти квадратная")
    tone = "светлая" if a["bright"] > 0.62 else ("тёмная" if a["bright"] <
                                                  0.33 else "средней яркости")
    cont = "контрастная" if a["contrast"] > 0.27 else (
        "мягкая, малоконтрастная" if a["contrast"] < 0.12 else "")
    lines.append("Картинка %d×%d, %s, %s%s." % (a["w"], a["h"], orient, tone,
                                               (", " + cont) if cont else ""))
    if a["colors"]:
        lines.append("Основные цвета: %s." % ", ".join(
            "%s (%d%%)" % (c, round(p * 100)) for c, p in a["colors"]))
    guess = []
    tc = dict(a["top"]) if a["top"] else {}
    bc = dict(a["bottom"]) if a["bottom"] else {}
    if (tc.get("голубой", 0) + tc.get("синий", 0) + tc.get("белый", 0)) \
            > 0.45 * 768 and a["bright"] > 0.45 and a["whites"] < 0.6:
        guess.append("сверху светлое голубое — похоже на небо")
    if bc.get("зелёный", 0) > 0.35 * 768:
        guess.append("снизу много зелёного — трава, листва или лес")
    if bc.get("синий", 0) + bc.get("голубой", 0) > 0.4 * 768:
        guess.append("снизу синее — возможно, вода")
    kind = ""
    if a["paper"] > 0.55 and 0.01 < a["ink"] < 0.35 and a["palette"] < 40:
        kind = ("похоже на документ или скриншот с текстом (светлый фон, "
                "тёмные мелкие знаки)")
    elif a["palette"] < 18:
        kind = "похоже на рисунок, схему или интерфейс (мало цветов)"
    elif a["detail"] > 0.18:
        kind = "фотография с множеством мелких деталей"
    else:
        kind = "похоже на фотографию"
    lines.append("Что это: %s." % kind)
    if guess:
        lines.append("По областям: %s." % "; ".join(guess))
    if a["skin"] > 0.12 and "документ" not in kind:
        lines.append("Много тонов кожи (%d%%) — возможно, на снимке люди "
                     "или лицо (это догадка по цвету, а не распознавание)."
                     % round(a["skin"] * 100))
    if a["text"]:
        lines.append("Текст на картинке (%s; возможны ошибки): «%s»." % (a.get("ocr_engine") or "OCR", a["text"][:1500]))
    scene = _llm_vision(raw, question)
    if scene:
        lines.insert(0, "Описание сцены (локальная модель со зрением): %s"
                     % scene)
    else:
        lines.append("Чего не умею: узнавать предметы и людей по форме — "
                     "для этого подключи локальную модель со зрением "
                     "(Настройки → API и ответы).")
    text = "\n".join(lines)
    # A visual adapter supplies evidence to the text model; it does not turn
    # existing text weights into an image encoder.
    zone_active = False
    if store.settings().get("llm_zones_on", True):
        try:
            import llm_zones
            llm_zones.route(text, zone="Камера")
            zone_active = True
            if learn:
                llm_zones.observe(text, zone="Камера", source="photo_adapter")
        except Exception:
            pass
    saved = False
    if learn:
        try:
            import kb
            result = kb.add_article("Картинка: %s" % str(name)[:150], text,
                           source="картинка %s" % str(name)[:80])
            saved = bool(isinstance(result, dict) and result.get("ok"))
        except Exception:
            pass
    if structured:
        return dict(ok=True, text=text, saved=saved, zone="Камера",
                    zone_active=zone_active, description=dict(
                        size=[a["w"], a["h"]], colors=a["colors"],
                        brightness=a["bright"], contrast=a["contrast"],
                        text=a["text"], caption=scene, ocr_engine=a.get("ocr_engine", "")),
                    capabilities=dict(basic=True, ocr=bool(a["text"]), scene=bool(scene)),
                    adapter="visual evidence to shared text LLM; no image weights trained")
    return text


def is_image(name, raw=b""):
    n = str(name or "").lower()
    return n.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")) or \
        raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n"
