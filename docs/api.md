# API SpectraLab

**База:** `http://127.0.0.1:8000/api/v1`. OpenAPI доступен в `/docs`; `make api-types`
обновляет `artifacts/openapi.json` и TypeScript-типы. API 1.1 сохраняет старые
маршруты и добавляет новые поля с совместимыми значениями по умолчанию.

Ошибки: `{"error":{"code":"invalid_value","message":"Описание","details":null}}`.
Валидация возвращает 422 с деталями полей, отсутствие записи — 404, превышение
размера запроса — 413, конфликт idempotency key — 409, исчерпание квоты — 507.
Каждый ответ содержит `X-Request-ID`; безопасный переданный ID (1–64 символа
`A-Za-z0-9._-`) сохраняется, иначе создаётся новый. Этот ID записывается в JSON-лог.

## Возможности и справочник

| Метод | Путь | Содержимое |
| --- | --- | --- |
| GET | `/health` | статус, готовность справочника, ready-модели (лёгкий liveness probe) |
| GET | `/system/status` | детальное состояние worker, очередей, квот хранилища и проверенной копии |
| GET | `/capabilities` | версии схем; диапазон, сетка, units, medium; ограничения CSV; defaults/limits генератора; квоты и параметры очереди |
| GET | `/elements` | классы, активная стадия ионизации, metadata справочника, число линий |
| GET | `/models` | ID/версия/ready, `score_kind`, `calibrated`, источник, область применимости, `diagnostic_code` и `unavailable_reason` |

Клиент получает настройки из capabilities. Ввод — vacuum nm; преобразование air
выполняется явно вне HTTP, автоматической смены среды в этих маршрутах нет. Приборный маршрут
`/measured-predictions` отдельно поддерживает явный air→vacuum. `/models` объясняет
необученную/повреждённую/несовместимую модель безопасным кодом, без локального пути.
CNN доступна только с optional runtime и совместимым артефактом; отсутствие runtime
не препятствует запуску сервиса.

`SPECTRALAB_REFERENCE_CACHE` выбирает проверенный локальный cache; необязательный
`SPECTRALAB_REFERENCE_SHA256` фиксирует SHA-256 **байтов файла**. Внутренний checksum
cache проверяется независимо. При отсутствии настройки действует project config,
по умолчанию `demo_fixture`. Выбор загружается при старте и един для `/elements`,
генерации и registry; для смены нужен перезапуск. HTTP не принимает путь справочника.
Модель с другим source/checksum, class order, ion stage или preprocessing отклоняется.

## Состояние системы, worker и хранилища

Маршрут `/health` сохраняет обратную совместимость и лёгкий контракт (`{"status":"ok",...}`),
служа быстрым liveness probe. Для детальной наблюдаемости локального рабочего места
одного исследователя предназначен `GET /system/status`:

```bash
curl -s http://127.0.0.1:8000/api/v1/system/status | jq
```

Ответ включает:
- `worker`: статус (`state` и синоним `status`: `absent`, `running`, `stopped`, `stale`), `worker_id`, `pid`,
  `current_task` (`kind`, `task_id`, `description`, `started_at`), `heartbeat_interval_seconds`,
  `stale_threshold_seconds` (по умолчанию 20 с) и диагностическое сообщение `message`.
  Worker удерживает неблокирующую host-lock блокировку `fcntl.flock(LOCK_EX | LOCK_NB)`
  на файле `.worker.lock` в каталоге заданий. Если блокировка не удерживается — worker `absent`.
  Если удерживается, статус определяется по возрасту `.worker_heartbeat.json`: свежий —
  `running`, старше порога `worker_heartbeat_stale_seconds` (по умолчанию 20 с) — `stale`.
  При повреждении файла heartbeat возвращается безопасный статус `stale` с сохранением работоспособности API.
- `queues`: счётчики заданий `jobs` (`total`, `queued`, `running`, `completed`, `failed`, `cancelled`,
  `quota_pending`, `remaining_pending`), `batches` (`total`, `draft`, `queued`, `running`, `completed`,
  `partial`, `failed`, `cancelled`), а также общий счётчик активных задач `active_total`.
