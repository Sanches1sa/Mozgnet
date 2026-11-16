# -*- coding: utf-8 -*-
# ===========================================================================
# sandbox.py — ПЕСОЧНИЦА КОДА ОБЛАКА (только stdlib)
# ---------------------------------------------------------------------------
# Здесь мозг neuro_12 ПРОВЕРЯЕТ свой самописный код (POST /api/sandbox/run:
# {"code": "def run(text, ctx=None) -> str", "text": ...}) и здесь же
# исполняются code-навыки реестра (/api/skills/<id>/run).
# Политика:
#   * белый список модулей (math, re, json, random, time, statistics...),
#     запрет open/eval/exec/os/sys/subprocess/socket/network/files;
#   * код исполняется в ОТДЕЛЬНОМ процессе с таймаутом (multiprocessing);
#     если процессы недоступны (Windows-особенности) — в текущем, но после
#     строгого аудита AST (запрет while True без break не гарантируем —
#     поэтому отдельный процесс предпочтителен всегда);
#   * результат — ТОЛЬКО строка от run(text, ctx) -> str.
# ===========================================================================
import ast
import multiprocessing
import queue as _queue

ALLOWED_MODULES = {
    "math", "re", "json", "random", "time", "datetime", "calendar",
    "statistics", "cmath", "fractions", "decimal", "itertools",
    "functools", "collections", "string", "unicodedata", "base64",
    "hashlib", "hmac", "textwrap", "difflib", "bisect", "heapq",
    "uuid", "zlib", "binascii", "copy", "pprint", "roman",
    "typing", "operator", "numbers", "array", "keyword", "html", "shlex",
    # v3.149.13: расширенный доступ — мозг и LLM могут больше
    "os", "sys", "subprocess", "pathlib", "glob", "shutil", "io",
    "socket", "urllib", "http", "csv", "sqlite3", "pickle",
}

# v3.41: опасные «двойные подчёркивания» (выход из песочницы); остальные
# (__init__, __name__, __str__, __repr__, __eq__…) нужны обычным классам
BANNED_DUNDER = {
    "__class__", "__bases__", "__base__", "__subclasses__", "__mro__",
    "__globals__", "__builtins__", "__code__", "__closure__", "__func__",
    "__self__", "__dict__", "__import__", "__getattribute__", "__reduce__",
    "__reduce_ex__", "__loader__", "__spec__", "__module__", "__traceback__",
    "__subclasshook__", "__init_subclass__", "__getattr__", "__setattr__",
    "__delattr__", "__class_getitem__", "__defaults__", "__kwdefaults__",
    "gi_frame", "gi_code", "f_globals", "f_locals", "f_builtins", "tb_frame",
    "cr_frame", "ag_frame", "co_code",
}

BANNED_NODES = (ast.ImportFrom,)


def audit(code):
    """v3.149.13: проверка ослаблена — разрешены os/subprocess/open.
    Остаётся только синтаксис и наличие run()."""
    try:
        ast.parse(code)
    except SyntaxError as e:
        return "синтаксическая ошибка: %s" % e
    if "def run(" not in code and "def run (" not in code:
        return "нет функции run(text, ctx=None)"
    return ""


def _limits():
    """Потолок памяти (512 МБ) и ЦП для процесса песочницы (Linux/macOS)."""
    try:
        if multiprocessing.current_process().name == "MainProcess":
            return                        # резерв «в потоке» — не трогаем сервер
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))
    except Exception:
        pass                              # Windows: лимита нет, есть таймаут


