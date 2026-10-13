# SpectraLab

SpectraLab — учебно-исследовательский инструмент для multi-label распознавания
элементов по атомному эмиссионному спектру. Стандартный профиль содержит `H`, `He`,
`Na`, `Mg`, `Ca`, `Fe`. Это оценка присутствия: `component_weights` — относительные
вклады генератора, не концентрации, массовые доли или проценты.

Выбран режим **одного исследователя на одном локальном компьютере**. `ml/` содержит
справочники, генератор, модели и эксперименты; `backend/` предоставляет FastAPI и
локальное хранение; `frontend/` — русский React-интерфейс. Обучение выполняется
из CLI или отдельным worker, вне процесса обработки HTTP-запросов. Пользователей,
аутентификации и доступа к чужим данным в этой конфигурации нет.

**ML08 завершён:** проведена внешняя проверка на 64 стандартах / 255 спектрах
NASA PDS ChemCam с подтверждёнными public-domain лицензией и vacuum-шкалой.
Измеренный recall показывает существенные пропуски; подтверждённых отрицательных
меток нет, поэтому F1/precision и эксплуатационная надёжность не заявляются.
[Протокол, результаты и ограничения](docs/external_validation.md).

**Приборная модель v2:** добавлены четыре материала с низким MgO, диагностика
кампаний и признаки UV/видимых линий. На вложенной групповой development-проверке
recall Mg — 96.0%, Fe — 93.6%; FPR — 0/39 и 1/25 соответственно.
[Отчёт и команды](docs/instrument_v2.md). Продолжение поиска выявило новые
low-Fe кандидаты в PDS и CSA, включая графит KD-2. Подтверждены лицензии и
химические данные, но шкала и необходимые контрольные границы остаются
неполными. Финальная проверка v2 **не выполнена**;
[аудит источников и условия допуска](docs/final_validation_search_v3.md).

Следующие задачи проекта собраны в начале [TODO](docs/todo.md):
NEXT01–NEXT08 закрывают данные, контрольные образцы, совместимость прибора
и независимую финальную проверку; NEXT09–NEXT12 описывают последующие улучшения
модели и локальной эксплуатации. Для каждой задачи указаны приоритет,
зависимости и критерий завершения. [Краткий roadmap](docs/roadmap.md).

## Быстрый запуск

Нужны Python **3.12.14**, Node **22.14.0** и npm **10.9.2**. Версии зафиксированы
в `.python-version`, `.nvmrc` и manifests; зависимости — в `uv.lock` и
`frontend/package-lock.json`. `make setup` устанавливает uv 0.6.17, если его нет.
При использовании nvm сначала выполните `nvm install && nvm use`.

```bash
make setup
make demo
make dev
```

`make demo` использует `demo_fixture`, создаёт небольшой синтетический датасет в
`data/processed/`, обучает доверенные локальные модели в `artifacts/models/` и
выполняет API→ML smoke. До обучения работает `builtin-nnls`; после demo доступны
`baseline-nnls`, `logistic-ovr` и `random-forest`.

