# SpectraLab

SpectraLab — учебно-исследовательский прототип, который по синтетическому атомному
эмиссионному спектру оценивает присутствие элементов `H`, `He`, `Na`, `Mg`, `Ca`, `Fe`.
Это multi-label распознавание, а не количественный анализ: `component_weights` — веса
вкладов генератора, не концентрации, массовые доли или проценты. На реальных измерениях
модель не валидировалась.

Проект состоит из трёх частей: `ml/` содержит справочник, генератор, preprocessing,
модели и эксперименты; `backend/` вызывает этот пакет через FastAPI; `frontend/` —
русский React-интерфейс, работающий с API. Обучение не выполняется внутри HTTP-запроса.

## Быстрый запуск

Нужны Python **3.12.14**, Node **22.14.0** и npm **10.9.2**. Версии указаны
в `.python-version`, `.nvmrc` и manifests, зависимости — в `uv.lock` и
`frontend/package-lock.json`. `make setup` устанавливает uv 0.6.17, если его нет.
С nvm выполните `nvm install && nvm use` перед установкой.

```bash
make setup
make demo
make dev
```

`make demo` создаёт небольшой синтетический датасет в
`data/processed/<dataset_hash_16>/`, обучает локальные артефакты в
`artifacts/models/` и запускает API→ML smoke. `make dev` поднимает backend и frontend;
интерфейс доступен на http://localhost:5173, Swagger — http://localhost:8000/docs.
Ctrl+C останавливает оба процесса. Только backend: `make api`.
Docker Compose публикует интерфейс на http://localhost:8080 и API на порту 8000;
остановите `make dev` перед запуском Compose. `docker compose down` останавливает
контейнеры, сохраняя локальные данные и артефакты.

Полезные точные команды:

```bash
make test
make lint
make build
make api-types
make experiment CONFIG=ml/configs/experiments/noise_robustness.yaml
make analytics
docker compose up --build
```

`make experiment` создаёт новый неизменяемый каталог
`reports/experiments/<run_id>-<UTC timestamp>-<random8>/`; повторный запуск не
перезаписывает предыдущий. До обучения доступен встроенный `builtin-nnls`; обучаемые
`baseline-nnls`, `logistic-ovr` и `random-forest` становятся ready после `make demo`.

## Входной CSV и API

Загрузочный CSV содержит ровно два столбца. Минимальный пример формата (для содержательного анализа нужна более плотная сетка):

```csv
wavelength_nm,intensity
350.0,0.01
575.0,1.0
800.0,0.02
```

Требуются конечные числа, строго возрастающие длины волн без дублей, не нулевой сигнал,
полное покрытие 350–800 nm, единицы `nm` и среда `vacuum`. API ограничивает upload 5 MB
и 100 000 точек. См. `docs/api.md`.

## Справочник и данные

Основной demo-сценарий всегда использует `data/fixtures/demo_lines.csv`: это
`demo_fixture`, вручную отобранный учебный набор шести классов, явно не NIST. Он должен
быть виден в UI, манифестах и отчётах.

Отдельный `data/fixtures/nist_na_i_vacuum_580_600.csv` — проверочная сохранённая
двухстрочная выгрузка Na I NIST ASD в vacuum nm. Она тестирует разбор формата, но не
служит основным шестиэлементным набором demo.

Импорт выполняется без сети:

```bash
scripts/uv.sh run --frozen --all-packages --no-sync spectralab import-reference \
  --input path/to/export.csv --metadata path/to/export.metadata.json \
  --cache data/cache/reference.json --report reports/reference_import.json
```

Импортёр возвращает `ReferenceCatalog`. Для пользовательского исследования такой
каталог можно передать из Python в `generate_spectrum`, `build_demo_dataset`,
`make_reference_templates` и `train_and_save_models` вместе с согласованным
`class_order` и `SpectrumPreprocessor`. CLI-команда `spectralab demo` намеренно остаётся
привязана к `demo_fixture`; импорт сам по себе не заменяет его для backend/demo.

Jupyter не входит в минимальный runtime: дополнительная группа `notebooks` в
`ml/pyproject.toml` содержит `ipykernel` и `jupyterlab`.

## Модель, результаты и ограничения

