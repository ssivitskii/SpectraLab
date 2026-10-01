# Локальная проверка 2026-10-01

Окружение: Python 3.12.14, Node 22.14.0, npm 10.9.2, uv 0.6.17.
Проверки этого отчёта выполнены до инициализации Git и публикации репозитория.

| Проверка | Фактический результат |
| --- | --- |
| `make setup` | exit 0, установка по двум lockfile |
| `make demo` | exit 0, обучены NNLS/Logistic OVR/Random Forest, сохранён датасет и smoke-run |
| `make experiment CONFIG=ml/configs/experiments/smoke.yaml` | exit 0, отдельный completed run без перезаписи первого |
| `make analytics` | exit 0, три сценария создали CSV/JSON/PNG; графики просмотрены |
| `make api-types` | exit 0; повторная генерация даёт тот же SHA-256 |
| `make test` | exit 0; 29 Python + 6 UI тестов |
| `make lint` | exit 0; Ruff check/format, ESLint, TypeScript |
| `make build` | exit 0, Vite 7.3.6 |
| `npm ci` / audit | exit 0; 0 известных npm-уязвимостей после обновления зависимостей |
| `docker compose config --quiet` | exit 0 |
| `docker compose build` | exit 0, оба образа |
| Docker runtime | health + frontend + generation/storage + все 4 модели + experiment + nginx JSON 413; exit 0 |
| `nginx -t` | exit 0 |
| CLI NIST import | exit 0; 2 строки приняты, 0 отброшено; SHA-256 совпадает с metadata |
| CLI generate | exit 0, JSON содержит конечные массивы и provenance |

В браузере проверены генерация H + Na, переход на анализ, реальный ответ NNLS,
загрузка CSV (SNR: неизвестен), выбор Logistic OVR, шесть оценок и пороги,
наложение/выключение справочных линий, zoom, сохранённый эксперимент с графиком и
таблицей, компоновка при ширине 820 px. Ошибок в консоли не обнаружено.
Dev-сервер корректно остановлен перед сменой зависимостей и затем запущен снова.

Для Docker использовался изолированный Compose project `spectralab-verify`,
backend на loopback-порту 18000 и автоматически выделенный порт frontend:
18080 был занят сторонним процессом. После проверки тестовые контейнеры и сеть
удалены через `docker compose down`, тома/данные и образы сохранены.

Результаты demo находятся в `reports/experiments/smoke-20261001T134912-c157119f/`
и `smoke-20261001T185457-3eb83003/`. Это демонстрационные вычисления на
`demo_fixture`, а не исследование качества реального измерительного прибора.
Локальные журналы проверок: `reports/verification/` (исключены из Git).

## Что не проверялось

Большие сетки, статистика по нескольким seed и измерения реальных образцов не
запускались. Опциональная среда Jupyter отдельно не устанавливалась; вместо
ноутбуков выполнены аналитические Python-сценарии. GitHub Actions workflow
подготовлен, но удалённый запуск CI не выполнялся.

Остались предупреждения зависимостей о deprecated API, а также предупреждение Vite
о размере отдельного Plotly-чанка (~1 MB, ~361 kB gzip). Проверки завершаются с exit 0.
Ошибки прав sandbox и npm resolver были устранены; блокирующих ограничений среды
для проверенного локального и контейнерного сценариев не осталось.
