# Локальная проверка перед публикацией 2026-10-13

Проверены все накопленные изменения проекта перед отправкой на GitHub.
Актуальные незавершённые задачи NEXT01–NEXT12 вынесены в начало
[TODO](todo.md) и [roadmap](roadmap.md), со ссылкой из README.
Исторические 24 завершённых пункта сохранены. Новый научный final остаётся
открытым: программные проверки ниже не являются его результатами.

| Проверка | Результат |
| --- | --- |
| `make api-types` | OpenAPI и TypeScript-контракт сгенерированы успешно |
| `make lint` | Ruff check/format (107 файлов), ESLint, TypeScript — exit 0 |
| `make test` | **324 Python passed** (15 предупреждений, 53.40 s), **33 UI passed** |
| `make build demo analytics` | Production build, demo/API smoke и три аналитических сценария — exit 0 |
| `make e2e` | **6 passed**, 32.3 s, настоящий API и отдельный worker |
| Файлы публикации | Секретов не обнаружено; `.env.*`, пользовательские batches и временные PDF renders исключены через `.gitignore`; `.env.example` сохранён |
| Документация | NEXT01–NEXT12 присутствуют в обоих планах; локальные ссылки TODO/roadmap/README существуют; `git diff --check` — exit 0 |
| Замороженная модель | Четыре implementation hashes v2 и SHA-256 профиля совпадают с исследовательской сводкой |

Локальные журналы: `.cache/github-publication-checks.log` и
`.cache/github-publication-e2e.log`. Предупреждения зависимостей о deprecated API
и предупреждение Vite о размере Plotly-чанка сохраняются; проверки успешны.
Численные исходные измерения, модели и большие локальные run-артефакты остаются
вне Git по правилам проекта; справочники NIST, provenance, компактные отчёты
и документированные снимки интерфейса входят в публикацию.

---

# Проверка поиска 2026-10-13 — новые источники v3

В этой итерации менялись реестры и документация. Код модели, веса, пороги,
API/UI не менялись; новый научный final-тест не выполнен.
[Отчёт](final_validation_search_v3.md),
[машиночитаемая сводка](final_validation_search_v3_summary.json).

| Проверка | Фактический результат |
| --- | --- |
| Аудиты кандидатов | Четыре JSON-реестра: PDS/SuperLIBS, CSA/Aalto, blank-ветки, дополнительные источники |
| Источники | 79 уникальных локальных снимков совпали с SHA-256; каталоги ZIP/LID отделены от численных спектров, частичный ChemLIBS inventory явно обозначен |
| Химические преобразования | mg/kg → wt% → FeOT/MgO для KD-2, Fe₂O₃* → FeOT для PDS; неоднозначность CSA FeO/Fe₂O₃ и номинальные нули сохранены |
| Независимая проверка | Таблица SuperCam прочитана визуально, vacuum-соглашение найдено в первичной публикации; несовместимость окон модели подтверждена |
| Сохранность v2 | Четыре implementation hashes и SHA-256 профиля совпали с прежней сводкой; identity профиля не изменился |
| Metadata admission | Реальный вызов `admit_final_campaign` на новом пустом final manifest и прежнем реестре исключений вернул blocked; результат содержит manifest/registry и воспроизводимые хеши |
| Научная оценка | Новые интенсивности, fitting и prediction не запускались; final_metrics/final_score = null, допущенных наборов/blank = 0 |

Metadata guard не является полной проверкой готовности теста: instrument/feature
coverage и positive support проверяются отдельно. Его требование совместного
Fe/Mg blank строже возможной схемы с раздельными контролями. Эти ограничения
сохранены в отчёте; успешные программные проверки не выдаются за измеренное
финальное качество модели. Полный suite повторно не запускался: код не менялся.

---

# Проверка доработок 2026-10-13 — development v2

Добавлены intake low-Mg, UV-справочник, диагностика кампаний, модель линий,
вложенная групповая проверка, numeric JSON CLI и metadata guard blank-допуска.
API/UI в этой итерации не менялись. [Научный отчёт](instrument_v2.md)
явно отделяет выполненный development от невыполненного нового final.

