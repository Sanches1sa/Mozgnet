# -*- coding: utf-8 -*-
"""Сборка и ПРОВЕРКА программных уроков: python tools/build_code_lessons.py
Каждый шаблон + его assert-тест запускается в чистом подпроцессе (python -I).
Пишет server/codelessons.json и ../board/python/code_lessons.json."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from code_lessons_a import A  # noqa: E402
from code_lessons_b import B  # noqa: E402


def check(item):
    src = item.get("fixcode") or item["code"]
    prog = src + "\n\n# --- проверка ---\n" + item["test"]
    d = tempfile.mkdtemp(prefix="cl_")
    p = os.path.join(d, "t.py")
    open(p, "w", encoding="utf-8").write(prog)
    try:
        r = subprocess.run([sys.executable, "-I", p], cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        return r.returncode == 0, r.stdout.decode("utf-8", "replace")[-600:]
    except subprocess.TimeoutExpired:
        return False, "таймаут"


def main():
    items = A + B
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids)), "дубли id"
    bad = 0
    for it in items:
        ok, out = check(it)
        it["tested"] = bool(ok)
        if not ok:
            bad += 1
            print("ПРОВАЛ", it["id"], out)
    if bad:
        sys.exit(1)
    for it in items:
        it["tested_on"] = "python %d.%d, linux, при сборке" % sys.version_info[:2]
    data = json.dumps(items, ensure_ascii=False, indent=1)
    for path in (os.path.join(HERE, "..", "server", "codelessons.json"),
                 os.path.join(HERE, "..", "..", "board", "python", "code_lessons.json")):
        open(path, "w", encoding="utf-8").write(data)
    print("уроков:", len(items), "все проверены")


if __name__ == "__main__":
    main()