Встроенный NNLS возвращает `score_kind: nnls_coefficient_times_fit_quality` и
`calibrated: false`. Его score — произведение неотрицательного коэффициента шаблона и
глобального качества реконструкции; это не вероятность. У Logistic OVR и Random Forest
тоже `calibrated: false`; API всегда возвращает тип score, пороги и оценки всех классов.
Пустой список обнаружений означает лишь отсутствие превышения порогов поддерживаемых
классов.

## Артефакты и происхождение

Каждый model manifest хранит `model_id`, версию и тип, дату, фиксированный порядок
классов, границы/шаг/число точек сетки, units и wavelength medium, preprocessing,
пороги и правило их выбора, score/calibration, источник данных, параметры генератора,
dataset/split hash, seed, dependency versions, validation/test metrics, applicability,
reference metadata, split IDs и SHA-256 joblib. При загрузке сверяются ID, порядок
классов, preprocessing/grid, source/checksum, версия scikit-learn и checksum модели.

Сохранённый dataset содержит `spectra.npz` (wavelengths, observed/clean signal,
labels, base IDs) и `metadata.json` с hash, class order, reference, split IDs и
параметрами/метками образцов. Запуск эксперимента хранит configuration, seed, status,
timestamps, reference metadata, dataset/split IDs/hashes, preprocessing, версии,
trained model metadata, runs, metrics и limitations. Численные результаты появляются
только после фактического completed run.

Генератор использует нейтральные атомы, интегрированный по пикселям гауссов профиль,
аддитивный Gaussian noise, простой фон, заданный сдвиг и вариацию амплитуд. Он не
моделирует реальные условия плазмы, другие стадии ионизации, Voigt-профили, detector
physics или неизвестные элементы. Подробности: `docs/physics_assumptions.md`,
`docs/experiment_protocol.md`, `docs/sources.md`, `docs/roadmap.md`.

## Генерация из CLI

Создайте JSON-файл параметров, например `ml/configs/generate_demo.json`, затем:

```sh
scripts/uv.sh run --frozen --all-packages --no-sync spectralab generate \
  --config ml/configs/generate_demo.json --output artifacts/generated-spectrum.json
```

Опциональный `--reference-cache data/cache/reference.json` использует импортированный
справочник. Выбирайте только элементы с линиями в этом справочнике и рабочем диапазоне.
Параметры генератора совпадают с общим `GenerationConfig` и API.

## Проверка

`make test` проверяет импорт NIST, площадь/seed/SNR генератора, групповые split,
validation-пороги, загрузку артефактов, отсутствие утечки истины через API и состояния UI.
`make lint` выполняет Ruff, ESLint и TypeScript; `make build` собирает production UI.
CI воспроизводит эти команды, demo и аналитику; `make api-types` пересоздаёт типы OpenAPI.
Plotly загружается отдельным чанком; Vite предупреждает о его размере около 1 MB.
Это не ошибка сборки. Численные результаты маленького smoke не являются оценкой
качества на реальных спектрах.

Локальные модели, спектры, кэши и отчёты исключены из Git. Сервис рассчитан на локальную
работу с одним процессом API. Каталог моделей должен содержать только доверенные
артефакты: SHA-256 проверяет целостность, но не доказывает доверие к автору joblib.

## Добавление элемента или модели

Новый элемент добавляется в `ml/configs/project.yaml` и в трассируемый справочник:
нужны нейтральные линии vacuum nm в рабочем диапазоне. Перезапустите API и заново
выполните обучение; старые артефакты с другим `class_order` будут отклонены.
Интерфейс получает список элементов из `/elements`.

Новая модель реализует `MultiLabelEstimator.fit(X, y)` и `scores(X)` из
`ml/src/spectralab_ml/models/estimators.py`: выход — матрица N × C в сохранённом
порядке классов. Задайте `score_kind` и `calibrated`; добавьте фабрику в `training.py`
и ID в разрешённые модели `experiments.py` / `inference/predictor.py`. Используйте
общую предобработку, validation-пороги и `save_artifact`; добавьте round-trip тест.
Это же точка подключения будущей опциональной 1D CNN.

Фактические команды и ограничения проверки: [docs/verification.md](docs/verification.md).

План следующих этапов для ML, backend и frontend: [TODO](docs/todo.md).
