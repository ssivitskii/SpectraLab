# Результаты исследований — 2026-10-13

Все числа ниже получены фактическими завершёнными запусками. Это небольшие
инженерные исследования синтетических спектров, не оценка качества на измерениях.
Seeds: **7, 17, 29, 43, 71**. Каждая серия имеет пять полных seed, failed/incomplete
отсутствуют. SD рассчитано с `ddof=1`; исходные JSON сохраняют отдельные прогоны,
config/source/protocol hashes и время после 2 прогревов и 7 повторов.

## Сравнение моделей

В `cnn_comparison.yaml`: 48 base samples × 2 вариации, шесть классов, fixture,
общие splits и условия; модельная сетка 0.5 nm. CNN обучалась 30 epochs на CPU.
Пороги каждой модели настроены только на development. Сравнение выполнено
в одной серии, без выбора лучшего seed или модели по внешнему test.

| Модель | Micro-F1 при 10 dB, mean ± SD | Micro-F1 при 30 dB, mean ± SD |
| --- | ---: | ---: |
| baseline-nnls | 0.9841 ± 0.0236 | 1.0000 ± 0.0000 |
| logistic-ovr | 0.9263 ± 0.0306 | 0.9216 ± 0.0311 |
| random-forest | 0.8416 ± 0.0555 | 0.9097 ± 0.0333 |
| cnn-1d | 0.9020 ± 0.0345 | 0.9003 ± 0.0302 |

На этих данных CNN не превзошла NNLS. Совпадение генератора и шаблонов даёт
NNLS естественное преимущество; это не сравнительный вывод для реального прибора.
Малый fixture и крупный шаг сетки ограничивают переносимость результатов.

## Калибровка

100 base samples × 2 вариации на seed. Validation разделён по base ID на
непересекающиеся calibration и threshold subsets. Финальный grouped test не
участвует в подгонке; reliability plots сохранены у каждой модели. В таблице
средние по пяти seed на исходном held-out test до controlled SNR sweep.
Brier и ECE: меньше — лучше. Для Logistic before — sigmoid от margin,
для RF — исходный probability-like score; это не заявление о калиброванности.

| Модель | Brier до → после | ECE до → после |
| --- | ---: | ---: |
| logistic-ovr | 0.01760 → 0.03275 | 0.08939 → 0.06561 |
| random-forest | 0.07872 → 0.20774 | 0.24976 → 0.26149 |

Калибровка ухудшила Brier обеих моделей и ECE Random Forest. Она оставлена
опциональной (`calibrate: false` по умолчанию); `calibrated: true` описывает
выполненную процедуру и синтетическую область проверки, не обещает качество.

## Контролируемая физика

Каждый механизм проверен отдельной серией из пяти seed, 36 base samples ×
1 вариация; меняется только указанный фактор. Обучение остаётся в Gaussian
условиях, поэтому измеряется устойчивость к сдвигу условий test. Фон и response
не получают единицы концентрации. Ниже micro-F1 NNLS, mean ± SD.

| Механизм | Значение фактора | Micro-F1 |
| --- | --- | ---: |
| photon_scale | 100.0 | 1.0000 ± 0.0000 |
| photon_scale | 1000.0 | 1.0000 ± 0.0000 |
| photon_scale | 10000.0 | 1.0000 ± 0.0000 |
| read_noise_std | 0.0 | 1.0000 ± 0.0000 |
| read_noise_std | 0.01 | 1.0000 ± 0.0000 |
| read_noise_std | 0.1 | 1.0000 ± 0.0000 |
| response_slope | -0.6 | 1.0000 ± 0.0000 |
| response_slope | 0.0 | 1.0000 ± 0.0000 |
| response_slope | 0.6 | 1.0000 ± 0.0000 |
| saturation_level | None | 1.0000 ± 0.0000 |
| saturation_level | 0.1 | 1.0000 ± 0.0000 |
| saturation_level | 0.5 | 1.0000 ± 0.0000 |
| lorentzian_fwhm_nm | 0.0 | 1.0000 ± 0.0000 |
| lorentzian_fwhm_nm | 0.1 | 1.0000 ± 0.0000 |
| lorentzian_fwhm_nm | 0.5 | 0.8889 ± 0.0000 |

