# -*- coding: utf-8 -*-
"""vision_agent.py — зрение для ИИ.

Позволяет ИИ «видеть» картинки:
- анализ через imgsense (цвета, яркость, области, текст если есть Tesseract)
- полноценное описание сцены, если включена vision LLM (Ollama + llava и т.п.)
- сохранение увиденного в базу знаний
- API для просмотра по URL, base64 или локальному файлу
"""
import base64
import os
import re
import time
import store


def enabled():
    """Зрение доступно всегда (базовый анализ), полное — если vision_llm_on."""
    try:
        s = store.settings()
        return {
            "basic": True,
            "full_llm": bool(s.get("vision_llm_on")),
            "url": s.get("llm_answer_url") or "",
            "model": s.get("llm_answer_model") or "",
        }
    except Exception:
        return {"basic": True, "full_llm": False}


def see(image_bytes=None, url=None, b64=None, filename="image.jpg", why="зрение"):
    """Посмотреть картинку и вернуть описание. Сохраняет в KB."""
    raw = b""
    if image_bytes:
        raw = image_bytes
    elif b64:
        try:
            raw = base64.b64decode(re.sub(r"^data:[^;,]*;base64,", "", str(b64)), validate=True)
        except Exception:
            return {"ok": False, "error": "некорректный base64"}
    elif url:
        try:
            import webfetch
            ok, body = webfetch._fetch_bytes(url, timeout=15, max_bytes=8_000_000)
            if not ok or not body:
                return {"ok": False, "error": "не удалось скачать изображение"}
            raw = body
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
    else:
        return {"ok": False, "error": "нет изображения"}

    if not raw or len(raw) > 8_000_000:
        return {"ok": False, "error": "нужна картинка размером до 8 МБ", "saved": False}
    try:
        import imgsense
        result = imgsense.describe(raw, name=filename, question=why, structured=True)
        result["bytes"] = len(raw)
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "saved": False}


def _format_desc(desc, why):
    if not isinstance(desc, dict):
        return str(desc)
    lines = [f"Источник: {why}", ""]
    if desc.get("error"):
        lines.append("Ошибка: " + desc["error"])
        return "\n".join(lines)
    if desc.get("caption"):
        lines.append("Описание: " + desc["caption"])
    if desc.get("text"):
        lines.append("Текст на картинке: " + desc["text"][:500])
    if desc.get("colors"):
        lines.append("Основные цвета: " + ", ".join(
            f"{c} ({int(p*100)}%)" for c, p in desc.get("colors", [])[:4]))
    if desc.get("brightness") is not None:
        lines.append(f"Яркость: {desc['brightness']:.2f}, контраст: {desc.get('contrast', 0):.2f}")
    if desc.get("size"):
        lines.append(f"Размер: {desc['size']}")
    if desc.get("regions"):
        lines.append("Области: " + str(desc["regions"])[:300])
    return "\n".join(lines)


def see_url(url, why="зрение по ссылке"):
    return see(url=url, why=why)


def see_b64(b64, filename="upload.jpg", why="загрузка"):
    return see(b64=b64, filename=filename, why=why)


def status():
    st = enabled()
    import shutil
    st["ocr_engine"] = bool(shutil.which("tesseract"))
    st["zone"] = "Камера"
    st["architecture"] = "визуальный адаптер к текстовой LLM; зрительная модель подключается отдельно"
    st["pillow"] = False
    try:
        from PIL import Image
        st["pillow"] = True
    except Exception:
        pass
    return st


def seed_instructions():
    """Посеять инструкции по зрению в KB."""
    try:
        import kb
        text = (
            "Зрение ИИ: облако может анализировать изображения через imgsense. "
            "Базовый анализ (цвета, яркость, области, размер) работает всегда при наличии Pillow. "
            "Полноценное описание сцены («что на картинке») доступно, если в настройках включена "
            "vision LLM (Ollama + llava или аналог: vision_llm_on + llm_answer_url). "
            "Увиденное сохраняется в базу знаний. "
            "Для сериалов и видео можно извлекать кадры и описывать их."
        )
        kb.add_article("Зрение облака", text, source="vision_seed")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