| Проверка | Фактический результат |
| --- | --- |
| Полный Python suite после последних изменений | **324 passed**, 15 предупреждений зависимостей, 54.31 s; `.cache/v2-final-tests.log` |
| `make lint` | Ruff check/format (107 файлов), ESLint и TypeScript — exit 0; `.cache/v2-final-lint.log` |
| `git diff --check` | exit 0 |
| Дополнительный intake | 4 новых материала low-Mg, 19 514 695 байт CSV/labels, immutable SHA; повтор проверяет закреплённые байты. Новых low-Fe нет |
| UV reference | 8 NIST exports Na/Mg/Ca/Fe I–II, явный vacuum; каталог и первичные заголовки проверены, SHA-256 закреплён |
| Диагностика кампаний | `diagnostic-20261013T163803-4b10cde1`, 195 физических материалов / 198 спектров; все данные ранее раскрыты; причинный дрейф не заявляется |
| Nested development CV | `development-20261013T164342-b37a6ab8`, 202 спектра / 199 физических материалов / 46 связанных групп; 5 outer × 3 inner folds; completed |
| Независимая сверка CV | Каждый outer validation ID встречается один раз; внутренние folds содержат только outer-fit IDs/группы. C соответствует argmax внутренних отчётов, decisions точно равны scores ≥ threshold своего fold; physical-weighted confusion и метрики совпали |
| Сохранность provenance | 4 закреплённых v2 implementation hashes и все 34 v1 hashes совпали; plan, reference, intake, profile hashes проверены |
| UV-абляция | Все 16 видимых линий на элемент сохранены; добавлены 11/16/13/16 UV-линий Na/Mg/Ca/Fe. Два теста проверяют состав и неизменность видимых признаков |
| Реальный CLI v2 smoke | Macusanite из development, 50 shots → среднее 45; сохранены profile identity/input SHA, uncalibrated, H/He unknown, development-only qualification, final_test_status not_performed |
| CLI/model guard | Проверены целостность, конечность чисел, прибор, class order, полная структура плана/окон, покрытие CCS и отказ при нулевой эмиссии; profile reload воспроизводит scores |
| Blank admission | 48 новых тестов: независимость всех identity/assay полей, bounds/единицы, реальный control type, license/axis/raw, malformed metadata; прежние protocol tests также проходят |
| Фактический final admission | `blocked`, final_metrics/score null; результат воспроизводится из embedded manifest/seen registry, SHA реестра 12 внешних кандидатов совпал |

Внутренние OOF-массивы не сохранены: оптимальность порогов проверена по
закреплённому коду, отчётам кандидатов и восстановленным разбиениям, без
независимого пересчёта из внутренних scores. Внешние OOF-массивы сохранены.
Это ограничение аудита отражено отдельно от отсутствующего final-теста.

Финальный CLI smoke:
`reports/adaptation_v2/development-20261013T164342-b37a6ab8/prediction-smoke-macusanite.json`.
Логи обучения: `.cache/improve-chemcam-v2.log`. Большие raw/run-файлы локальные
и исключены из Git; исходники, provenance и компактные сводки сохранены в проекте.

---

# Предыдущая итерация 2026-10-13 — приборная адаптация v1

Последняя итерация добавляет исследовательские CLI, читатель ChemCam, групповой
протокол и приборную модель. API/UI не менялись в этой итерации.

| Проверка | Результат |
| --- | --- |
| Полный Python suite | **235 passed**, 15 предупреждений зависимостей, 46.57 s; `.cache/adaptation-final-tests.log` |
| `make lint` | Ruff check/format (93 файла), ESLint, TypeScript — exit 0; `.cache/adaptation-final-lint.log` |
| `git diff --check` | exit 0 |
| Новый intake | 198 файлов спектров и PDS labels, 965 863 798 байт; SHA-256, лицензия, шкала и именование проверены; 1 отсутствующий файл подтверждён отдельной поправкой, без замены |
| Повторный intake | Все закреплённые файлы проверены, SHA-256 реестра остался прежним; изменённые байты не принимаются и не перепривязываются |
| Разделение | Train 93 спектра / 90 материалов / 24 группы; tune 34/34/8; test 71/71/12; повторы, семейства, сессии, SHA и ранее проверенные стандарты не пересекают границы |
| Одна итоговая оценка | `chemcam-20261013T161956-5164617a`, status completed, first_locked_evaluation; профиль загружен из сохранённого артефакта перед оценкой, test не использовался при выборе модели/порогов |
| Реальный prediction CLI | 50 импульсов, среднее по 45 после исключения первых пяти; четыре operational результата, H/He unknown, calibrated false, identity/SHA сохранены |
| Защита использования test | 21 тест: O_EXCL, race, fsync failure, неизменяемый replay, отказ при иных identity и повреждённой квитанции |
| Безопасный профиль | JSON/NPZ без pickle; checksums, размеры/формы, значения, class order, сетка, полосы и instrument ID проверяются |
| Независимая сверка отчёта | Confusion counts пересчитаны из сохранённых предсказаний; 34 хеша реализации и хеши источников/артефактов совпали; порядок model lock → test receipt → test outputs подтверждён |

Измеренное качество не прошло практический допуск: у адаптированной модели
recall Mg = 0/19, FPR Fe = 8/17. Пороги после test не менялись. Полное сравнение,
парные интервалы и ограничения: [отчёт](instrument_adaptation.md),
[машиночитаемая сводка](instrument_adaptation_summary.json).
Журнал фактического прогона: `.cache/adaptation-run.log`. Исходные данные,
подробные результаты и модели локальные и исключены из Git.

