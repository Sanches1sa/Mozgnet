# 3.221.0 — Контур: режим «Star Gate по инструкциям», LLM наблюдает

- Новая модель агента `brain` (настройки агента и окно проекта): `project_brain.py` + 121 инструкция Контура (`server/contour_instructions/`, генератор `tools/build_contour_instructions.py`).
- Операции: survey, locate, read_file, review (mutable_default, bare_except, eq_none, open_no_encoding, unused_import, long_function, missing_docstring, duplicate_defs, todo, print_debug), fix_mutable_defaults, fix_open_encoding, fix_eq_none, fix_bare_except, fix_whitespace (каждая правка проверяется `ast.parse`), add_lesson (шаблон из 106 уроков + собственная проверка + тест в `tests/`), add_tests (компиляция), notes_md, changelog, check.
- Нет подходящей инструкции — ничего не меняется, об этом честно сказано. Код проекта не запускается.
- LLM наблюдает: статья «Контур: наблюдение …» в БЗ (её читает llmlink и пишет «усвоено N%»), запись в `contour_learned.json` → поток обучения «admin», мысль в ленте.
- `GET/POST /api/contour/instructions`; пересев знаний версии 6. Свои инструкции: `data/contour_instructions/*.json`.
- Честно: инструкции и операции проверены тестами на небольших примерах, не на ваших реальных проектах; свободно написать новую фичу этот режим не может.
