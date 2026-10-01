# API SpectraLab

**База:** `http://127.0.0.1:8000/api/v1`. Интерактивная спецификация доступна как
`/docs`, OpenAPI экспортируется командой `make api-types` в `artifacts/openapi.json`.
Все ошибки имеют вид `{"error":{"code":"invalid_value","message":"Описание ошибки","details":null}}`.

## Системные запросы

| Метод | Путь | Содержимое |
| --- | --- | --- |
| GET | `/health` | статус, готовность справочника, ready-модели |
| GET | `/elements` | классы, `ion_stage`, метаданные справочника и число линий |
| GET | `/models` | ID, версия, готовность, `score_kind`, `calibrated`, источник и область применимости |
| GET | `/experiments` | только фактически сохранённые запуски |
| GET | `/experiments/{run_id}` | неизменяемая запись запуска `result.json` |

## Спектры

`POST /spectra/generate` принимает 1–3 уникальных элемента и столько же положительных
`component_weights`:

```json
{"elements":["H","Na"],"component_weights":[1.0,0.7],"instrumental_fwhm_nm":0.5,"sampling_step_nm":0.05,"snr_db":20,"seed":42}
```

Дополнительны `background_level`, `background_slope`, `calibration_shift_nm`,
`amplitude_variation`; `snr_db: null` даёт режим без добавленного шума. Ответ сохраняет
спектр и возвращает `spectrum_id`, массивы, `clean_signal`, генераторные `true_labels`,
параметры, `base_sample_id`, источник и origin. `true_labels` нужны для отображения
синтетической истины и не передаются в классификатор.

`POST /spectra/upload` — multipart с `file`, `units=nm`, `wavelength_medium=vacuum`.
CSV должен содержать только `wavelength_nm,intensity`, покрывать 350–800 nm и иметь не
более 100 000 точек/5 MB. Для upload сохраняется `snr_db: null`, `snr_kind: unknown`.
`GET /spectra/{spectrum_id}` читает сохранённую запись.

## Предсказание

`POST /predict` допускает ровно один способ ввода.

```json
{"input_type":"spectrum_id","spectrum_id":"<32 hex>","model_id":"builtin-nnls"}
```

или

```json
{"input_type":"arrays","wavelength_nm":[350,800],"intensity":[0.1,0.2],"units":"nm","wavelength_medium":"vacuum","model_id":"builtin-nnls"}
```

Ответ содержит `model_id`, версию и происхождение, `reference_source`, оценки всех
поддерживаемых элементов, `score_kind`, `calibrated`, per-class `thresholds`,
`detected_elements`, предупреждения, время и диагностические сопоставления линий.

Встроенный NNLS имеет `score_kind: nnls_coefficient_times_fit_quality`,
`calibrated: false` и фиксированный порог 0.2. Его score не является вероятностью.
Сопоставление линии означает ближайший найденный пик в пределах 1 nm; это диагностика,
не доказанное объяснение ML-решения. Модель не обучается по HTTP. Пользователь не может
загружать joblib/pickle: разрешены только локальные доверенные артефакты.

Незапущенные обучаемые модели отвечают понятной ошибкой; backend не подменяет их другой
моделью. Артефакты проверяются по identity, class order, source/checksum справочника,
preprocessing, версии scikit-learn и SHA-256 model file.

## Ограничения и хранение

Для защиты запрос ограничен 6 MB, upload — 5 MB; хранилище спектров — 500 файлов и
500 MB по умолчанию (настройки `SPECTRALAB_*`). Идентификаторы проверяются, пути и
symlink вне доверенных каталогов не используются. Разрешённый CORS по умолчанию —
`http://localhost:5173`.

API принимает только vacuum nm. Air↔vacuum не преобразуется молча. Успех API не
подтверждает наличие элемента в реальной пробе: область применимости — синтетические
нейтрально-атомные спектры и отдельная проверка реальными измерениями ещё не выполнена.