- `storage`: учёт занятого места и квот по категориям: `spectra`, `predictions`,
  `measured_predictions`, `jobs`, `batches`, `batch_inputs`, `batch_cache`. Для каждой
  категории возвращаются `record_count`, `size_bytes`, `quota_records`, `remaining_records`,
  `quota_bytes`, `remaining_bytes`.
  Суммарные поля `total_used_bytes` и `quota_headroom_bytes` отражают общий объём и запас до квоты.
  Блок `disk` (`total_bytes`, `used_bytes`, `free_bytes`) даёт данные файловой системы.
- `backup`: сведения о последней проверенной резервной копии (`last_archive_verified`: `completed_at`,
  `archive_name`, `archive_sha256`, `archive_size_bytes`, `records_count`, `verification_passed`, `details`)
  и восстановлении (`last_restore_verified`: `completed_at`, `archive_name`, `archive_sha256`,
  `restored_records_count`, `verification_passed`, `details`), считываемые из `.backup_status.json`.
  При повреждении структуры служебного файла возвращается `null` для повреждённой записи без аварии HTTP 500.

## Спектры и история

`POST /spectra/generate` принимает 1–3 уникальных элемента и столько же положительных
`component_weights` (это относительные веса компонентов, не концентрации):

```json
{"elements":["H","Na"],"component_weights":[1.0,0.7],"instrumental_fwhm_nm":0.5,"sampling_step_nm":0.05,"snr_db":20,"seed":42,"measurement_metadata":{"sample_id":"sample-01"}}
```

Дополнительны `background_level`, `background_slope`, `calibration_shift_nm`,
`amplitude_variation`, `profile` (`gaussian`/`voigt`), `lorentzian_fwhm_nm`,
`noise_model` (`gaussian`/`poisson_read`), `photon_scale`, `read_noise_std`,
`response_slope`, `saturation_level`, `ion_stage`. Неуказанная стадия берётся из
активного проекта. Несовместимая стадия отклоняется. `poisson_read` требует
`snr_db: null`; Lorentzian FWHM применим к Voigt. Шаг обязан делить диапазон без
остатка и укладываться в лимит точек. Все числовые пределы выдаёт capabilities.

Ответ сохраняет ID, timestamp, schema version, массивы, clean signal, true labels,
параметры/seed, base sample ID и источник. Истина генератора передаётся только UI
и экспорту; predictor получает исключительно измеренные массивы.

`POST /spectra/upload` — multipart с `file`, `units=nm`, `wavelength_medium=vacuum`
и необязательным `measurement_metadata` как JSON-строкой. CSV содержит ровно
`wavelength_nm,intensity`, конечные числа, возрастающую сетку и весь диапазон
проекта. Лимиты по умолчанию: 100 000 точек и 5 MB. SNR загрузки остаётся
`snr_db: null`, `snr_kind: unknown`.

| Метод | Путь | Действие |
| --- | --- | --- |
| GET | `/spectra?limit=20&cursor=...` | страница истории без массивов |
| GET | `/spectra/{id}` | полная сохранённая запись |
| PATCH | `/spectra/{id}/metadata` | обновить только пользовательские metadata |
| DELETE | `/spectra/{id}` | явно удалить ровно одну запись, 204 |

Metadata: необязательные `sample_id`, `instrument_id`, `session_id` (до 200 символов),
`calibration` (описание до 2000), `notes` (до 4000). PATCH сохраняет пропущенные поля;
`null` очищает выбранное поле. `computed` отдельно содержит вычисленные характеристики;
пользователь не может подменить их через metadata.

Записи и восстанавливаемый индекс переживают перезапуск. Повреждённая запись
пропускается в истории, прямой GET объясняет ошибку; остальные записи остаются
доступны. Повреждённый индекс пересоздаётся. Для исправления повреждённой записи
используйте offline backup или явно удалите её и повторите импорт. Удаление спектра
не удаляет сохранённые predictions: каждый prediction содержит снимок исходных данных.

## Предсказание

`POST /predict` допускает ровно один способ ввода:

