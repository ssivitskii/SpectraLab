# Протокол экспериментов

Конфигурации YAML имеют `schema_version: 1`. Неизвестные ключи, неявные преобразования
типов, неверная сетка, несовместимый factor/generation и недоступный reference
отклоняются до создания run. Пути конфигов относительны файлу YAML.

Multi-hot метки имеют фиксированный `class_order`. Сначала уникальные
`base_sample_id` делятся seed-детерминированно на train/development/test (60/20/20),
затем создаются вариации. Группы не пересекаются. Grid/preprocessing задаёт проект
и сохраняет модель. Benchmark использует отдельный проект с сеткой 0.5 nm;
обычный demo сохраняет 0.05 nm. Эти результаты нельзя смешивать.
Истинный состав и параметры генерации не поступают в predictor.

## Модели и пороги

NNLS, Logistic OVR и Random Forest остаются основными baseline. Опциональная
`cnn-1d` обучается BCEWithLogitsLoss для нескольких одновременных меток. Extra `cnn`
содержит PyTorch; базовый runtime импортирует API без него. CNN хранит только
численные веса NPZ без pickle, версию архитектуры, seed, class order, preprocessing,
SHA-256, epochs и learning rate. Загрузка разрешена из доверенного каталога.

Порог каждого класса выбирается по development F1 на 51 точке и верхней границе,
допускающей пустое обнаружение. Для одного класса в development сохраняется
фиксированный default с объяснением. Test никогда не выбирает пороги.

При `calibrate: true` development дополнительно делится по **base IDs** на обучение
калибратора и выбор порогов. Logistic/RF получают per-class sigmoid с фиксированным
C=1; при единственном классе используется явно отмеченная сглаженная частота.
Manifest хранит обе группы, метод и область проверки. Test Brier/ECE и reliability
plots сравнивают до/после; отсутствие улучшения не скрывается. У default demo
калибровка отключена. Флаг `calibrated` описывает применение метода и не означает
проверку на реальных спектрах.

## Серии и controlled sweep

`make series CONFIG=ml/configs/experiments/benchmark_series.yaml` использует заранее
объявленные seed `[7,17,29,43,71]`. Серия содержит отдельные immutable run-ID,
config/source/protocol hashes, список ожидаемых seed и исходные результаты.
В среднее попадают только seed с полным набором моделей и условий и совпадающими
controlled base IDs. Failed/incomplete перечисляются отдельно, не заменяются нулями.
Standard deviation: `ddof=1`; при n<2 — null. Проверки воспроизводят агрегацию из raw
results, включая сбой одного seed и несовместимые протоколы.

Скорость измеряется после 2 прогревов, 7 повторов целого batch; сохраняются все
наблюдения, median/p95 и число спектров. Это локальное warm-process измерение,
зависящее от нагрузки компьютера, а не гарантия времени HTTP.

Модели обучаются один раз в каждом seed. Controlled test использует одинаковые
base IDs для всех значений фактора и всех моделей и отделён от остальных split.
Unseen combinations удерживает целые смеси при известных элементах. Отдельные
`physics_*.yaml` измеряют Voigt, Poisson, read noise, response и saturation по одному
механизму. Порог не подбирается заново в каждой точке sweep.

Каждый run сохраняет running/completed/failed, timestamps, конфигурацию, source,
dataset/split IDs/hashes, версии, model manifests, CSV и PNG. Серия сохраняет raw runs
в своём `runs/`; API может открыть их по ID. Частично записанный run не считается
успешным. Browser jobs исполняет отдельный worker с durable состоянием.

## Отказ от ответа

`make open-set` использует NIST line reference, пять известных классов и удержанный
Fe. Измеренные спектры здесь **синтетические**. Порог нормированной NNLS-ошибки
реконструкции — 95-й percentile known development, до просмотра test.
Отдельно оцениваются known false rejection, unknown-only/mixed false acceptance и
неподходящий шум на пяти seed. В отчёте остаются thresholds и novelty scores.

Это исследовательская политика; она не включена автоматически в `/predict`.
Пустой `detected_elements` означает только отсутствие превышения порогов известных
классов. Ни пустой ответ, ни большой residual не идентифицируют неизвестное вещество.
Результат не обосновывает open-set качество реального прибора.

Внешние измерения и нерешённые ограничения: [external_validation.md](external_validation.md).
Фактические результаты этой итерации: [research_results.md](research_results.md).