## Предыдущая итерация 2026-10-13

Окружение: macOS arm64, Apple M1 / 8 GB; Python 3.12.14, Node 22.14.0,
npm 10.9.2, uv 0.6.17, optional PyTorch 2.9.0. Минимальный runtime без PyTorch
отдельно проверен subprocess-тестом registry.

| Проверка | Результат |
| --- | --- |
| `make lint` | Ruff check/format, ESLint и TypeScript — exit 0 |
| `make test` | 125 Python + 33 frontend теста — exit 0 |
| `make build` | Vite production build — exit 0 |
| `make api-types` повторно | exit 0; SHA-256 типов не изменяется: `30bca566839b121e3b1badb09e8af855726b26ecc1507a961c7b1829c3ea3c4c` |
| `make demo` | exit 0, обучены три baseline; API generation/predict smoke прошёл |
| `make analytics` | exit 0; обработаны одиночные и вложенные seed runs без смешения с series envelope |
| `make e2e` | 6 Playwright тестов на настоящих изолированных API и worker, финальный прогон 39.6 s |
| Реальный worker | Серия seed 101 completed, health и predict отвечали во время работы; статусы и child IDs доступны |
| Offline NIST | 3 853 линии; cache checksum и dataset/model/run provenance согласованы, smoke completed |
| Baseline/CNN/calibration/physics series | По 5 фиксированных seed, completed; [измеренные результаты](research_results.md) |
| Open-set | 5 seed; false accept/reject сохранены отдельно от detection, [ограничения](research_results.md) |
| ML08 ChemCam | 64 физических образца / 255 спектров, 5 seed × 3 модели; completed, [recall и ограничения](external_validation.md) |
| Расширенный benchmark | 20/20 сочетаний seed/suite/grid, 60 обученных моделей, 126 агрегатов; 0 failed, [отчёт](expanded_benchmark.md) |
| Пакетный backend | 10 новых тестов: queue/cache/provenance, per-file errors, cancel/resume/crash, CLI и subprocess worker, export и backup/restore |
| ML08 artifact reload | Три модели seed 7 воспроизводят 255 сохранённых решений точно, scores с допуском 1e-12 |
| Предыдущий external intake | pLIBS Z-300 остаётся blocked, metrics null; его ограничения не перенесены на новый ChemCam |
| `git diff --check` | exit 0 |

Браузерные сценарии включают генерацию → анализ → несколько моделей → экспорт,
CSV upload и отказ до POST, metadata/history, пресеты, эксперименты, jobs,
idempotency/cancellation и недоступную модель. Новые сценарии проверяют папку с
CSV и README, отдельные отказы без отправки ошибочных файлов, две модели,
reload/resume, совпадение SHA-256 и экспорт; изменённый файл не подменяет provenance.
Проверены Plotly rendering, zoom,
клавиатурная таблица и ширина 820 px. Подробности и сохранённый benchmark
100 000 точек: [frontend-validation.md](frontend-validation.md).

При final review исправлены и повторно проверены повреждённые model manifests,
CNN checkpoint/config round-trip, сохранение timing в API, гонка завершения процесса
при отмене, восстановление jobs/reports, экспорт seed reports и отсутствие локальных
путей в диагностике недоступной модели.

Сетевой auth/ownership не реализован по выбранному пользователем локальному режиму.
Лимит памяти worker на macOS не обещается: OS resource cap действует только на Linux;
время и отмена проверены локально. Backup/restore выполняется при остановленных
API и worker, как описано в [API](api.md).

GitHub Actions обновлён браузерной проверкой и upload диагностик, но удалённый CI
в этой итерации не запускался. Docker заново не собирался: старый результат ниже
не является проверкой новых изменений. Jupyter отдельно не запускался.
ML08 измеряет только recall подтверждённого присутствия: нет отрицательных меток,
поэтому precision/F1 не определены. Исправлена первичная агрегация: равный вес каждого
физического образца при разном числе известных меток. Regression test проверяет этот
случай; исходный ошибочный отчёт помечен superseded, итог опубликован отдельно.
Из предупреждений остались deprecated API зависимостей
и размер лениво загружаемого Plotly (~1 MB, ~361 kB gzip).

Локальные журналы текущей итерации: `.cache/iteration-checks.log`,
`.cache/iteration-test-build.log`, `.cache/iteration-api-types.log`,
`.cache/iteration-e2e.log`, `.cache/chemcam-validation.log`. Предыдущие demo/analytics
проверки: `.cache/final-checks.log`, `.cache/analytics.log`. Browser diagnostics и большие научные
артефакты исключены из Git; конфиги, provenance и компактные отчёты входят в проект.
Первый запуск E2E в sandbox не получил разрешение bind loopback; повтор с разрешённым
локальным сервером завершился успешно.

---

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