```json
{"input_type":"spectrum_id","spectrum_id":"<32 hex>","model_id":"builtin-nnls"}
```

```json
{"input_type":"arrays","wavelength_nm":[350,800],"intensity":[0.1,0.2],"units":"nm","wavelength_medium":"vacuum","model_id":"builtin-nnls"}
```

Ответ включает `prediction_id`, `spectrum_id` (null для массивов), `created_at`,
версию схемы/модели, источник, оценки, `score_kind`, `calibrated`, per-class thresholds,
detected elements, предупреждения, время и диагностические reference lines.
`GET /predictions/{prediction_id}` восстанавливает сохранённый результат.

NNLS score `nnls_coefficient_times_fit_quality` не является вероятностью.
Ближайшая reference line — диагностика, не доказательство причины ML-решения.
Пустой detected_elements не означает обнаружение неизвестного вещества.
API не принимает pickle/joblib/checkpoint и не обучает модели в HTTP-процессе.

## Каталог и серии экспериментов

Старый `GET /experiments` сохраняет ответ-массив. Новый
`GET /experiments/catalog?limit=20&status=completed&protocol=controlled_sweep&reference_source=demo_fixture`
возвращает `{items,next_cursor}`; limit от 1 до 100. Сортировка по
`(created_at, run_id)` убывающая, включая одинаковые timestamps. Cursor фиксирует
границу и фильтры. Передайте его без изменений для следующей страницы; смена фильтра
требует сброса cursor (старый cursor возвращает 422). Новые записи видны при обновлении
первой страницы и не сдвигают пройденные границы.

`GET /experiments/{run_id}` читает одиночный запуск или серию, включая дочерние run-ID.
Незавершённые записи имеют реальные `running`, `failed`, `incomplete`, `cancelled`
статусы и причину ошибки. Серия имеет `artifact_type: experiment_series`, ожидаемые
и успешные seeds, seed_runs с config/source hashes, failed_runs, incomplete_runs,
aggregates. Агрегат содержит mean/std/count по модели, условию и метрике (в том числе
single/mixture). В сравнении только seeds с полной матрицей моделей/условий;
`std` использует `ddof=1`, при count < 2 равен null. Неуспешные seeds не заменяются нулями.

## Экспорт

`GET /spectra/{id}/export`, `/predictions/{id}/export`, `/experiments/{run_id}/export`:

- `format=zip` (по умолчанию): `record.json`, доступный `spectrum.csv`, `manifest.json`.
  Для серии включены исходные записи seeds как `runs/{run_id}/record.json`.
- `format=json`: `{manifest,record,related_records}` с той же сохранённой записью.
- `format=csv`: точные исходные `wavelength_nm,intensity`; только спектр/prediction.

Manifest включает версии, IDs, source, model version и SHA-256/размер каждого файла.
Checksum считается по точным UTF-8 байтам; manifest сам себя не хеширует.
`X-Content-SHA256` проверяет весь полученный attachment. JSON/CSV доступны без UI;
CSV для полного provenance удобно получать внутри ZIP. Prediction экспортирует
снимок на момент анализа, даже если metadata исходного спектра позднее поменялись.
Пути, executable model artifacts и произвольные вложения не выдаются; разрешены
только валидные сохранённые IDs и фиксированные имена файлов.

## Фоновые задания

Запустите **отдельный** worker: `.venv/bin/python -m app.worker`; `--once` обработает
не более одного ожидающего задания и завершится. HTTP только сохраняет очередь.

```json
{"seeds":[11,22,33,44,55],"config_name":"demo","idempotency_key":"study-2026-10-13-a"}
```

`POST /jobs` возвращает 202 и durable job-ID. `config_name=demo` использует smoke,
`default` — baseline_comparison; клиент не задаёт пути/произвольный код.
Seeds уникальны, от 1 до 10 штук. `GET /jobs` и `/jobs/{id}` показывают очередь,
реальные статусы и `progress: {completed,total}` по завершённым seed-прогонам.
`POST /jobs/{id}/cancel` отменяет queued сразу, running после остановки subprocess.
Повторный POST с тем же ключом/телом возвращает тот же ID; другое тело с этим ключом —
409. Явный повтор failed/cancelled создаётся с новым ключом.