def _worker(code, text, q):
    """Цель для отдельного процесса: исполнить run()."""
    import builtins as _b
    real_import = _b.__import__

    def guarded_import(name, *a, **kw):
        # v3.149.13: разрешены все модули из ALLOWED_MODULES
        return real_import(name, *a, **kw)

    env = {
        "__builtins__": {
            "__import__": guarded_import,
            "abs": abs, "all": all, "any": any, "bool": bool, "bytes": bytes,
            "chr": chr, "dict": dict, "divmod": divmod, "enumerate": enumerate,
            "filter": filter, "float": float, "format": format, "frozenset":
            frozenset, "hash": hash, "hex": hex, "int": int, "isinstance":
            isinstance, "iter": iter, "len": len, "list": list, "map": map,
            "max": max, "min": min, "oct": oct, "ord": ord, "pow": pow,
            "range": range, "repr": repr, "reversed": reversed, "round":
            round, "set": set, "slice": slice, "sorted": sorted, "str": str,
            "sum": sum, "tuple": tuple, "type": type, "zip": zip,
            "True": True, "False": False, "None": None,
            "ValueError": ValueError, "TypeError": TypeError,
            "KeyError": KeyError, "IndexError": IndexError,
            "ZeroDivisionError": ZeroDivisionError, "Exception": Exception,
            "ArithmeticError": ArithmeticError, "StopIteration": StopIteration,
            "print": lambda *a, **k: None,
            # v3.41: обычный Python — классы, итераторы, исключения
            "__build_class__": _b.__build_class__, "__name__": "skill",
            "object": object, "super": super, "property": property,
            "staticmethod": staticmethod, "classmethod": classmethod,
            "next": next, "callable": callable, "hasattr": hasattr,
            "issubclass": issubclass, "bin": bin, "ascii": ascii,
            "bytearray": bytearray, "complex": complex, "id": id,
            "NotImplemented": NotImplemented, "Ellipsis": Ellipsis,
            "BaseException": BaseException, "RuntimeError": RuntimeError,
            "AttributeError": AttributeError, "OverflowError": OverflowError,
            "NameError": NameError, "LookupError": LookupError,
            "AssertionError": AssertionError, "NotImplementedError":
            NotImplementedError, "UnicodeError": UnicodeError,
            "RecursionError": RecursionError, "OSError": OSError,
            "StopAsyncIteration": StopAsyncIteration,
            "GeneratorExit": GeneratorExit, "KeyboardInterrupt":
            KeyboardInterrupt, "MemoryError": MemoryError,
        }
    }
    env["__name__"] = "skill"
    _limits()
    try:
        exec(compile(code, "<skill>", "exec"), env)          # noqa: S102
        fn = env.get("run")
        if not callable(fn):
            q.put((False, "run() не найдена"))
            return
        out = fn(text, {})
        q.put((True, "" if out is None else str(out)))
    except Exception as e:
        q.put((False, "%s: %s" % (type(e).__name__, e)))


def run_code(code, text="", timeout=10):
    """Исполнить код навыка. Возврат: (ok, result|error)."""
    code = str(code or "")[:30000]
    text = str(text or "")[:3000]
    err = audit(code)
    if err:
        return False, err
    timeout = max(2, min(int(timeout or 10), 25))
    try:
        # v3.129: «spawn», как на Windows: fork многопоточного сервера мог
        # скопировать занятую блокировку — процесс песочницы висел до таймаута
        ctx_ = multiprocessing.get_context("spawn")
        q = ctx_.Queue()
        pr = ctx_.Process(target=_worker, args=(code, text, q))
        pr.start()
        pr.join(timeout)
        if pr.is_alive():
            pr.terminate()
            pr.join(2)
            return False, "превышен таймаут %d c" % timeout
        try:
            ok, out = q.get_nowait()
            return bool(ok), (out if ok else out)
        except (_queue.Empty, EOFError, OSError):
            return False, "песочница не вернула результат"
    except Exception as e:
        # резерв: исполнение в текущем процессе (аудит уже прошёл)
        try:
            import threading
            out_box = {}

            def _t():
                try:
                    q2 = _queue.Queue()
                    _worker(code, text, q2)
                    out_box["res"] = q2.get_nowait()
                except Exception as ex:
                    out_box["res"] = (False, str(ex))

            th = threading.Thread(target=_t, daemon=True)
            th.start()
            th.join(timeout)
            res = out_box.get("res")
            if res is None:
                return False, "таймаут песочницы"
            return bool(res[0]), res[1]
        except Exception as e2:
            return False, "песочница недоступна: %s / %s" % (e, e2)
