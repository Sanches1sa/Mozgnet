# neuro12-cloud 3.210.0
- tls.py: HTTPS-слушатель (8443, NEURO_TLS_PORT; NEURO_TLS=0 — выкл.), самоподписанный сертификат (openssl → cryptography), свой сертификат из data/tls/, автоперечитывание при смене файла, /api/tls (отпечаток).
- /api/health: поле https_port.
- store.d4_add/d4_take, GET /api/brain/d4, llmlink._d4_all: мысли своей LLM идут и на шину D4 (настройка d4_thoughts).
- cloud/launcher/install-ssl-linux.sh — установка SSL на Linux.
- tests/test_tls210.py.