Worker удерживает один lock на host/storage. Серия выполняется в subprocess с одним
BLAS/OpenMP потоком, лимитом CPU/времени (3600 s), файла (512 MB), и на Linux — памяти
(4096 MB по умолчанию). macOS ограничивает CPU/время/число seeds, но RLIMIT_AS там не
используется. При остановке worker child завершается; после перезапуска незавершённые
jobs становятся failed с `worker_interrupted`, без ложного completed и автоматического
повторного обучения. Для completed нужен успешный subprocess и завершённый series report.
Health/predict выполняются независимо от обучения; прогресс не является оценкой оставшегося времени.

## Пакетный анализ папки CSV

Браузер передаёт выбранные пользователем файлы. HTTP не принимает путь к папке
сервера: `relative_path` — только относительное имя внутри манифеста; абсолютные
пути и `..` запрещены. Формат каждого CSV и ограничения совпадают с одиночной
загрузкой: `wavelength_nm,intensity`, vacuum/nm, полный диапазон проекта.

1. `POST /batches` создаёт durable `draft` и фиксирует состав папки и модели:

   ```json
   {"name":"Session 12","model_ids":["builtin-nnls"],"expected_files":[{"relative_path":"folder/sample.csv","sha256":"<64 lowercase hex>","size_bytes":36}],"idempotency_key":"session-12-import","measurement_metadata":{"session_id":"12"}}
   ```

2. `POST /batches/{id}/files` принимает multipart `file`, `relative_path`,
   `units=nm`, `wavelength_medium=vacuum`. Точные байты обязаны совпасть с SHA-256
   и размером манифеста. Сервер проверяет CSV и сохраняет файл как `ready` либо
   `invalid` с причиной. Повтор идентичной загрузки возвращает ту же запись.
3. `POST /batches/{id}/rejections` сохраняет отклонённый клиентом файл без загрузки
   его содержимого: `{relative_path,sha256,size_bytes,error_code,error_message}`.
   SHA-256 может быть `null` только для декларации файла, который нельзя прочитать
   или допустимо отклонить по размеру. Это клиентская диагностика
   (`validation_origin=client`), а не проверка сервером.
4. `POST /batches/{id}/start` возвращает 202 и ставит анализ в очередь, когда все
   позиции получили файл или отказ. Пропущенная загрузка оставляет `pending_upload`
   и возвращает 409. После обновления страницы пользователь повторно выбирает папку;
   браузер сопоставляет имена, размеры и checksums с сохранённым манифестом.

`GET /batches` возвращает список, `GET /batches/{id}` — полный снимок с файлами,
модельными идентичностями и результатами каждой пары файл×модель. Статусы задания:
`draft`, `queued`, `running`, `completed`, `partial`, `failed`, `cancelled`.
`progress.completed` считает все обработанные пары, включая неуспешные;
`progress.failed` показывает их число отдельно, `total` — размер матрицы.
Некорректный файл попадает в отчёт с причиной для каждой выбранной модели.

`POST /batches/{id}/cancel` останавливает очередь либо subprocess.
`POST /batches/{id}/resume` повторяет только незавершённые и неуспешные пары;
готовые predictions и их IDs сохраняются. Отменённый draft с недостающими файлами
возвращается в `draft`. Невалидные исходные байты неизменяемы: исправленный CSV
запускайте новым пакетом. При аварии worker запись получает `worker_interrupted`
и требует явного resume. Один worker обрабатывает пакеты и исследовательские jobs;
`--batches-only` ограничивает его пакетами CSV. Все вычисления выполняются в
отдельном subprocess с теми же лимитами ресурсов, что исследовательские jobs.

