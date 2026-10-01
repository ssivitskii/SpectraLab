# Протокол экспериментов

**Статус:** правила для synthetic `demo_fixture`; не отчёт о научной валидации.

Набор имеет multi-hot метки в фиксированном `class_order`. Сначала уникальные
`base_sample_id` делятся seed-детерминированно на train/validation/test (60/20/20),
затем создаются варианты шума/уширения. Пересечения групп запрещены. Препроцессор
валидирует train, интерполирует на 350–800 nm с шагом 0.05 и нормализует по `max_abs`;
сохранённая версия применяется при inference. Истина и параметры состава не являются
входом predictor.

Методы — NNLS, Logistic OVR и Random Forest. Все score пока не калиброваны. Порог
каждого класса выбирается только на validation по максимальному F1 на сетке из 51 значений и дополнительной верхней границе,
допускающей отсутствие обнаружений;
для single-class validation сохраняется дефолт, а причина фиксируется в manifest.
Финальные validation/test метрики включают micro/macro-F1, exact match, Hamming loss,
precision/recall/support по элементам, результаты single/mixture и warm-process timing.

YAML в `ml/configs/experiments/` задают smoke, baseline comparison, sweep SNR 5–40 dB
и clean, FWHM 0.2–2 nm, step 0.05–0.5 nm, mixtures и unseen combinations. Сейчас в
каждом конфиге один `seed: 42`; это демонстрационный запуск, не оценка разброса.

Каждый запуск создаёт новый каталог
`reports/experiments/<prefix>-<UTC timestamp>-<random8>/` с начальным `result.json`
status `running`. При успехе он получает `completed`, dataset/split hash, split IDs,
reference metadata, preprocessing, dependency versions, manifests, `metrics.csv` и
`robustness.png`; при исключении остаётся `failed` с ошибкой. Каталог не перезаписывается.

Модели обучаются один раз на запуск; пороги фиксируются до controlled test и
не подбираются заново для каждой точки деградации.

В controlled sweep один набор controlled test base IDs используется для всех точек
фактора и не пересекается с train/validation/test. В unseen-combinations удерживаются
полные сочетания, но каждый отдельный элемент должен оставаться в train. Это не тест
неизвестных элементов. Для исследовательских выводов рекомендуется повторить протокол
на нескольких заранее опубликованных seed и показать разброс, не подменяя его одним run.
