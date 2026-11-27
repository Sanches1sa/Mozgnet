# -*- coding: utf-8 -*-
"""Генератор инструкций агента «Контур» (режим «Star Gate по инструкциям»).
python tools/build_contour_instructions.py -> server/contour_instructions/*.json"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "server", "contour_instructions")
os.makedirs(OUT, exist_ok=True)


def I(id, title, ask, steps, writes=False, notes="", autonomy=True):
    d = {"id": id, "title": title, "ask": ask, "writes": writes, "steps": steps, "verified": False}
    if not autonomy:
        d["autonomy"] = False
    if notes:
        d["notes"] = notes
    return d


S = lambda: {"op": "survey"}
CHK = lambda: {"op": "check"}
REV = lambda *r: {"op": "review", "rules": list(r)}
CL = lambda: {"op": "changelog"}

PROC = [
    I("ct.survey", "Изучить структуру проекта", ["изучи проект", "что в проекте", "обзор проекта", "расскажи о структуре проекта", "опиши структуру"],
      [S(), {"op": "notes_md", "path": "PROJECT_NOTES.md"}], True, "Создаёт PROJECT_NOTES.md со списком модулей, функций и классов."),
    I("ct.notes", "Описание проекта в файл", ["опиши проект в файле", "сделай документацию проекта", "составь описание модулей", "напиши readme по коду", "сгенерируй PROJECT_NOTES"],
      [S(), {"op": "notes_md", "path": "PROJECT_NOTES.md"}, CHK()], True),
    I("ct.locate", "Найти, где используется имя", ["найди где используется", "найди определение", "где используется функция", "найди в проекте", "покажи где вызывается"],
      [{"op": "locate"}], False, "Имя берётся из кавычек или первого латинского слова задачи: найди «load_config»."),
    I("ct.read", "Прочитать файл", ["прочитай файл", "покажи файл", "что в файле", "открой файл", "покажи содержимое"],
      [{"op": "read_file"}]),
    I("ct.review.full", "Полное ревью кода", ["сделай ревью кода", "проверь качество кода", "найди проблемы в коде", "аудит проекта", "code review", "найди ошибки в проекте"],
      [S(), REV("mutable_default", "bare_except", "eq_none", "open_no_encoding", "duplicate_defs", "unused_import", "long_function", "missing_docstring", "todo"), CHK()]),
    I("ct.review.bugs", "Поиск вероятных ошибок", ["найди вероятные баги", "где могут быть баги", "поищи опасные места", "что может сломаться"],
      [REV("mutable_default", "bare_except", "eq_none", "duplicate_defs"), CHK()]),
    I("ct.review.defaults", "Изменяемые значения по умолчанию", ["найди изменяемые аргументы по умолчанию", "список по умолчанию в аргументе", "mutable default"],
      [REV("mutable_default")]),
    I("ct.review.except", "Голые except", ["найди голые except", "except без типа", "ловля всех исключений"], [REV("bare_except")]),
    I("ct.review.encoding", "open без кодировки", ["найди open без encoding", "проблемы с кодировкой файлов", "кракозябры на windows"], [REV("open_no_encoding")]),
    I("ct.review.todo", "TODO и FIXME", ["найди todo", "список fixme", "что недоделано", "оставленные заметки в коде"], [REV("todo")]),
    I("ct.review.unused", "Неиспользуемые импорты", ["найди неиспользуемые импорты", "лишние импорты", "мёртвые импорты"], [REV("unused_import")]),
    I("ct.review.long", "Слишком длинные функции", ["найди длинные функции", "какие функции слишком большие", "что стоит разбить"], [REV("long_function")]),
    I("ct.review.docs", "Нет описаний у функций", ["найди функции без описания", "где нет docstring", "проверь документацию кода"], [REV("missing_docstring")]),
    I("ct.review.dups", "Повторные определения", ["найди дубликаты функций", "повторно определённые функции", "одинаковые имена функций"], [REV("duplicate_defs")]),
    I("ct.review.print", "Отладочные print", ["найди print в коде", "оставленная отладка", "отладочные выводы"], [REV("print_debug")]),
    I("ct.fix.defaults", "Исправить изменяемые аргументы по умолчанию", ["исправь изменяемые аргументы по умолчанию", "замени список по умолчанию на None", "исправь mutable default"],
      [{"op": "fix_mutable_defaults"}, CHK(), CL()], True, "Только пустые [] и {}; функции в одну строку не трогаются; правка откатывается, если синтаксис ломается."),
    I("ct.fix.encoding", "Добавить encoding в open", ["добавь encoding в open", "исправь open без кодировки", "сделай чтение файлов в utf-8", "исправь кракозябры"],
      [{"op": "fix_open_encoding"}, CHK(), CL()], True, "Только текстовые режимы; бинарные open не трогаются."),
    I("ct.fix.eqnone", "== None на is None", ["исправь сравнение с none", "замени == none на is none", "исправь != none"], [{"op": "fix_eq_none"}, CHK(), CL()], True),
    I("ct.fix.except", "Голый except на except Exception", ["исправь голые except", "замени except на except exception", "не лови все исключения"], [{"op": "fix_bare_except"}, CHK(), CL()], True),
    I("ct.fix.space", "Пробелы в конце строк и перевод строки в конце файла", ["убери пробелы в конце строк", "наведи порядок в форматировании", "отформатируй код", "почисти пробелы"],
      [{"op": "fix_whitespace"}, CHK(), CL()], True),
    I("ct.fix.all", "Безопасные исправления разом", ["исправь безопасные проблемы", "почини очевидные ошибки", "наведи порядок в коде", "сделай рефакторинг безопасный", "исправь типичные ошибки"],
      [REV("mutable_default", "bare_except", "eq_none", "open_no_encoding"), {"op": "fix_mutable_defaults"}, {"op": "fix_open_encoding"}, {"op": "fix_eq_none"},
       {"op": "fix_bare_except"}, {"op": "fix_whitespace"}, CHK(), CL()], True, "Каждая правка проверяется синтаксисом; смысл поведения не меняется."),
    I("ct.tests", "Добавить проверку компиляции", ["добавь тесты", "напиши тесты", "создай тесты проекта", "добавь проверку кода", "сделай unittest"],
      [{"op": "add_tests"}, CHK(), CL()], True, "Тест проверяет только компиляцию файлов — поведение не доказывает."),
    I("ct.add_lesson", "Добавить проверенный шаблон из уроков", ["добавь функцию из уроков", "добавь готовую функцию", "вставь шаблон", "добавь в проект функцию", "добавь утилиту"],
      [{"op": "add_lesson"}, CHK(), CL()], True, "Подбирает шаблон из 106 уроков по задаче, прогоняет его собственную проверку, кладёт в lessons_added/ или в указанный .py и пишет тест."),
    I("ct.rename", "Переименовать файл (как просили; нет имени — индекс версии)",
      ["переименуй файл", "переименуй файл в", "смени имя файла", "назови файл по-другому", "rename file", "переименовать файл и поправить ссылки"],
      [{"op": "rename"}, CHK()], True,
      "Берёт имя из задачи: «переименуй main.py в app.py». Если нового имени нет — добавляет индекс версии (a.py → a_v2.py, a_v2.py → a_v3.py). "
      "Во всех файлах проекта исправляет упоминания старого имени, а в Python — импорты (import old → import new as old). «Копию»/«сохрани старый» — не удаляет исходный. "
      "Делает только это: без тестов, описаний и журнала.", autonomy=False),
    I("ct.rename.version", "Новая версия файла: индекс версии в имени и ссылки",
      ["добавь индекс версии к файлу", "переименуй с индексом версии", "подними версию файла", "новая версия файла", "сделай версию файла", "добавь номер версии в имя файла"],
      [{"op": "rename"}, CHK()], True,
      "Если имя не задано — a.py → a_v2.py, a_v2.py → a_v3.py; ссылки на прежнюю версию в остальных файлах переправляются на новую.", autonomy=False),
    I("ct.archive", "Имя итогового архива",
      ["назови архив", "назови итоговый архив", "переименуй архив", "имя итогового архива", "как назвать результат", "упакуй в архив с именем", "сохрани архив как"],
      [{"op": "archive_name"}], False,
      "Имя берётся из задачи («назови архив app_final»). Если имени нет — имя проекта с индексом версии: x.zip → x_v2.zip → x_v3.zip. Внутри архива файлы не переименовываются.", autonomy=False),
    I("ct.bump_version", "Поднять версию в файле VERSION (только по просьбе)",
      ["подними версию проекта", "увеличь номер версии", "обнови файл version", "новая версия в version", "сделай версию выше"],
      [{"op": "bump_version"}], True,
      "x.y.z → x.y.(z+1) в VERSION в корне или в папке первого уровня. По умолчанию не делается: нужно только если результат загружается как обновление облака поверх той же версии.", autonomy=False),
    I("ct.status", "Состояние работ", ["что изменилось", "что сделал агент", "итог работы", "покажи изменения"], [S(), CHK()]),
    I("ct.checks", "Проверить синтаксис проекта", ["проверь синтаксис", "проверь что код компилируется", "нет ли синтаксических ошибок", "проверь python файлы"], [CHK()]),
    I("ct.changelog", "Записать изменения в журнал", ["запиши изменения в журнал", "веди changelog", "добавь запись в changelog"], [CL()], True),
]


def lesson_recipes():
    """По рецепту на каждый урок программирования: «добавь в проект …» -> add_lesson."""
    try:
        items = json.load(open(os.path.join(HERE, "..", "server", "codelessons.json"), encoding="utf-8"))
    except Exception:
        return []
    out = []
    for it in items:
        if it["id"].startswith("fix."):
            continue
        asks = ["добавь в проект: " + it["title"].lower()] + ["добавь " + a for a in it["ask"][:2]]
        out.append(I("ct.l." + it["id"], "Добавить: " + it["title"], asks, [{"op": "add_lesson", "lesson": it["id"]}, CHK(), CL()], True,
                     "Шаблон из урока %s." % it["id"]))
    return out


def main():
    items = PROC
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))
    json.dump(items, open(os.path.join(OUT, "procedures.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    les = lesson_recipes()
    json.dump(les, open(os.path.join(OUT, "lessons.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("процедур:", len(items), "· по урокам:", len(les))


if __name__ == "__main__":
    main()