Повтор create с тем же ключом/телом возвращает тот же ID, другое тело — 409.
Кэш вычислений использует SHA-256 исходных байтов и идентичность модели, включающую
checksum артефакта/manifest, справочник, preprocessing, классы, ионную стадию,
пороги, версии библиотек и код inference. Изменение идентичности до обработки
останавливает соответствующую пару с объяснением; создайте новый пакет.
Кэш не переносит пользовательские metadata: каждый результат содержит собственный
снимок provenance. Изменение/удаление отдельной записи истории не меняет пакет.
Кэш ограничен размером; его заполнение не мешает дальнейшим вычислениям.

`GET /batches/{id}/export?format=json|csv|zip`:

- JSON: `{manifest,record}` со всеми файлами, статусами, причинами отказов,
  снимками моделей и результатами.
- CSV: одна строка на пару файл×модель, включая IDs, checksum, metadata, score kind,
  thresholds и warnings. Пользовательский текст защищён от spreadsheet-formulas.
- ZIP: `report.json`, `summary.csv`, точные загруженные байты
  `inputs/{file_id}.csv`, `manifest.json` с SHA-256 и размерами. Имена пользователя
  остаются в отчёте; структура ZIP использует только безопасные IDs.

Локальная CLI позволяет анализировать папку без браузера:

```bash
.venv/bin/python -m app.batch ./measurements --models builtin-nnls --output /tmp/batch.zip
.venv/bin/python -m app.batch --resume <batch-id> --output /tmp/batch-resumed.json
.venv/bin/python -m app.batch ./measurements --enqueue
.venv/bin/python -m app.worker --batches-only
```

Сканирование рекурсивное, только `.csv`, без перехода по symlinks. Без `--enqueue`
CLI выполняет пакет через worker subprocess или ждёт уже запущенный worker.
Повтор с теми же именем пакета, файлами и моделями использует тот же batch-ID.
`--resume` продолжает сохранённый пакет; для недостающих загрузок укажите исходную
папку ещё раз. Изменившийся файл не подменяет заявленный checksum. Выход 0 означает
полное завершение, 2 — частичный/ошибочный/отменённый пакет; отчёт всё равно доступен.
Существующий файл `--output` не перезаписывается.

Лимиты публикуются в `capabilities.batches`: до 200 файлов, до 5 моделей,
5 MB на CSV и 100 MB загруженных байтов на пакет. Отклонённые клиентом файлы
не расходуют лимит загруженных байтов. Дополнительно по умолчанию: 100 пакетов,
500 MB суммарных исходных CSV, 100 MB кэша и общие квоты spectra/predictions.
Отчёт сохраняет предупреждения модели: пакетная обработка не является валидацией
состава реального образца.

## Приборные профили и анализ реальных измерений

Для анализа реальных физических измерений (в отличие от синтетических спектров) API поддерживает
исследовательские приборные профили (например, `calcium-v4`), учитывающие спектральные полосы
прибора, разрывы, калибровку и перевод air→vacuum.

| Метод | Путь | Действие |
| --- | --- | --- |
| GET | `/instrument-profiles` | список зарегистрированных доверенных профилей |
| GET | `/instrument-profiles/{id}` | метаданные профиля: полосы, пороги, требования к шкале и покрытию |
| POST | `/measured-predictions` | анализ измеренного спектра через выбранный профиль (multipart или JSON) |
| GET | `/measured-predictions/{id}` | сохранённый результат анализа со снимком provenance |
| GET | `/measured-predictions/{id}/export?format=json\|csv\|zip` | экспорт анализа в JSON, CSV или ZIP-архив |

### Метаданные и требования приборного профиля

Каждый профиль (`GET /instrument-profiles/{id}`) описывает поддерживаемые элементы
(`supported_elements`), неоцениваемые элементы (`unknown_elements`), калиброванные
пороги, а также требования:
- `scale_requirements`: рабочая среда `vacuum` nm; при передаче `medium="air"` сервер
  автоматически выполняет точный пересчёт `air → vacuum` по формуле Peck–Reeder 1972
  (Standard Air).
- `coverage_requirements`: список спектральных полос `bands_nm`, максимальный шаг
  дискретизации `max_sampling_step_nm` (0.5 nm) и максимально допустимый внутренний
  разрыв `max_gap_nm` (0.5 nm). Если спектр содержит разрывы внутри полосы больше 0.5 nm,
  запрос отклоняется с кодом 422.

