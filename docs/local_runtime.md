# Проверенный локальный запуск

## Первая установка

```bash
make setup
make demo
make dev
```

`make setup` создаёт `.venv` с Python 3.12 и зависимостями из `uv.lock`.
Если `uv` отсутствует, для его bootstrap используется установленный `python3`.
Если активный Node/npm не соответствует Node 22.14.0 / npm 10.9.2, на macOS
и Linux (ARM64/x64) скачивается официальный архив Node в `.tools/node`.
SHA-256 сверяется с `SHASUMS256.txt` того же официального релиза. Глобальные
установки Python/Node/npm не изменяются. Нужны `curl`, `tar`, `shasum` либо
`sha256sum`, `make` и сеть при первой установке.

Все Makefile-команды и `scripts/npm.sh` выбирают локальный Node первым.
Окружение продолжает работать, даже если системный `node --version` показывает
другую версию. Полезная проверка npm проекта: `scripts/npm.sh --version`.

`make demo` создаёт локальные demo-модели и эксперимент, затем проверяет
генерацию, API, распознавание и сохранение результата. Данные синтетические;
их метрики не являются оценкой качества на реальных измерениях.

## Работа приложения

`make dev` управляет тремя процессами: API, Vite и worker. Интерфейс доступен
на `http://localhost:5173`, API-документация — на `http://localhost:8000/docs`.
Сообщение `SpectraLab ready` печатается после успешных HTTP-ответов серверов.
Worker обслуживает очереди исследований и пакетного анализа. Ctrl+C завершает
запущенные процессы и их группы. При падении одного процесса остальные также
завершаются, а команда сообщает имя процесса и возвращает ненулевой код.

Смена портов автоматически меняет адрес frontend-прокси:

```bash
API_PORT=8100 UI_PORT=5200 make dev
```

Порты должны различаться и быть свободны. Vite запускается с `--strictPort`:
ссылка не меняется скрыто, если выбранный порт занят. Проверка зависимостей
и портов выполняется до запуска дочерних процессов.

Если worker уже запущен через `make worker`, используйте:

```bash
scripts/uv.sh run --frozen --all-packages --no-sync python scripts/dev.py --no-worker
```

Для настройки PyCharm выберите существующий интерпретатор
`<папка проекта>/.venv/bin/python` для workspace/родительского модуля. Проверенные
терминальные команды используют эту же среду. Если автонастройка IDE сообщает,
что не видит uv, переустанавливать системный Python не требуется.

## Проверки перед сдачей

```bash
make lint test build
make api-types
git diff --exit-code -- frontend/src/api/schema.d.ts
make browser-install
make e2e
```

`browser-install` скачивает Chromium в `.cache/ms-playwright`.
E2E запускает отдельные frontend/API на 5174/8001 и отдельный worker с временным
хранилищем. Данные рабочего приложения на 5173/8000 при этом не используются.
Снимки экранов, видео и traces при сбое находятся в `frontend/test-results/`,
HTML-отчёт — в `frontend/playwright-report/`.

Уже выполненный локальный smoke очереди: создание одного demo job через
`/api/v1/jobs` дало `queued`, worker завершил его со статусом `completed`,
`progress=1/1` и сохранённым `run_id`. Состояние worker проверено через
`/api/v1/system/status` и frontend-прокси.

Прогон 2026-10-23: setup/demo/build выполнены; 650 Python tests passed,
4 skipped; 54 UI tests passed; 8 browser E2E passed (20.8 s). Линтеры,
TypeScript и повторная генерация OpenAPI завершились без diff схемы.