`make dev` запускает backend и frontend: [интерфейс](http://localhost:5173),
[Swagger](http://localhost:8000/docs). Ctrl+C останавливает оба процесса.
Только backend: `make api`. Для выполнения заданий, созданных в интерфейсе,
запустите **в другом терминале**:

```bash
make worker
```

Альтернатива — `docker compose up --build`: интерфейс на
[localhost:8080](http://localhost:8080), API на `localhost:8000`; порты привязаны к
loopback. Остановите `make dev` перед Compose. `docker compose down` сохраняет
локальные данные и артефакты. Compose запускает API и UI; worker запускается отдельно.

## Что доступно в интерфейсе

- **Генератор:** элементы и ограничения из capabilities, Gaussian/Voigt, шум,
  response и насыщение; локальный версионированный пресет с параметрами и seed,
  переход к анализу и экспорт CSV. Истина генератора отделена от prediction.
- **Анализ:** проверка CSV до отправки, история спектров, metadata образца/прибора/
  сессии/калибровки, сравнение нескольких ready-моделей с независимыми результатами
  и ошибками. Видны версии, источник, тип score, пороги и причины недоступности.
- **Пакетный анализ:** папка или несколько CSV, очередь с сохранённым ID,
  восстановление незагруженных файлов по SHA-256, повтор незавершённых результатов,
  отдельные ошибки и единый JSON/CSV/ZIP. Worker обрабатывает выбранные модели;
  успешные результаты при возобновлении сохраняются.
- **График:** display-only огибающая для плотных спектров, фильтр справочных линий,
  доступная таблица reference/observed wavelength и delta, клавиатурный выбор
  диапазона и сохранение zoom при переключении слоёв. Prediction и экспорт
  используют полные исходные массивы.
- **Эксперименты:** cursor-пагинация и фильтры, одиночные запуски и серии,
  mean/SD/count из сохранённого отчёта, переключение метрик и single/mixture,
  состав серии и причины failed/incomplete. Несовместимые протоколы и источники
  не объединяются молча.
- **Задания и экспорт:** создание, реальные статусы и прогресс, отмена, повтор с
  idempotency key и переход к отчёту; JSON/CSV/ZIP с версиями, provenance и checksum.
  Ссылки на spectrum/run/job восстанавливаются после обновления страницы.

## Команды исследований и проверки

```bash
# Быстрые проверки и сборка
make test
make lint
make build
make api-types
make analytics

# Реальный браузер + изолированный временный backend
make browser-install
make e2e

# Одиночный эксперимент и согласованная серия на пяти фиксированных seed
make experiment CONFIG=ml/configs/experiments/noise_robustness.yaml
make series CONFIG=ml/configs/experiments/benchmark_series.yaml

# Сборка локального NIST cache из сохранённых выгрузок, без сети
make reference
make experiment CONFIG=ml/configs/experiments/nist_smoke.yaml

# Исследование калибровки и одного физического механизма
make series CONFIG=ml/configs/experiments/calibration.yaml
make series CONFIG=ml/configs/experiments/physics_voigt.yaml

# Отдельный синтетический open-set benchmark
make open-set

# Расширенный NIST benchmark: слабые примеси, близкие линии, профили и сетки
make expanded-benchmark

# Завершённая проверка synthetic→real; скачивание нужно только в первый раз
make external-validation-download
make external-validation

# Предыдущий недопущенный PDS intake сохранён для аудита
make external-data
```

`make external-data` требует сети и проверяет совместимость/происхождение данных;
он не публикует метрики при нерешённых ограничениях. `make open-set` исследует
отказ от ответа отдельно от обычного `/predict` и не включает эту политику в API
автоматически.

Пакетная обработка доступна также из локального CLI:

```bash
scripts/uv.sh run --frozen --all-packages --no-sync python -m app.batch \
  /path/to/spectra --models builtin-nnls --output reports/my-batch.zip
# Возобновить сохранённый batch; для недозагруженных файлов укажите папку снова
scripts/uv.sh run --frozen --all-packages --no-sync python -m app.batch \
  /path/to/spectra --resume BATCH_ID --output reports/my-batch-resumed.zip
```

CLI с `--enqueue` только ставит пакет в очередь для `make worker`; без него запускает
локальную обработку либо ждёт работающий worker. Существующий export не перезаписывается.
Разрывы диапазона ChemCam обрабатывает отдельный исследовательский адаптер; обычный
CSV API продолжает требовать покрытие профиля проекта.

Конфиги проекта и эксперимента имеют явную `schema_version: 1`; неизвестные поля,
типы, сетка и несовместимые условия отклоняются до создания run. Одиночный запуск
получает новый неизменяемый каталог в `reports/experiments/`; серия сохраняет
ожидаемые seed, дочерние run-ID, hashes и исходные результаты. В сравнительные
агрегаты входят только полные согласованные seed. SD использует `ddof=1` и равен
`null` при числе наблюдений меньше двух. Failed/incomplete не заменяются нулями.

Benchmark-профиль использует сетку 0.5 nm, обычный demo — 0.05 nm; результаты и
модели разных профилей нельзя смешивать. Скорость измеряется после прогрева с
повторами, median и p95. Фактические результаты:
[исследовательский отчёт](docs/research_results.md),
[расширенный benchmark](docs/expanded_benchmark.md),
[протокол экспериментов](docs/experiment_protocol.md).

## Исследовательский профиль прибора

Новая development-итерация и CLI `predict_line_v2.py` описаны в
[отчёте v2](docs/instrument_v2.md). Приведённая ниже проверка v1 — исторический
зафиксированный эксперимент; её test теперь используется как development v2
и повторно независимым не считается.

Отдельный профиль `chemcam-lanl-testbed` обучается на измерениях ChemCam
`calib_2015` и проверяется на удержанных стандартах `calib_2021`. Зафиксированная
выборка содержит **127 development-спектров** и **71 test-стандарт**. Один исходно
выбранный файл отсутствует в архиве и исключён до обучения без замены.
Одинаковые опубликованные имена объединены в 124 физических
материалов; в test 71 материал и 70 семейств. Физические образцы, семейства, сессии
и ранее использованные материалы разделяются до обучения; development дополнительно
делится на train/tune. На итоговом test профиль показал ограничения: recall Mg = 0,
FPR Fe = 47.1%; профиль остаётся исследовательским. Полное сравнение — в
[отчёте](docs/instrument_adaptation.md).

```bash
# Получить выбранные CCS CSV и PDS labels, сохранить provenance и SHA-256
make instrument-data
# Зафиксировать проверенный состав, разделение, метки и правила до предсказаний
make instrument-freeze
# Обучить кандидатов на train, выбрать модель/пороги на tune, затем проверить test
make instrument-adaptation

# Явно повторить тот же закреплённый эксперимент после первого использования test
.venv/bin/python scripts/adapt_chemcam.py --replay

# Применить сохранённый profile к одному новому CCS CSV; output должен быть новым
scripts/uv.sh run --frozen --all-packages --no-sync python scripts/predict_instrument.py \
  --artifact /path/to/run/profile --input /path/to/new_ccs.csv \
  --instrument-id chemcam-lanl-testbed --output reports/new-instrument-prediction.json
```

Запуск исследования печатает каталог в `reports/adaptation/`; его `profile/`
передаётся в `--artifact`. CLI требует совпадения прибора и сохранённые пороги,
усредняет shots 6…N и использует только полосы профиля, отклоняя внутренние разрывы
больше 0.5 nm. Результат сохраняет SHA-256 входа, identity профиля, правила обработки,
score и пороги. `instrument_adapted_logistic_score` **не является вероятностью**,
`calibrated: false`; профиль не регистрируется в API автоматически.

Метки `Na`, `Mg`, `Ca`, `Fe` определены по опубликованным `Na2O`, `MgO`, `CaO`,
`FeOT` в wt%: `0 < x <= 0.1` — operational negative, `x >= 1` — positive,
остальное, включая ноль и пропуски, — unknown. Это отдельная задача
`operational_above_threshold`, не доказательство отсутствия элемента и не измеренный
предел обнаружения. `H`/`He` остаются unknown. Источники и точный протокол:
[происхождение данных](docs/sources.md), [адаптация прибора](docs/instrument_adaptation.md).

## Справочники и входной CSV

Demo всегда использует `data/fixtures/demo_lines.csv`: это вручную отобранный
учебный `demo_fixture`, явно **не NIST**. Отдельный двухстрочный Na I fixture
проверяет импорт формата. Полный сохранённый источник для шести поддерживаемых
элементов находится в `data/reference/nist_neutral_350_800/`: исходные выгрузки,
metadata, URLs, версия, raw SHA-256 и проверяемый `catalog.json`. NIST atomic lines —
справочные данные, не измеренные спектры смесей.

Выберите cache в конфиге исследования (`reference_cache`, при необходимости
`reference_sha256`) или через CLI `--reference-cache`. Команды `train`, `experiment`
и `series` используют выбранный источник согласованно. Для локального API задайте
источник до запуска:

```bash
SPECTRALAB_REFERENCE_CACHE=data/reference/nist_neutral_350_800/catalog.json make api
```

Смена источника требует перезапуска. `/elements`, генератор и registry используют
один каталог; несовместимая модель отклоняется. Источник не выбирается произвольным
путём из HTTP-запроса. Импорт другой сохранённой выгрузки:

```bash
scripts/uv.sh run --frozen --all-packages --no-sync spectralab import-reference \
  --input path/to/export.csv --metadata path/to/export.metadata.json \
  --cache data/cache/reference.json --report reports/reference_import.json
```

CSV измерения содержит ровно `wavelength_nm,intensity`:

```csv
wavelength_nm,intensity
350.0,0.01
575.0,1.0
800.0,0.02
```

Это минимальный пример формата; для содержательного анализа нужна более плотная
сетка. Требуются конечные числа, возрастающие длины волн без дублей, ненулевой
сигнал и полное покрытие диапазона проекта. Стандартный профиль — 350–800 nm,
`vacuum`, до 5 MB и 100 000 точек. Актуальные условия выдаёт `/capabilities`.
Air/vacuum преобразуется явно, а не угадывается при загрузке. SNR загруженного
CSV остаётся неизвестным. Подробности: [API](docs/api.md), [источники](docs/sources.md).

## Физика, модели и область применимости

Генератор поддерживает интегрированные по пикселям Gaussian/Voigt-профили,
Gaussian либо Poisson/read noise, отклик детектора, насыщение, фон, сдвиг
калибровки и вариацию амплитуд. Параметры, единицы и seed сохраняются в provenance.
Профили учитывают потерянные за конечным окном хвосты без скрытой перенормировки.
Для Poisson/read `snr_db` равен `null`, что не означает отсутствие шума.

Линии различают `(element, ion_stage)`; проект задаёт активную стадию, а шаблоны
разных ионов разделены. Для стандартного воздуха реализовано отдельное
преобразование Peck–Reeder с проверенными эталонами и provenance. Стандартный
справочник остаётся нейтральным; новая стадия требует подходящего источника и
совместимого обучения. Старые несовместимые артефакты не загружаются молча.

NNLS, Logistic OVR и Random Forest остаются основными baseline. NNLS возвращает
`nnls_coefficient_times_fit_quality`: это коэффициент шаблона, умноженный на
качество реконструкции, **не вероятность**. Калибровка Logistic/RF реализована,
но **по умолчанию выключена**. При `calibrate: true` development делится по base IDs
на обучение калибратора и выбор порогов; test не участвует в настройке. В отчёте
сравниваются Brier/ECE и reliability plots до/после; улучшение не предполагается
заранее. `calibrated` отражает применённый метод и его область проверки.

Опциональная multi-label **1D CNN** подключена через интерфейс estimator.
Минимальный runtime работает без PyTorch. Для её обучения установите extra:

```bash
scripts/uv.sh sync --frozen --all-packages --extra cnn
make series CONFIG=ml/configs/experiments/cnn_comparison.yaml
```

Для `/models` и `/predict` нужны optional runtime и совместимый CNN-артефакт в
доверенном каталоге моделей. CNN checkpoint хранит численные веса NPZ, версию
архитектуры, seed, class order, preprocessing и SHA-256; загрузка не использует
pickle. Преимущество CNN над baseline определяется измерением, не предполагается.
Jupyter также опционален: extra `notebooks` содержит `ipykernel` и `jupyterlab`.

Пустой `detected_elements` означает только отсутствие превышения порогов известных
классов. Он не доказывает отсутствие элементов или наличие неизвестного вещества.
Open-set benchmark отдельно измеряет false rejection известных и false acceptance
удержанных неизвестных элементов/смесей на синтетических данных.

Физические механизмы — управляемые приближения, не полная модель плазмы или прибора.
Не моделируются LTE/non-LTE популяции, self-absorption и измеренная QE-кривая.
Диагностические линии не являются доказанным объяснением ML-решения. Ограничения:
[физические допущения](docs/physics_assumptions.md),
[внешняя валидация](docs/external_validation.md).

## Артефакты, хранение и локальный режим

Model manifest сохраняет версии, порядок классов, ion stage, grid/preprocessing,
source/checksum, параметры генератора, dataset/split IDs и hashes, seed, thresholds,
calibration, метрики и зависимости. При загрузке проверяются совместимость и
целостность. Dataset содержит observed/clean spectra, labels и base IDs; true labels
не поступают в predictor. Серии сохраняют raw runs, а экспорт позволяет проверить
сохранённую запись независимо от UI, без локальных путей и исполняемых моделей.

История spectra/predictions и durable jobs переживают перезапуск; записи защищены
атомарной заменой и локальными POSIX locks. Worker выполняет серию в отдельном
subprocess с лимитами. После прерывания незавершённое задание не становится ложным
`completed`. Есть квоты, request ID, явное удаление спектра и offline backup/restore
с проверкой checksum. Команды и ограничения восстановления описаны в [API](docs/api.md).

Запускайте сервис на loopback одного компьютера. Несколько машин, сетевые файловые
системы и совместный сетевой доступ не входят в выбранный режим. `.env` исключён
из Docker build context. Локальные модели, измерения и отчёты исключены из Git;
компактные сохранённые справочники и manifests происхождения версионируются.
Joblib-артефакты должны происходить из доверенного источника: SHA-256 подтверждает
целостность, но не доверие к автору.

## Генерация из CLI и расширение

```bash
scripts/uv.sh run --frozen --all-packages --no-sync spectralab generate \
  --config ml/configs/generate_demo.json --output artifacts/generated-spectrum.json
```

Опциональный `--reference-cache` выбирает импортированный справочник. Используйте
элементы/стадию с линиями в каталоге и согласованный диапазон.

Новый элемент требует изменения project config и трассируемого справочника,
перезапуска API и переобучения. Интерфейс получает классы и ограничения из API.
Новая модель реализует `MultiLabelEstimator.fit(X, y)` и `scores(X)` с матрицей
N × C в сохранённом порядке классов; добавляется в training/registry, задаёт
`score_kind`, calibration и формат артефакта. Требуются совместимость preprocessing,
разделение split, настройка порогов вне test и round-trip проверки.

## Проверка и дальнейшая работа

`make test` проверяет ML, API и компоненты UI. `make lint` запускает Ruff, ESLint и
TypeScript; `make build` — production UI. `make api-types` пересоздаёт типы OpenAPI.
CI выполняет основные проверки, demo, аналитику и браузерные E2E с диагностическими
артефактами. Измерение графика на 100 000 точек, сценарии и условия проверки:
[frontend validation](docs/frontend-validation.md). Plotly загружается лениво;
предупреждение Vite о чанке около 1 MB не является ошибкой сборки.

Фактические результаты и ограничения: [research results](docs/research_results.md),
[verification](docs/verification.md). Статусы выполненных задач, внешних блокеров и
следующие улучшения: [TODO](docs/todo.md).