### Отправка измерения на анализ

`POST /measured-predictions` принимает данные в двух взаимоисключающих форматах:
1. **Multipart form-data**:
   - `file`: CSV-файл со спектром (заголовок `wavelength_nm,intensity`).
   - `profile_id`: идентификатор приборного профиля (по умолчанию `calcium-v4`).
   - `metadata`: JSON-строка со схемой метаданных измерения (типизированный `MeasurementMetadataPayload`, обязательны явные сведения о приборе и оси).
   - Дополнительные поля: `instrument_id`, `medium` (`air` или `vacuum`), `units` (строго `nm`), `measurement_id`,
     `physical_sample_id`, `family_id`, `session_id` (длина строковых ID до 128 символов). При передаче они проверяются на строгое
     соответствие блоку `metadata`; противоречащие значения или недопустимые единицы (например, `angstrom`) отклоняются с кодом 422.
2. **JSON**:
   - Спектральные данные передаются ровно через **одно** представление: `csv_content` (строка CSV),
     `points` (список `[{"wavelength_nm": float, "intensity": float}]`) либо согласованная пара массивов
     `wavelength_nm` и `intensity`. Передача нескольких или пустых представлений отклоняется с 422.
   - `profile_id`: идентификатор приборного профиля (по умолчанию `calcium-v4`).
   - `metadata`: типизированный объект метаданных измерения (`MeasurementMetadataPayload`). Вложенные строковые
     идентификаторы (`measurement_id`, `physical_sample_id`, `family_id`, `session_id`) строго валидируются
     (строка длиной до 128 символов; объекты/словари отклоняются без приведения типов через `str`).
   - Верхнеуровневые параметры `instrument_id`, `medium`, `units` (`nm`), `measurement_id`, `physical_sample_id`,
     `family_id`, `session_id`.

#### Обязательные подтверждения прибора и оси (provenance)
Сервер **никогда не подставляет** неподтверждённые сведения об измерении из профиля модели:
- `instrument_id`: идентификатор прибора должен быть явно предоставлен вызывающей стороной
  (в параметрах запроса или в `metadata.instrument_id`) и соответствовать прибору профиля
  (`chemcam-lanl-testbed` для профиля `calcium-v4`).
- `axis`: объект `axis` в метаданных обязателен и должен содержать явные параметры:
  - `units`: единицы измерения (обязательно явно строго `"nm"`; отсутствие, `null` или иные единицы отклоняются с кодом 422).
  - `medium`: среда измерения (`air` или `vacuum`).
  - `source`: идентификатор источника калибровки оси (непустая строка).
  - `source_sha256`: контрольная сумма источника оси (64-значный lowercase hex SHA-256).
- При отсутствии обязательных сведений об измерении, их несовместимости с профилем или противоречии
  между параметрами запроса и метаданными запрос отклоняется со статусом **422 Unprocessable Entity**.

Обработка запроса (валидация схемы Pydantic, парсинг CSV, выполнение ML-модели и сохранение на диск)
вынесена в рабочий пул потоков (`anyio.to_thread.run_sync`), благодаря чему event loop API не
блокируется во время вычислений. Атрибуты образца (`physical_sample_id`, `family_id`, `session_id`,
`measurement_metadata`) сохраняются в неизменном виде, возвращаются в ответах GET, экспортах
и восстанавливаются из backup.

Примеры вызова через curl (на синтетических тестовых данных):