Voigt с Lorentzian FWHM 0.5 nm заметно снизил F1 NNLS. Остальные малые
sweep не выявили снижения NNLS в этих диапазонах; это не доказательство
нечувствительности к детектору. Метрики Logistic/RF и single/mixture находятся
в исходных отчётах. Предельные режимы, конечное окно и дисперсия шума проверены
отдельными численными тестами. Единицы параметров описаны в
[physics_assumptions.md](physics_assumptions.md) и новых model manifests.

## Open-set

Синтетические спектры построены по сохранённому NIST-каталогу. Известны
H/He/Na/Mg/Ca, Fe удержан как неизвестный. Порог остатка NNLS — 95-й процентиль
known development; test не использован. В каждом seed: 10 known test и по 12
unknown-only, known+unknown, unsuitable noise.

| Условие | Метрика | Mean ± SD по 5 seed |
| --- | --- | ---: |
| known | known_false_rejection_rate | 0.1400 ± 0.1140 |
| unknown_only | unknown_false_acceptance_rate | 0.0000 ± 0.0000 |
| known_unknown_mixture | unknown_false_acceptance_rate | 0.0000 ± 0.0000 |
| unsuitable_noise | unknown_false_acceptance_rate | 0.0000 ± 0.0000 |

Пятьдесят known test дают заметные ложные отказы; нулевые false acceptance
в малой выборке с одним удержанным элементом не доказывают надёжного open-set.
Политика остаётся исследовательской и не включена в `/predict`. Обычный пустой
список обнаружений не называется неизвестным веществом.

## Источник и внешние измерения

Новый offline NIST-каталог содержит 3 853 линии шести нейтральных элементов,
350–800 nm vacuum; сохранены raw exports и checksums.
Content SHA-256: `446ccf824669087a9232092c2d225343e299c2d0117dd04c8e8e723d353c857d`.
Малый NIST smoke завершился для трёх baseline; dataset/model/run содержат
один источник. Это проверка подключения каталога, не реальная валидация.

ML08 завершён на другом наборе: NASA PDS ChemCam, 64 физических образца и
255 спектров, us-pd и vacuum подтверждены. Внешний recall трёх baseline на пяти
seed и ограничения без отрицательных меток: [внешняя валидация](external_validation.md).
Предыдущий pLIBS Z-300 intake остаётся недопущенным; его правовые и спектральные
пробелы не скрыты лицензией нового набора.

Отдельно выполнен [расширенный benchmark](expanded_benchmark.md) со слабыми
примесями, близкой парой Na/Fe, Gaussian/Voigt профилями и двумя сетками на сценарий.
Он дополняет, а не заменяет исторические результаты малой серии выше.

## Сохранённые отчёты

Большие model/dataset/report файлы остаются локально вне Git. Конфиги и этот
сводный отчёт включены в проект; повтор команды создаёт новый run-ID.

- `benchmark-series`: `benchmark-series-20261013T122844-a09b8a83` — `status=completed`, 5/5 seeds.
- `cnn-comparison-series`: `cnn-comparison-series-20261013T123909-40bbe1ac` — `status=completed`, 5/5 seeds.
- `calibration-series`: `calibration-series-20261013T123028-bc6d6644` — `status=completed`, 5/5 seeds.
- `physics-poisson-series`: `physics-poisson-series-20261013T123236-3ab17c01` — `status=completed`, 5/5 seeds.
- `physics-read_noise-series`: `physics-read_noise-series-20261013T123456-e6c83cbe` — `status=completed`, 5/5 seeds.
- `physics-response-series`: `physics-response-series-20261013T123523-9567f5ab` — `status=completed`, 5/5 seeds.
- `physics-saturation-series`: `physics-saturation-series-20261013T123533-ce50abe6` — `status=completed`, 5/5 seeds.
- `physics-voigt-series`: `physics-voigt-series-20261013T123541-d22209bd` — `status=completed`, 5/5 seeds.
- NIST: `nist-smoke-20261013T123611-f1458443`.
- Open-set: `reports/verification/open_set.json`.

Воспроизведение:

```bash
make series CONFIG=ml/configs/experiments/benchmark_series.yaml
scripts/uv.sh sync --frozen --all-packages --extra cnn
make series CONFIG=ml/configs/experiments/cnn_comparison.yaml
make series CONFIG=ml/configs/experiments/calibration.yaml
make series CONFIG=ml/configs/experiments/physics_voigt.yaml
# Аналогично physics_poisson/read_noise/response/saturation.yaml
make reference
make experiment CONFIG=ml/configs/experiments/nist_smoke.yaml
make open-set
make external-data
```