```bash
# 1. Каталог профилей
curl -s http://127.0.0.1:8000/api/v1/instrument-profiles | jq

# 2. Анализ через multipart CSV с явными метаданными прибора и оси
curl -s -X POST http://127.0.0.1:8000/api/v1/measured-predictions \
  -F "file=@measurement.csv" \
  -F 'metadata={
    "schema_version": 1,
    "instrument_id": "chemcam-lanl-testbed",
    "measurement_id": "meas-run-001",
    "physical_sample_id": "SAMPLE-01",
    "axis": {
      "units": "nm",
      "medium": "air",
      "source": "chemcam-wave-calibration-v1",
      "source_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    }
  }' | jq

# 3. Анализ через JSON со списком точек и подтверждённой осью
curl -s -X POST http://127.0.0.1:8000/api/v1/measured-predictions \
  -H "Content-Type: application/json" \
  -d '{
    "profile_id": "calcium-v4",
    "metadata": {
      "schema_version": 1,
      "instrument_id": "chemcam-lanl-testbed",
      "measurement_id": "meas-run-002",
      "physical_sample_id": "SAMPLE-02",
      "axis": {
        "units": "nm",
        "medium": "air",
        "source": "chemcam-wave-calibration-v1",
        "source_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      }
    },
    "points": [
      {"wavelength_nm": 240.9267, "intensity": 1.0},
      {"wavelength_nm": 240.9767, "intensity": 1.2}
    ]
  }' | jq

# 4. Получение сохранённого результата по ID
curl -s http://127.0.0.1:8000/api/v1/measured-predictions/<prediction_id> | jq

# 5. Экспорт ZIP-архива (содержит prediction.json, record.json, summary.csv, input.csv, manifest.json)
# Обратите внимание: флаг -i не используется, чтобы заголовки ответа не попали в архив
curl -s "http://127.0.0.1:8000/api/v1/measured-predictions/<prediction_id>/export?format=zip" \
  -o measured_prediction.zip
```

### Защита от утечки меток, селективный отказ и неизвестные элементы

- **Защита от утечки и подтасовки (anti-cheating):** CSV-файлы с заголовками, содержащими
  химические концентрации или оксидные метки (например, `Na2O`, `MgO`, `CaO`, `FeOT`,
  `target`, `concentration`), а также нечисловые значения (NaN, Inf) отклоняются сервером
  с кодом 422 до обработки модели.
- **Селективный отказ (selective refusal):** если уверенность модели недостаточна для
  надёжного решения, элемент получает `status: "unknown"`, `detected: null`, `accepted: false`
  и пояснение `reason: "below_frozen_evidence_or_score_margin"`.
- **Неоцениваемые элементы:** элементы `H` и `He`, не оцениваемые приборным профилем
  `calcium-v4`, возвращаются со значением `status: "unknown"`, `detected: null`, `score: null`,
  `accepted: false` и пояснением `reason: "unsupported_by_profile"`.
- **Экспорт с provenance:** ZIP-экспорт содержит исходный загруженный CSV `input.csv`,
  `prediction.json`, `summary.csv` и `manifest.json` с контрольными суммами SHA-256 каждого файла.
  Ответ сопровождается HTTP-заголовком `X-Content-SHA256`.

## Локальный режим, квоты и резервная копия

Выбран режим одного локального исследователя. Запускайте API на `127.0.0.1`; сервис
не содержит пользователей/владения данными/auth и не предназначен для публикации
в общей сети. CORS по умолчанию — `http://localhost:5173`; это не аутентификация.
Локальные API/worker/maintenance синхронизируют запись POSIX file locks и атомарной
заменой файлов с fsync. Несколько машин и сетевые файловые системы не поддерживаются.
`.env` и `.env.*` исключены из Docker build context.

Настройки `SPECTRALAB_*`: `MAX_REQUEST_BYTES` 6 MB, `MAX_UPLOAD_BYTES` 5 MB,
`MAX_MEASURED_REQUEST_BYTES` 32 MB только для POST `/measured-predictions`.
Лимит декодированного CSV измерения — 16 000 000 байт, максимум 100 000 точек;
дополнительный объём HTTP-конверта нужен для JSON-экранирования и multipart metadata.
Превышение любого байтового лимита возвращает 413. Остальные квоты:

`MAX_STORED_SPECTRA` 500, `MAX_STORAGE_BYTES` 500 MB,
`MAX_STORED_PREDICTIONS` 1000, `MAX_PREDICTION_STORAGE_BYTES` 100 MB,
`MAX_STORED_MEASURED_PREDICTIONS` 1000, `MAX_MEASURED_PREDICTION_STORAGE_BYTES` 100 MB,
`MAX_PENDING_JOBS` 10, `MAX_JOB_RECORDS` 1000, `JOB_TIMEOUT_SECONDS` 3600,
`WORKER_MEMORY_MB` 4096, `MAX_BATCH_FILES` 200, `MAX_BATCH_RECORDS` 100,
`MAX_BATCH_UPLOAD_BYTES` 100 MB, `MAX_BATCH_INPUT_STORAGE_BYTES` 500 MB,
`MAX_BATCH_CACHE_BYTES` 100 MB. Автоматического удаления пользовательских результатов нет.
Для освобождения места явно удаляйте спектры через DELETE; offline архивирование
prediction/job/report каталогов выполняет владелец после backup.

Остановите API и worker перед согласованной резервной копией:

```bash
.venv/bin/python -m app.maintenance backup /tmp/spectralab-backup.zip
SPECTRALAB_ARTIFACTS_DIR=/tmp/restored-artifacts SPECTRALAB_REPORTS_DIR=/tmp/restored-reports .venv/bin/python -m app.maintenance restore /tmp/spectralab-backup.zip
```

### Потоковое копирование и верификация архива

- **Потоковый backup:** архив записывается и его контрольные суммы проверяются
  чанками по 64 KB. Restore проверяет архив до записи, но затем читает записи
  целиком в память; лимит суммарного распакованного размера — 1 500 000 000 байт.
  Это не потоковое восстановление с постоянным потреблением памяти.
- **Временный файл на том же носителе:** архив создаётся под именем `.{name}.{uuid}.tmp`
  в каталоге назначения, предотвращая неполные файлы при сбое.
- **Предварительная валидация (archive pre-verification):** перед публикацией созданный архив
  проверяется на физическую целостность zip-контейнера, читается `manifest.json`, и проверяются
  контрольные суммы всех записей.
- **Атомарная публикация без перезаписи:** готовый проверенный архив перемещается на целевое имя.
  Если файл с таким именем уже существует, операция немедленно прерывается с ошибкой,
  существующий архив сохраняется, а временный файл `.tmp` удаляется.
- **Сохранение статуса проверенной копии:** при успешной публикации в каталог артефактов
  записывается файл `.backup_status.json` (`last_archive_verified` с именем, SHA-256, размером,
  количеством записей и временем). При операции `restore` аналогично фиксируется запись
  `last_restore_verified`.
- **Состав архива:** копируются JSON-записи spectra, predictions, jobs, batches, experiments,
  дочерние seeds, исходные CSV пакетов и записи реальных измерений `measured_predictions`
  (вместе с исходными сырыми CSV). Кэш batch inference и служебные логи не включаются.
  Временный heartbeat worker (`.worker_heartbeat.json`) намеренно исключён из архива,
  чтобы после восстановления не возникало ложного статуса работающего worker.
  Модели, секреты и `.env` не включаются; доверенные модели и выбранный reference cache
  сохраняйте отдельно. Restore проверяет весь архив, IDs, имена и checksums до записи,
  отклоняет traversal, работает только с пустыми каталогами и не перезаписывает данные.
  Восстановленные running jobs/batches становятся failed и требуют явного повтора/resume. Индекс
  истории строится из восстановленных записей. При аварии диска во время восстановления
  используйте новый пустой каталог и повторите restore из той же копии.

Проверено: backend API/contract tests, cursor при одинаковых timestamps и вставке,
metadata/restart/corrupt record, checksums экспорта, параллельная запись из двух
процессов, streaming backup и preverified restore и отклонение подмены/traversal, idempotency, cancellation,
resource timeout и recovery. Отдельный реальный smoke worker завершил seed 101 с
тремя моделями; health и predict отвечали во время обучения, series и дочерний run
прошли HTTP-контракт. Синтетические результаты не валидируют реальные измерения.
Пакетные проверки покрывают client/server отказы CSV, возобновление загрузки и
очереди, checksums/provenance, повторное использование вычислений без дублирования
predictions, смену model identity, лимит кэша, streaming backup и preverified restore исходных байтов
и измеренных спектров, реальный worker subprocess и повторный CLI-запуск.
