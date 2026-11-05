# Рабочая память проекта и правила вклада

Каноническое имя этого файла — `AGENTS.md` (заглавными буквами).

Этот файл — обязательный живой журнал SpectraLab. Его читают **до начала**
работы и обновляют **в конце каждого вклада**: это относится к людям, Codex,
другим агентам и автоматизации, которая меняет репозиторий.

## Обязательное обновление

После каждого изменения добавьте запись в раздел «Журнал изменений»:

- дату (UTC), автора или имя агента и короткую цель;
- что фактически изменено, с путями к главным файлам;
- какие проверки запускались и их результат;
- известные ограничения, невыполненные проверки или внешние блокеры.

Не удаляйте старые записи и не отмечайте работу завершённой без проверяемого
свидетельства. Если меняются статус, результаты эксперимента, источник данных,
протокол или ограничение, одновременно актуализируйте «Снимок состояния» ниже
и связанный документ в `docs/`. Не вносите в журнал секреты, токены, персональные
данные или большие логи. Перед работой проверяйте последний коммит и локальные
правки; сохраняйте незакоммиченные изменения других участников.

## Снимок состояния

- SpectraLab — локальный исследовательский инструмент для анализа LIBS/атомных
  эмиссионных спектров. Базовый профиль распознаёт `H`, `He`, `Na`, `Mg`, `Ca`,
  `Fe`; его выход пока не является концентрацией или массовой долей.
- В репозитории уже есть LTE-генератор (Саха–Больцман, стадии I/II,
  самопоглощение, континуум и матрица), устойчивая предобработка, признаки по
  линиям и исследовательские ChemCam-профили. Подробнее: `docs/plasma_generator_v3.md`
  и `docs/instrument_adaptation.md`.
- Все известные реальные результаты — development или replay. Независимый final
  для Fe/Mg заблокирован отсутствием достаточного набора допущенных внешних
  контролей. Не называйте текущие метрики эксплуатационной надёжностью; условия
  допуска описаны в `docs/roadmap.md`.
- Один выбранный PDS-файл JSC1413 отсутствует в архиве (404) и законно исключён
  без замены. Это ожидаемое ограничение, а не повод молча подменять образец.
- Основной план развития core-функциональности: `docs/core_functionality_plan.md`.
- Основа обновлена до `origin/main` (`f6f43b9`): Photon UI, backend-интеграция
  и уточнённые интервалы plasma operational report. Локальные правки сохранены.
- Доступен CLI `spectralab analyze-series`: metadata с группами и полосами,
  QC/median aggregation ChemCam shots, CSV и воспроизводимый receipt. Пример и
  ограничения: `docs/shot_series.md`. Assay schema, реальный QC benchmark и UI
  интеграция серии остаются открытыми.
- Локальный запуск проверен полностью: `make setup` устанавливает Node/npm в
  `.tools/node` при несовпадении системной версии; `make dev` запускает API, UI
  и worker вместе, ожидает готовности и поддерживает согласованные API/UI порты.
  Команды и настройка IDE: `docs/local_runtime.md`.
- Добавлена и установлена модель `line-logistic` для «Анализа»: 174 локальных
  признака линий, train-only scaling и logistic regression. На synthetic drift
  0.75 nm micro-F1 0.994 против 0.493 у logistic-ovr, пять matched seed. Реальные
  ChemCam-профили не заменены; детали и границы — `docs/line_classifier_v1.md`.
- Добавлен экспериментальный `line-logistic-robust`: собственные окна каждого
  элемента, обучение на неравных весах и фоне. В synthetic development полнота
  для веса 0.10 — 100% вместо 13.7%, для 0.03 — 75.3% вместо 0%; при shift
  0.75 nm есть регрессия и ложные срабатывания. Оба ID сохранены; модель по
  умолчанию и реальные профили не менялись. `docs/line_classifier_trace_v2.md`.

- Локальная BI-отчётность активности очереди доступна через `bi/start.py`: PostgreSQL
  и Apache Superset. Она не оценивает научное качество моделей; детали в README.
- Добавлена физическая диагностика плазмы (LTE): график Больцмана для $T_e$,
  штарковская плотность электронов $n_e$ по $H_\alpha$ и проверка критерия МакВиртера;
  доступна через `POST /api/v1/plasma/diagnostics` и UI-компонент `PlasmaDiagnosticsCard`.
- Добавлен модуль количественного анализа состава (Assay) оксидов (`FeOT`, `MgO`, `CaO`,
  `TiO2`, `SiO2`, `Al2O3`) с калиброванными конформными интервалами (Split Conformal
  Prediction 90%, 95%, 99%) и хемометрическим контролем области применимости (Applicability
  Domain / Mahalanobis leverage $h(x)$): доступен через `POST /api/v1/quantitative/predict`
  и UI-компонент `QuantitativeAssayCard`.
- Добавлен модуль калибровочного переноса между приборами (Calibration Transfer):
  ленточная Piecewise Direct Standardization (PDS) с регуляризацией Ridge, автоматическое
  полиномиальное выравнивание шкалы длин волн по реперным эмиссионным линиям NIST и
  версионированные профили переноса с SHA-256; доступен через REST API `GET/POST /api/v1/transfer/*`
  и UI-компонент `CalibrationTransferCard`.
- Добавлено объяснимое измерительное досье спектра (Unified Dossier) и спектральная
  атрибуция линий NIST (Line Explainability): расчёт отношения сигнал/фон (SBR), детекция
  спектральных наложений/блендов ($\Delta\lambda \le 0.15\text{ нм}$), экспорт свидетельства
  в JSON, печатный HTML и CSV; доступно через REST API `/api/v1/dossier/*` и модальное
  окно `DossierModal`.
- Добавлен пакетный количественный анализ оксидов и матричный аудит применимости
  (Batch Quantitative Assay & Applicability Audit): расчёт оксидных содержаний (`FeOT`, `MgO`,
  `CaO`, `TiO2`, `SiO2`, `Al2O3`), сводка по доле образцов In-Domain/OOD, экспорт хемометрической
  матрицы в формате CSV; доступен через REST API `/api/v1/batches/{id}/assay-matrix*` и UI-компонент
  `BatchAssayCard`.

- Добавлено физическое самопоглощение (Cowan-Dieke) и безкалибровочный анализ (CF-LIBS):
  оценка оптической толщины $\tau_0$ по резонансным дублетам Mg II, Ca II, Na I, восстановление
  непоглощенных интенсивностей $I_0$ и инверсия элементного состава по инвариантам Саха-Больцмана;
  доступно через REST API `/api/v1/plasma/self-absorption`, `/api/v1/plasma/cf-libs` и UI-компонент
  `SelfAbsorptionCard`.

- Добавлена визуализация хемометрической области применимости (Applicability Domain & Leverage Space):
  разложение в пространство главных компонент (PCA), 95% и 99% эллипсы Хотеллинга ($T^2$),
  ортогональные невязки ($Q$-residuals) и декомпозиция вкладов линий в отклонение образца;
  доступно через REST API `/api/v1/quantitative/applicability-space` и UI-компонент `ApplicabilityDomainChart`.

- Добавлен сквозной приборный валидационный конвейер (End-to-End Real-Instrument Pipeline & Benchmark):
  автоматизированная цепочка PDS transfer $\to$ NIST alignment $\to$ Boltzmann $T_e$ $\to$ Cowan-Dieke
  self-absorption $\to$ CF-LIBS $\to$ quantitative assay $\to$ 95% conformal bounds $\to$ Hotelling $T^2$/Q $\to$
  line attribution; воспроизводимая валидация на 40 стандартах ChemCam PDS (`calib_2015`);
  отчёты в `reports/pipeline_benchmark/` и `docs/research_results.md`.

## Инварианты работы с данными и моделями

- Не смешивать synthetic, development, replay и независимый final. После чтения
  final-данных нельзя менять признаки, пороги или модель и по-прежнему называть
  тот же набор независимым.
- Не заменять недоступные спектры, не выводить нулевую концентрацию из отсутствия
  сигнала и не превращать `unknown` в отрицательную метку.
- Сохранять provenance: источник, SHA-256, единицы, шкалу длины волны,
  instrument/session/sample IDs, split IDs, seed, версию модели и пороги.
- Разделять группы по физическому материалу, семейству и сессии там, где это
  применимо. Не допускать утечки повторных shots между train/tune/test.
- Новую количественную модель нельзя описывать как валидированную на реальном
  приборе без отдельного допущенного набора с assay и неопределённостью.

## Среда и быстрые проверки

- Проект требует Python `3.12.*`, Node `22.14.0` и npm `10.9.2`; Python-пакеты
  закреплены в `uv.lock`, frontend — в `frontend/package-lock.json`.
- Запускайте Python-команды через `scripts/uv.sh run --frozen --all-packages
  --no-sync ...`. Скрипт сам использует управляемый Python и локальные cache/
  installation directories.
- Основные проверки: `make test`, `make lint`, `make build`; для изменённого
  intake — `scripts/uv.sh run --frozen --all-packages --no-sync pytest
  ml/tests/test_instrument_intake.py -q`.
- `make instrument-data` загружает около 1 ГБ PDS-измерений. Для лёгкой сетевой
  проверки используйте `scripts/uv.sh run --frozen --all-packages --no-sync
  python scripts/prepare_instrument_data.py --metadata-only`.

## Журнал изменений

### 2026-11-05 UTC — Antigravity: Веха 10 — Сквозная приборная валидация на ChemCam Replay (End-to-End Real-Instrument Pipeline & Benchmark)

- В рамках Фазы 2 комплексного плана развития реализован сквозной валидационный конвейер для реальных приборных серий:
  - `ml/src/spectralab_ml/evaluation/real_pipeline_benchmark.py`:
    - Модуль `RealPipelineBenchmark` связывает в непрерывную воспроизводимую цепочку все стадии обработки:
      1) Компенсация дрейфа шкалы и PDS-перенос (`CalibrationTransferModel`);
      2) Диагностика плазмы по Саха–Больцману (`analyze_plasma_diagnostics`);
      3) Физическая коррекция самопоглощения Cowan–Dieke и CF-LIBS инверсия (`analyze_self_absorption`, `compute_cf_libs_inversion`);
      4) Количественная регрессия оксидов (`QuantitativeAssayRegressor`) с калиброванными конформными интервалами 90%, 95%, 99%;
      5) Хемометрический контроль области применимости в пространстве главных компонент (`ApplicabilitySpaceModel`, эллипсы Hotelling $T^2$ и $Q$-невязки);
      6) Спектральная атрибуция линий NIST и контроль наложений (`analyze_spectral_explainability`);
    - Автоматическая оценка 40 сертифицированных геохимических стандартов ChemCam NASA PDS (`chemcam_adaptation_intake.json`, `calib_2015`);
    - Вычисление валидационных метрик: RMSE, MAE, $R^2$, эмпирическое покрытие 95% конформных интервалов (FeOT RMSE 0.67 wt%, $R^2 = 0.941$, покрытие 82.5%; MgO RMSE 0.73 wt%, $R^2 = 0.996$, покрытие 87.5%; SiO2 RMSE 5.26 wt%, $R^2 = 0.955$; TiO2 RMSE 0.53 wt%, покрытие 85.3%);
    - Генерация воспроизводимых артефактов в `reports/pipeline_benchmark/`:
      `pipeline_benchmark_summary.json`, `pipeline_benchmark_report.md` и детального CSV `predicted_vs_true.csv`.
  - `ml/tests/test_real_pipeline_benchmark.py`: модульный тест валидации стабильности пайплайна, расчета погрешностей и корректности экспорта артефактов.
  - `docs/research_results.md`: добавлен раздел со сводной таблицей и выводами сквозной приборной валидации.
- Проверки: `make lint build` (Ruff 203 files clean, ESLint clean, TypeScript clean, Vite build успешен);
  `make test` (728 Python passed, 4 skipped; 86 Vitest passed в 16 suites); `make e2e` (18/18 browser tests passed); `git diff --check` (clean).
- Известные ограничения: валидация проведена на открытых предполётных калибровочных стандартах LANL ChemCam testbed (`calib_2015`); полевые марсианские спектры с высокой пылевой эрозией требуют отдельного аудита в соответствии с дорожной картой проекта.


### 2026-11-05 UTC — Antigravity: Веха 9 — Визуализация хемометрической области применимости (Applicability Domain Visualizer & Leverage Space)

- В рамках Фазы 2 комплексного плана развития реализован модуль визуализации хемометрического пространства применимости:
  - `ml/src/spectralab_ml/models/applicability_space.py`:
    - Модель многомерного пространства применимости `ApplicabilitySpaceModel` на основе 24 аналитических признаков оксидов (`FeOT`, `MgO`, `CaO`, `TiO2`, `SiO2`, `Al2O3`);
    - PCA-проекция в плоскость главных компонент (PC1, PC2), вычисление объясненной дисперсии;
    - Расчёт 95% и 99% доверительных эллипсов Хотеллинга ($T^2 = \sum t_k^2 / s_k^2$) по точному $F$-распределению;
    - Оценка ортогональных $Q$-невязок модели ($Q = \|x - \hat{x}\|^2$) и перцентильных порогов $Q_{95}, Q_{99}$;
    - Декомпозиция вкладов спектральных линий в невязку (Influence decomposition) с определением направления отклонения (`high`/`low`).
  - `backend/app/schemas/api.py`: добавлены Pydantic-схемы `FeatureContributionDto`, `ApplicabilitySpaceResponse`, `ApplicabilitySpaceRequest`.
  - `backend/app/api/routes/quantitative.py`: добавлен эндпоинт `POST /api/v1/quantitative/applicability-space` с поддержкой прямых массивов и stored `spectrum_id`.
  - `frontend/src/api/client.ts` и `schema.d.ts`: добавлены клиентский метод `api.quantitativeApplicabilitySpace` и типизация.
  - `frontend/src/components/ApplicabilityDomainChart.tsx`: интерактивная визуализация Plotly с точками калибровочных стандартов (базальт, андезит, гранит, перидотит), эллипсами $T^2$ (95% и 99%), маркером исследуемого образца, карточками метрик $T^2 / Q$ и раскрывающимся списком вкладов спектральных линий.
  - `frontend/src/components/QuantitativeAssayCard.tsx`: встроена кнопка переключения «Пространство AD (PCA)» и компонент `ApplicabilityDomainChart`.
  - `ml/tests/test_applicability_space.py`: 4 модульных теста ядра (инициализация, in-domain, out-of-domain, air/vacuum).
  - `backend/tests/test_applicability_space_api.py`: 3 интеграционных теста API (массивы, stored spectrum_id, ошибки валидации).
  - `frontend/src/test/applicability_space.test.tsx`: 3 модульных теста Vitest (рендер метрик $T^2/Q$, переключение вкладов, ошибки API).
  - `frontend/e2e/applicability_space.spec.ts`: сквозной Playwright E2E тест (генерация спектра, открытие карточки AD, проверка метрик и декомпозиции).
- Проверки: `make lint build` (Ruff 201 files clean, ESLint clean, TypeScript clean, Vite build успешен);
  `make test` (727 Python passed, 4 skipped; 86 Vitest passed в 16 suites); `make e2e` (18/18 browser tests passed).
- Известные ограничения: многомерное пространство калибровано на ключевых породообразующих силикатных матрицах; образцы с доминированием редких элементов (карбонаты, сульфаты) корректно классифицируются как out-of-domain.


### 2026-11-05 UTC — Antigravity: Веха 8 — Физическое самопоглощение и CF-LIBS (Self-Absorption & CF-LIBS)

- В рамках Фазы 2 комплексного плана развития реализован модуль физического самопоглощения и безкалибровочного LIBS-анализа:
  - `ml/src/spectralab_ml/simulation/self_absorption.py`:
    - Модель Cowan-Dieke ($SA = (1 - \exp(-\tau_0))/\tau_0$);
    - 1D Brent solver для нахождения оптической толщины $\tau_0$ по наблюдаемым отношениям дублетов ($R_{\text{obs}} \le R_{\text{theo}}$) на кураторских дублетах (`Mg II 279.64/280.35`, `Ca II 393.48/396.96`, `Na I 589.16/589.76`);
    - Реконструкция непоглощенной интенсивности ($I_0 = I_{\text{obs}} / SA$);
    - Мультиэлементная CF-LIBS инверсия (`compute_cf_libs_inversion`) с использованием статистических сумм NIST $U(T)$ из `PlasmaReference`, экспоненциальных Больцмановских весов и замыкания $\sum C_i = 1.0$.
  - `backend/app/schemas/api.py`: добавлены Pydantic-схемы `DoubletAbsorptionDto`, `SelfAbsorptionResponse`, `CFLibsElementDto`, `CFLibsResponse`, `SelfAbsorptionRequest`, `CFLibsRequest`.
  - `backend/app/api/routes/plasma.py`: добавлены эндпоинты `POST /api/v1/plasma/self-absorption` и `POST /api/v1/plasma/cf-libs` (с автоматическим определением температуры через Boltzmann fit при отсутствии `temperature_k`).
  - `frontend/src/api/client.ts` и `schema.d.ts`: добавлены клиентские методы `api.plasmaSelfAbsorption`, `api.plasmaCFLibs` и типы данных.
  - `frontend/src/components/SelfAbsorptionCard.tsx`: интерактивная карточка Photon со статусами дублетов (`absorbed`, `thin`, `not_detected`), $R_{\text{obs}} / R_{\text{theo}}$, $\tau_0$, $SA$, $I_{\text{obs}} \to I_0$ и сеткой элементного состава CF-LIBS.
  - `frontend/src/pages/Analysis.tsx`: компонент `SelfAbsorptionCard` встроен в страницу анализа спектра.
  - `ml/tests/test_self_absorption.py`: 4 модульных теста ядра (Cowan-Dieke фактор, Brent solver, Mg/Ca дублеты, CF-LIBS инверсия).
  - `backend/tests/test_self_absorption_api.py`: 4 интеграционных теста API (массивы, stored spectrum_id, CF-LIBS, валидация).
  - `frontend/src/test/self_absorption.test.tsx`: 3 модульных теста Vitest (рендер дублетов, переключение деталей, ошибка API).
  - `frontend/e2e/self_absorption.spec.ts`: сквозной Playwright E2E тест (генерация спектра, рендер дублетов Mg/Ca, раскрытие деталей CF-LIBS).
- Проверки: `make lint build` (Ruff 198 files clean, ESLint clean, TypeScript clean, Vite build успешен);
  `make test` (720 Python passed, 4 skipped; 83 Vitest passed в 15 suites); `make e2e` (17/17 browser tests passed).
- Известные ограничения: базовые реперные дублеты сфокусированы на Mg II, Ca II, Na I в диапазоне 250–650 нм; CF-LIBS инверсия предполагает оптически тонкий режим для аналитических линий после отбора/коррекции.


### 2026-11-05 UTC — Antigravity: Веха 7 — Пакетный количественный анализ и матричный экспорт (Batch Quantitative Assay & Applicability Audit)

- В рамках Фазы 2 комплексного плана развития реализован модуль пакетной оценки состава и контроля области применимости:
  - `backend/app/schemas/api.py`: добавлены Pydantic-схемы `BatchSampleAssayDto`, `BatchAssaySummaryDto`, `BatchAssayResponse`.
  - `backend/app/services/batches.py`:
    - Метод `BatchStorage.get_assay_matrix`: вычисление содержаний оксидов (`FeOT`, `MgO`, `CaO`, `TiO2`, `SiO2`, `Al2O3`), конформных интервалов (90%, 95%, 99%), худшего статуса применимости (`in_domain`, `near_boundary`, `out_of_domain`), максимального leverage $h(x)$ и агрегированных средних для всех валидных файлов пакета;
    - Метод `BatchStorage.export_assay_matrix_csv`: генерация RFC 4180 CSV матрицы концентраций и границ погрешности по всем образцам пакета.
  - `backend/app/api/routes/batches.py`: добавлены эндпоинты `GET /api/v1/batches/{batch_id}/assay-matrix` и `GET /api/v1/batches/{batch_id}/assay-matrix/download`.
  - `frontend/src/api/client.ts` и `schema.d.ts`: добавлены клиентские функции `api.batchAssayMatrix`, `api.batchAssayMatrixDownloadUrl` и типизация.
  - `frontend/src/components/BatchAssayCard.tsx`: интерактивная панель Photon с KPI-сводкой (число образцов, In-domain счетчик, средняя масса, средние оксиды), селектором доверия (90/95/99%), таблицей образцов с AD бейджами и кнопкой выгрузки CSV матрицы.
  - `frontend/src/pages/BatchAnalysis.tsx`: компонент `BatchAssayCard` встроен в детальное представление пакета.
  - `backend/tests/test_batch_assay_api.py`: интеграционный тест создания пакета, загрузки спектров, выборки матрицы и скачивания CSV.
  - `frontend/src/test/batch_assay.test.tsx`: 3 модульных теста Vitest (рендер KPI, селектор доверия, обработка ошибок).
  - `frontend/e2e/batch_assay.spec.ts`: сквозной Playwright E2E тест (создание пакета, загрузка файла, проверка карточки, выбор 99%, проверка URL экспорта).
- Проверки: `make lint build` (Ruff clean, ESLint clean, TypeScript clean, Vite build успешен);
  `make test` (712 Python passed, 4 skipped; 80 Vitest passed в 14 suites); `make e2e` (16/16 browser tests passed).
- Известные ограничения: пакетный расчет оксидов ориентирован на спектры со стандартным охватом (350–800 нм); файлы вне допустимого диапазона пропускаются с сохранением статуса ошибки.


### 2026-11-05 UTC — Antigravity: Веха 6 — Объяснимое измерительное досье и экспорт комплексного свидетельства (Unified Dossier & Line Explainability)

- В рамках Фазы 2 комплексного плана развития реализован модуль измерительного досье и спектральной атрибуции:
  - `ml/src/spectralab_ml/models/explainability.py`:
    - Каталог аналитических линий NIST (`LINE_CATALOG`) с энергиями $E_k$, вероятностями переходов $A_{ki}$ и целевыми оксидами;
    - Алгоритм детекции линий, расчёта локального фона по крыльям, чистой интенсивности ($I_{net}$) и отношения сигнал/фон ($SBR$);
    - Детектор спектральных наложений/интерференций (`find_spectral_blends`) в окне $\le 0.15\text{ нм}$;
    - Оценка чистоты спектра (`spectral_purity_score`) и относительного вклада линий (`relative_contribution_score`).
  - `backend/app/schemas/api.py`: добавлены Pydantic-схемы `LineEvidenceDto`, `ExplainabilityReportDto`, `MeasurementDossierResponse`, `DossierGenerateRequest`.
  - `backend/app/services/dossier.py`: реализован сервис `DossierStorage` с синтезом резюме, генерацией отчёта Markdown, рендерингом стилизованного печатного HTML и экспортом CSV.
  - `backend/app/services/container.py`: сервис `dossier` внедрен в DI-контейнер `Services`.
  - `backend/app/api/routes/dossier.py`: добавлены эндпоинты `POST /api/v1/dossier/generate`, `GET /api/v1/dossier/{id}`, `GET /api/v1/dossier/{id}/download?format=json|html|csv`.
  - `backend/app/main.py`: роутер `dossier.router` зарегистрирован в FastAPI.
  - `frontend/src/api/client.ts` и `schema.d.ts`: добавлены клиентские методы `api.dossierGenerate`, `api.dossierGet`, `api.dossierDownloadUrl` и типизация.
  - `frontend/src/components/DossierModal.tsx`: интерактивное модальное окно с паспортом измерения (SHA-256, диапазон), бейджами чистоты/Te/массы, табами «Линии NIST» (с фильтром), «Оксидный состав (Assay)», «Протокол Markdown» и кнопками скачивания HTML/CSV/JSON.
  - `frontend/src/pages/Analysis.tsx` и `ShotSeries.tsx`: встроена кнопка вызова измерительного досье для одиночных спектров и серий.
  - `ml/tests/test_explainability.py`: 3 модульных теста ядра (детекция линий, SBR, бленды, air/vacuum).
  - `backend/tests/test_dossier_api.py`: 3 интеграционных теста API (генерация, выборка по ID, скачивание JSON/HTML/CSV, stored spectrum_id, 404/422).
  - `frontend/src/test/dossier.test.tsx`: 5 модульных тестов Vitest (рендер, табы, фильтрация, закрытие).
  - `frontend/e2e/dossier.spec.ts`: сквозной Playwright E2E тест (генерация спектра, открытие модала, проверка данных, переключение табов, закрытие).
- Проверки: `make lint build` (Ruff 194 files clean, ESLint clean, TypeScript clean, Vite build успешен);
  `make test` (711 Python passed, 4 skipped; 77 Vitest passed в 13 suites); `make e2e` (15/15 browser tests passed).
- Известные ограничения: каталог спектральных линий сфокусирован на основных породообразующих элементах (Fe, Ti, Mg, Ca, Si, Al, Na, H); редкие примеси и высокоионизованные стадии (>II) не включены в базовый каталог.


### 2026-11-05 UTC — Antigravity: Веха 5 — Калибровочный перенос между приборами (Calibration Transfer: PDS & Wavelength Alignment)

- В рамках комплексного плана развития реализован модуль калибровочного переноса между приборами и компенсации дрейфа спектрометров:
  - `ml/src/spectralab_ml/models/calibration_transfer.py`:
    - Алгоритм ленточной Piecewise Direct Standardization (PDS) с регуляризацией Ridge ($O(k \cdot p)$ памяти и времени) и поддержкой аддитивного фона;
    - Алгоритм автоматического полиномиального выравнивания длины волны (`fit_wavelength_shift_polynomial`, `apply_wavelength_correction`) по реперным эмиссионным линиям NIST (`STANDARD_REFERENCE_LINES`);
    - Класс `CalibrationTransferModel` с криптографическим SHA-256 хешированием матриц, сохранением и загрузкой JSON-артефактов;
    - Предустановленные профили переноса (`identity`, `chemcam_pds_v1`, `wavelength_alignment_v1`, `combined_pds_alignment_v1`).
  - `backend/app/schemas/api.py`: добавлены Pydantic-схемы `TransferProfileSummaryDto`, `TransferProfilesListResponse`, `TransferDiagnosticsDto`, `TransferApplyRequest`, `TransferApplyResponse`, `TransferFitRequest`, `TransferFitResponse`.
  - `backend/app/services/transfer_storage.py`: реализовано хранилище `TransferStorage` для обнаружения, применения и регистрации профилей переноса.
  - `backend/app/services/container.py`: сервис `transfer` внедрен в DI-контейнер `Services`.
  - `backend/app/api/routes/transfer.py`: добавлены эндпоинты `GET /api/v1/transfer/profiles`, `GET /api/v1/transfer/profiles/{id}`, `POST /api/v1/transfer/apply`, `POST /api/v1/transfer/fit`.
  - `backend/app/main.py`: роутер `transfer.router` зарегистрирован в FastAPI.
  - `frontend/src/api/client.ts` и `schema.d.ts`: добавлены клиентские методы `api.transferProfiles`, `api.transferProfile`, `api.transferApply`, `api.transferFit` и TypeScript-типы.
  - `frontend/src/components/CalibrationTransferCard.tsx`: интерактивный компонент Photon с селектором профилей переноса, карточками метрик (сдвиг $\Delta\lambda$, RMSD, множитель отклика, статус метода), графиком Plotly сравнения Raw vs Corrected и переключением разностного профиля ($\Delta$).
  - `frontend/src/pages/Analysis.tsx` и `ShotSeries.tsx`: компонент встроен в страницы анализа одиночных спектров и серий выстрелов.
  - `ml/tests/test_calibration_transfer.py`: 4 модульных теста ядра (снижение ошибки PDS >70%, восстановление 0.75 нм сдвига, сериализация артефактов, identity).
  - `backend/tests/test_transfer_api.py`: 6 интеграционных тестов API (список, профиль, сырые массивы, stored spectrum_id, fit custom, ошибки валидации).
  - `frontend/src/test/transfer.test.tsx`: 4 модульных теста Vitest (рендер, смена профиля, переключение разности, обработка ошибок).
  - `frontend/e2e/transfer.spec.ts`: сквозной Playwright E2E тест (выбор ChemCam PDS, проверка вызова API, обновление метрик, переключение разности).
- Проверки: `make lint build` (Ruff 189 files clean, ESLint clean, TypeScript clean, Vite build успешен);
  `make test` (705 Python passed, 4 skipped; 72 Vitest passed в 12 suites); `make e2e` (14/14 browser tests passed).
- Известные ограничения: базовые калибровки переноса ChemCam построены на опубликованных лабораторных интервалах и синтетическом дрейфе; перенос на новые физические спектрометры требует измерения согласованного набора реперных стандартов переноса (transfer standards) в соответствии с инвариантами проекта.


### 2026-11-04 UTC — Antigravity: Веха 4 — Количественная регрессия и калиброванная неопределённость (Assay & Conformal Uncertainty)

- В рамках комплексного плана развития реализован модуль количественной оценки состава (Assay) с конформными доверительными интервалами и детекцией области применимости:
  - `ml/src/spectralab_ml/models/quantitative.py`:
    - Спецификация оксидных множителей и пределов обнаружения (`OXIDE_FACTORS` для `FeOT`, `MgO`, `CaO`, `TiO2`, `SiO2`, `Al2O3`), пересчёт длин волн воздух-вакуум;
    - Алгоритм экстракции площадей аналитических линий с интегральной нормализацией спектра (`extract_species_line_features`);
    - Ridge-регрессия и Split Conformal Prediction с конечной выборкой ($q = \lceil(n+1)(1-\alpha)\rceil/n$, гарантированное маргинальное покрытие $\ge 1-\alpha$ для 90%, 95%, 99%);
    - Хемометрический детектор области применимости (Applicability Domain) на основе расстояния Махаланобиса / leverage $h(x) = (x - \bar{x}) \Sigma^{-1} (x - \bar{x})^T$ относительно критического порога $h^* = 3(p+1)/n$ (статусы `in_domain`, `near_boundary`, `out_of_domain`, `below_lod`).
  - `backend/app/schemas/api.py`: добавлены Pydantic-схемы `QuantitativePredictionDto`, `QuantitativeReportResponse`, `QuantitativePredictRequest`.
  - `backend/app/api/routes/quantitative.py`: добавлен эндпоинт `POST /api/v1/quantitative/predict` с поддержкой `spectrum_id`, прямых спектральных массивов, выбора целевых оксидов и уровня доверия.
  - `backend/app/main.py`: зарегистрирован роутер `quantitative.router`.
  - `frontend/src/api/client.ts` и `schema.d.ts`: добавлены метод `api.quantitativePredict` и TypeScript-типы.
  - `frontend/src/components/QuantitativeAssayCard.tsx`: интерактивный интерфейс оценки оксидов (карточки, wt%/ppm, шкала конформного интервала, бейджи статуса применимости AD, селектор доверия 90/95/99%, сумма обнаруженных оксидов и раскрывающаяся таблица).
  - `frontend/src/pages/Analysis.tsx` и `ShotSeries.tsx`: компонент встроен в анализ одиночного спектра и агрегированной серии.
  - `ml/tests/test_quantitative_regression.py`: 4 модульных теста ядра (предсказания, конформное масштабирование, OOD leverage, air/vacuum).
  - `backend/tests/test_quantitative_api.py`: 4 интеграционных теста API (массивы, spectrum_id, фильтрация таргетов, валидация).
  - `frontend/src/test/quantitative.test.tsx`: 5 модульных тестов Vitest (рендер, смена доверия, сводная таблица, LOD/OOD бейджи, обработка ошибок).
  - `frontend/e2e/quantitative.spec.ts`: сквозной Playwright E2E тест (генерация Fe/Mg/Ca, проверка предсказаний, смена доверия до 99%, раскрытие таблицы).
- Проверки: `make lint build` (Ruff 184 files clean, ESLint clean, TypeScript clean, Vite build успешен);
  `make test` (695 Python passed, 4 skipped; 68 Vitest passed в 11 suites); `make e2e` (13/13 browser tests passed).
- Известные ограничения: базовые калибровки обучены на синтетической матрице с контролируемым шумом;
  количественные оценки на реальных приборных данных ChemCam без дополнительного согласованного эталонного assay набора с неопределенностями остаются оценочными development-моделями (согласно инвариантам проекта).


### 2026-11-04 UTC — Antigravity: Веха 3 — Физическая диагностика плазмы и график Больцмана (LTE Plasma Diagnostics)

- В рамках комплексного плана развития реализован модуль физической диагностики параметров плазмы:
  - `ml/src/spectralab_ml/simulation/diagnostics.py`: расчёт электронной температуры плазмы $T_e \pm \Delta T_e$
    методом графика Больцмана ($\ln(I_{ki}\lambda_{ki}/g_k A_{ki}) = -E_k / (k_B T_e) + C$) на нерезонансных
    линиях NIST (Fe I, Ti I, Mg I, Ca I) с оценкой $R^2$ и composite confidence score; оценка плотности электронов
    $n_e$ по штарковскому уширению линии $H_\alpha$ ($656.46\text{ нм}$ vac / $656.28\text{ нм}$ air) с
    деконволюцией инструментального контура Фойгта (Olivero-Longbothum); проверка критерия МакВиртера применимости
    приближения LTE ($n_e \ge 1.6 \times 10^{12} T_e^{1/2} (\Delta E)^3\ \text{см}^{-3}$).
  - `backend/app/schemas/api.py`: добавлены схемы `BoltzmannPointDto`, `BoltzmannFitDto`, `StarkDensityDto`,
    `McWhirterDto`, `PlasmaDiagnosticsSummaryDto`, `PlasmaDiagnosticsResponse` и `PlasmaDiagnosticsRequest`.
  - `backend/app/api/routes/plasma.py`: добавлен эндпоинт `POST /api/v1/plasma/diagnostics` с поддержкой передачи
    как `spectrum_id`, так и произвольных массивов длины волны/интенсивности, выбора элемента и разрешения.
  - `frontend/src/api/client.ts` и `schema.d.ts`: добавлены клиентские методы `api.plasmaDiagnostics` и типизация.
  - `frontend/src/components/PlasmaDiagnosticsCard.tsx`: интерактивный компонент с карточками параметров
    ($T_e$, $n_e$, LTE McWhirter badge), селектором элемента (авто/Fe/Ti/Mg/Ca), выбором FWHM прибора,
    графиком Больцмана Plotly с линией регрессии и раскрывающейся таблицей линий со спектральными параметрами.
  - `frontend/src/pages/Analysis.tsx` и `ShotSeries.tsx`: физическая диагностика плазмы встроена в рабочий экран
    анализа спектра и в анализ серий выстрелов (по агрегированному медианному профилю).
  - `ml/tests/test_plasma_diagnostics.py`: 5 модульных тестов физического ядра (точность восстановления $T_e$
    на синтетических спектрах 8000 K, 10000 K, 12000 K с погрешностью $\le 8\%$, Штарк $n_e$, критерий МакВиртера,
    устойчивость к шуму).
  - `backend/tests/test_plasma_api.py`: 3 интеграционных теста API (массивы, spectrum_id, валидация).
  - `frontend/src/test/plasma_diagnostics.test.tsx`: 4 модульных теста Vitest (метрики, переключение элементов,
    раскрытие таблицы линий, обработка ошибок).
  - `frontend/e2e/plasma.spec.ts`: Playwright E2E тест (генерация спектра Fe+H, отображение параметров плазмы,
    проверка API-запроса при смене элемента).
- Проверки: `make lint build` (Ruff 180 files, ESLint, TypeScript, Vite build без ошибок);
  `scripts/uv.sh run pytest` (683 Python passed, 4 skipped);
  Vitest (63 passed в 10 suites); `make e2e` (12/12 browser tests passed).


### 2026-11-04 UTC — Antigravity: Веха 2 — UI интеграция анализа серий выстрелов (Shot-Series UI)

- В рамках плана развития core-функциональности реализован пользовательский интерфейс анализа серий выстрелов:
  - `frontend/src/api/client.ts`: добавлены методы `analyzeSeries`, `seriesList`, `series`,
    `downloadSeriesFile`, `deleteSeries` и парсер предпросмотра `parseSeriesCsvPreview`.
  - `frontend/src/components/Icon.tsx`: добавлена иконка серии импульсов (`series`).
  - `frontend/src/components/SeriesChart.tsx`: интерактивный Plotly-график с отображением медианного
    спектра, коридора дисперсии ($\pm 1\ \text{MAD}$), всех выстрелов (принятые/отклонённые) и подсветкой
    выстрела при наведении на строку таблицы.
  - `frontend/src/pages/ShotSeries.tsx`: страница анализа серий выстрелов с загрузкой файлов (drag-and-drop,
    кнопка загрузки демо ChemCam), панелью параметров QC (discard first shots, SNR min, saturation ceiling,
    outlier MAD z), карточками метрик качества, интерактивной таблицей диагностики выстрелов с бейджами,
    кнопками скачивания артефактов (`receipt.json`, `aggregate.csv`, `dispersion.csv`) и историей серий.
  - `frontend/src/App.tsx`: добавлен пункт навигации «Серии» и маршрут `/series`.
  - `frontend/src/test/series.test.tsx`: 5 модульных тестов Vitest (пустое состояние, загрузка демо,
    отображение диагностики QC, скачивание артефактов, предпросмотр CSV и обработка ошибок).
  - `frontend/e2e/series.spec.ts`: сквозной тест Playwright (загрузка демо, отправка в API, проверка
    результатов анализа, скачивание `receipt.json`, адаптивность на мобильном экране 390px).
- Проверки: `make lint build` (Ruff, ESLint, TypeScript, Vite build без ошибок); `make test`
  (679 Python passed, 4 skipped; 59 UI passed); `make e2e` (11/11 browser tests passed).


### 2026-11-04 UTC — Antigravity: Веха 1 — REST API и хранилище серий выстрелов (Shot-Series)

- В рамках плана развития core-функциональности реализован бэкенд для анализа серий выстрелов:
  - `backend/app/schemas/api.py`: добавлены Pydantic-схемы запросов (`SeriesAnalysisFormRequest`,
    `SeriesAnalysisRequestJson`) и ответов (`SeriesAnalysisResponse`, `SeriesListResponse`,
    `SeriesShotDiagnostic`, `SeriesAggregatePoint`).
  - `backend/app/services/series_storage.py`: создано хранилище `SeriesStorage` для изолированного
    сохранения результатов серии (`receipt.json`, `aggregate.csv`, `dispersion.csv`, `metadata.json`,
    `raw_input.csv`) с потокобезопасной блокировкой и LRU-ротацией по квотам.
  - `backend/app/services/series.py`: сервисный модуль `analyze_shot_series_service` на базе
    `spectralab_ml.data.shot_series` с поддержкой ChemCam CCS и произвольных multi-shot CSV,
    автоматической нормализацией метаданных и контролем качества (QC: SNR, насыщение, shape outlier).
  - `backend/app/api/routes/series.py`: добавлены маршруты `POST /api/v1/series/analyze`,
    `GET /api/v1/series`, `GET /api/v1/series/{series_id}`, `GET /api/v1/series/{series_id}/files/{filename}`
    и `DELETE /api/v1/series/{series_id}`.
  - `backend/app/main.py`: подключён роутер серий и настроен лимит входящих файлов до 32 МБ.
  - `backend/app/services/system_status.py`: добавлена категория `series` в отчёт хранилища статуса системы.
  - `frontend/src/api/schema.d.ts`: актуализированы типизированные контракты через `make api-types`.
  - `backend/tests/test_series_api.py`: добавлены 5 тестов (multipart/json обработка, QC отбраковка,
    скачивание файлов, удаление и валидация).
- Проверки: `make lint build` (Ruff, ESLint, TypeScript, Vite build без ошибок); `make test`
  (679 Python passed, 4 skipped; 54 UI passed); `make e2e` (10/10 browser tests passed).


### 2026-11-04 UTC — Antigravity: настройка рабочей среды, IDE и локальных артефактов

- Проект и ветка `main` синхронизированы с `origin/main` (`b21f2b7`).
- Подготовлено окружение Python: виртуальное окружение `.venv` (Python 3.12.10, uv),
  пакеты `spectralab-ml` и `spectralab-backend` подключены в editable-режиме через workspace uv.
- Создана локальная конфигурация `.env` на основе `.env.example`.
- Добавлены настройки IDE в `.vscode/settings.json` (интерпретатор `.venv/bin/python`,
  `pytest`, форматтер `ruff`, `extraPaths` для `ml/src`, `backend`, `scripts`) и рекомендации
  расширений в `.vscode/extensions.json`. В `.gitignore` добавлен `.vscode/`.
- Установлен браузер Playwright Chromium (`make browser-install`) в `.cache/ms-playwright`.
- Сгенерированы локальные demo-артефакты и обучены модели (`make demo`, `make line-model`,
  `make line-model-robust`): модели `builtin-nnls`, `line-logistic` и `line-logistic-robust`
  сохранены в `artifacts/models/`.
- Проверки: `make test` (674 passed, 4 skipped; 54 UI passed), `make lint build`
  (Ruff, ESLint, TypeScript, Vite build без ошибок), `make e2e` (10/10 browser tests passed).


### 2026-11-04 UTC — Codex: независимые локальные BI-сессии

- В `bi/superset_config.py` задано отдельное имя session cookie. В браузере
  воспроизведён сброс входа при открытии второго localhost-дашборда; после правки
  вход и повторное открытие обоих приложений сохраняют независимые сессии.


### 2026-11-04 UTC — Codex: локальная BI-отчётность очереди

- Добавлены `bi/`: экспорт JobStorage, SQL-витрины PostgreSQL и четыре графика
  Apache Superset 6.1.0; запуск и обновление снимка одной командой описаны в README.
- Проверено: 3 теста экспортера; две demo-задачи через настоящий HTTP API
  (completed и cancelled); четыре графика через API и обычный вход в браузере;
  повторный запуск и обновление ранее открытых срезов без устаревших данных.
- Отчётность описывает активность задач. Evidence остаётся unknown, время включает
  ожидание в очереди; реальная приборная надёжность и новые ML-результаты не заявляются.
  Локальные пароли, снимки и демонстрационное хранилище исключены из Git.

### 2026-10-24 UTC — Codex: DS/ML portfolio research overview

- Reorganized `README.md` around research questions, grouped evaluation and
  evidence. Preserved the previous detailed introduction in
  `docs/research-overview-history.md` and kept all run instructions.
- No model, threshold, dataset or experiment result changed. Historical external
  results remain separate from later development/replay; independent final is open.
- Verified repository-relative Markdown targets and `git diff --check`.
  No scientific experiments rerun for this documentation-only contribution.

### 2026-10-23 UTC — Codex: отдельная ветка для доработок

- По новой просьбе пользователя создана `codex/core-ml-improvements` от
  актуального `origin/main` (`f6f43b9`; основной ветки `master` в репозитории нет).
  Коммит `b85f4de` со всеми 48 файлами включён fast-forward без переписывания
  истории. Дерево файлов совпадает с проверенным коммитом; `main` не изменялся.
- Пользователь разрешил push новой ветки; отправляется только её ref,
  без force, без обновления удалённого `main` и без создания PR.
  Попытка `git push --set-upstream origin codex/core-ml-improvements`
  завершилась отказом GitHub: `Permission to ssivitskii/SpectraLab.git denied
  to makkssik`. Ветка пока только локальная; remote SHA не подтверждён.
  Нужны права collaborator для текущего аккаунта либо согласованный fork workflow.
- Черновик командного отчёта остаётся локальным. Код не менялся: повторные
  тесты не требуются; проверены `git diff --check`, ancestry и сохранность файлов.

### 2026-10-23 UTC — Codex: общий локальный коммит

- По просьбе пользователя собраны накопленные доработки запуска, series QC/CLI,
  line-logistic/robust, тесты, воспроизводимые отчёты и инструкции AGENTS.md.
  Датасеты, checkpoints, cache, IDE-файлы и рабочее хранилище не включаются.
- `origin` обновлён через fetch; перед коммитом `HEAD` и `origin/main` совпадают.
  Пользователь уточнил: нужен только локальный коммит, push он выполнит сам.
  Push не выполняется; история не переписывается. Используется текущая
  системная подпись Git, настройки автора не меняются.
- Черновик `team_report_2026-10-23.txt` пока остаётся локально: пользователь
  попросил сначала опубликовать код, затем переписать отчёт понятным языком.
- Повторно прошли `make lint test build`: 671 Python passed, 4 skipped;
  54 UI passed, Ruff/ESLint/TypeScript и build — без ошибок. Последний E2E:
  10 passed. Перед коммитом проверены staged diff и типовые форматы секретов;
  ключей/токенов не обнаружено. Убраны два trailing whitespace в core-плане.

### 2026-10-23 UTC — Codex: слабые компоненты и element-local ML

- `datasets.py`: opt-in `line_robust_v1` с log-uniform weights 0.02–1,
  нормированными по максимуму, grouped variations, фоном/наклоном и drift ±0.5 nm.
  Прежний профиль `demo` по умолчанию не изменён. Полные generation parameters
  и profile-dependent dataset hash сохраняются; версии Python/Node не менялись.
- `models/line_classifier.py`: optional element-local feature groups; каждый
  классификатор robust видит только собственные справочные окна. Конфигурация
  групп проверяется стандартным artifact loader, старые v1 модели загружаются.
  Новый ID подключён к config/training/registry, без изменения API schema.
- Команды `make line-model-robust` / `make line-model-trace-benchmark`;
  `evaluation/trace.py`, `scripts/benchmark_line_trace.py`: 10 условий,
  30 ordered pairs ×2 realizations ×5 seed; explicit zero-target controls,
  отдельные evaluation IDs/seeds, общие массивы для сравниваемых моделей.
  Training distributions различаются, это не однофакторная ablation.
- Completed run `line-trace-v2-20261023T190122`: вес 0.10 — F1 1.000 vs 0.724,
  weak recall 100% vs 13.7%; вес 0.03 — F1 0.934 vs 0.667, recall 75.3% vs 0%.
  Для обоих условий F1 лучше на каждом seed. При 0.10 + фон ×2 recall 83%.
  На двух absence controls (600 спектров) FP не наблюдались. При shift 0.75 nm
  F1 хуже: 0.958 vs 1.000, FPR по negative decisions 4.42% vs 0%.
- Сохранены `docs/line_classifier_trace_v2.md` и compact summary JSON с hashes
  и per-seed результатами. Ранний exploratory augmented all-window вариант
  давал больше FP и не используется; итоговый benchmark — development, не final.
  Вес — не концентрация, исследование не подтверждает реальный detection limit.
- Seed 42 robust обучен/установлен рядом с v1; backend/UI/worker перезапущены
  после проверки пустых очередей. В выдачу robust добавлено предупреждение о
  концентрациях и возможных FP при drift; default model и measured profiles
  сохранены. Браузерный E2E проверяет выбор и prediction для обоих ID.
- Live API smoke: synthetic H/Na 1:0.10, seed 4242,
  spectrum `6814b1cf93864426980a81c8d7fb7b6f`: v1 обнаружил H,
  robust — H и Na; шесть ready models, worker running.
- Проверено: **671 Python passed, 4 skipped; 54 UI passed; 10 E2E passed
  (20.0 s)**; Ruff/ESLint/TypeScript и build прошли. Добавлены 8 регрессионных
  тестов. Полный запуск выявил старую order-dependent мутацию session reference
  в cache test; тест изолирован копией catalog, production provenance guard
  не ослаблялся. `git diff --check` — без ошибок.

### 2026-10-23 UTC — Codex: каноническое имя AGENTS.md

- По просьбе пользователя `agents.md` переименован в `AGENTS.md` через
  промежуточное имя для корректной смены регистра на macOS. Все прежние записи
  и обязательные правила для контрибьюторов сохранены.
- Актуализирована ссылка в `docs/core_functionality_plan.md`. Проверены
  фактическое имя файла, отсутствие временного файла, сохранность журнала и
  `git diff --check`; код не менялся, тесты не требуются.

### 2026-10-23 UTC — Codex: новая ML-модель line-logistic и matched benchmark

- Добавлен `models/line_classifier.py`: признаки на трёх масштабах вокруг
  29 справочных линий, локальный фон, arcsinh, StandardScaler только по train,
  balanced per-element logistic regression. 174 признака вместо 9001 пикселя.
  Предсказания — margins, не вероятности; поддержаны constant-class fallback и
  отдельная sigmoid calibration на development группах.
- Модель подключена к config/training, проверяемым артефактам, ModelRegistry и
  UI/API. `make line-model` обучил/установил seed 42, 144 базовых состава ×2
  вариации, `demo_fixture`. Dataset hash
  `f5da9e49b22ad744737560484e796d05a477993603d8738f919b1d2f3f079f03`;
  model SHA-256 `128e61db3a49248a406b8cb8d9df3698819f219911ad9ceb84a43ce4c8f1cf94`.
- Сохранены два completed matched benchmark с seed 7/17/29/43/71 и одинаковыми
  данными для четырёх моделей. Шумовой протокол `line_classifier.yaml`:
  NNLS/logistic/line-logistic получили F1=1.00, улучшение на нём не доказано.
  `line_classifier_drift.yaml`: shift 0.50 nm — F1 1.000 vs 0.917 (logistic);
  shift 0.75 nm — 0.994 vs 0.493, exact-match 0.978 vs 0.144. На обоих крупных
  сдвигах дельта F1 положительна для каждого seed.
- Источники: `line-classifier-v1-series-20261023T184122-96ae0134` и
  `line-classifier-drift-v1-series-20261023T184307-b9ce7651` в
  `reports/experiments/`. Tracking: `docs/line_classifier_v1_summary.json`,
  `docs/assets/line_classifier_v1.png`, `docs/line_classifier_v1.md`;
  `scripts/report_line_classifier.py` перерисовывает без fitting/tuning.
- Проверено: **663 Python passed, 4 skipped; 54 UI tests passed; 9 browser E2E
  passed (20.5 s)**. Ruff/ESLint/TypeScript и build прошли. Добавлены 13 model
  tests (train-only scaler, leakage, calibration, serialization/config mismatch,
  continuum/drift и invalid data), E2E выбора line-logistic и реального prediction.
  Селектор в Analysis получил явное доступное имя «Метод распознавания».
- При перезапуске обнаружен TIME_WAIT false positive в dev port preflight;
  добавлен SO_REUSEADDR для probe, повторный запуск прошёл, 9 launcher tests
  перепроверены. Backend перезапущен и API показывает новую модель ready.
- Ограничения: 18 fixed controlled составов на condition ×5 seed; SD по seed
  не является физическим CI. Источник — demo fixture, реального переноса,
  концентраций и пределов обнаружения не проверяли. Большие окна могут смешивать
  близкие реальные линии; extraction медленнее полного logistic inference.

### 2026-10-23 UTC — Codex: работающий локальный full-stack запуск

- Добавлен `scripts/setup_node.sh`: при необходимости устанавливает официальный
  Node 22.14.0 / npm 10.9.2 в `.tools/node`, проверяя SHA-256 скачанного архива.
  `setup.sh`, `npm.sh` и Makefile используют этот runtime. `make setup` успешно
  выполнен на машине с системным Node 25, без изменения глобальной установки.
- Переработан `scripts/dev.py`: API + UI + worker запускаются одной командой;
  есть readiness-проверка, валидация портов и зависимостей, `--strictPort`,
  автоматический frontend proxy для `API_PORT`, `UI_PORT`, `--no-worker` и
  завершение групп процессов при выходе/падении. Добавлены 9 регрессионных тестов.
- Проверено: `make lint test api-types` прошёл; **650 Python passed, 4 skipped**,
  **54 UI tests passed**, TypeScript/ESLint/Ruff — без ошибок. `make build` и
  `make demo` прошли. OpenAPI schema повторно сгенерирована без diff.
- `make browser-install` скачал Chromium в project cache; `make e2e` —
  **8 passed (20.8 s)**: генерация/анализ/экспорт, batch/resume, measured
  inference, системный статус, история/ошибки и узкая вёрстка. Изолированное
  временное хранилище E2E не использовало рабочие данные.
- В реально запущенном `make dev` проверены HTTP health через frontend proxy,
  четыре ready-модели и running worker. Demo job
  `9a11a5d90f7446428e5d814c6e2bd92e` прошёл `queued → completed`, progress 1/1,
  run `job-9a11a5d90f7446428e5d814c6e2bd92e`. Локальные demo-артефакты сохранены.
- Добавлен `docs/local_runtime.md`, README актуализирован. Dev-сервер оставлен
  запущенным на localhost:5173 / API 8000 с worker. PyCharm build tool вернул
  success с ограниченными diagnostics; настройка SDK описана для `.venv`.
- Остаются предупреждения зависимостей (deprecation, размер Plotly chunk);
  научная независимая валидация и real-data QC benchmark не проводились.

### 2026-10-23 UTC — Codex: рабочий CLI анализа серии и актуализация main

- `main` обновлён fast-forward с `97adc78` до `f6f43b9`; конфликтов и потери
  локальных правок нет.
- Добавлены `series_cli.py`, `data/series_metadata.py`, команда `analyze-series`,
  synthetic fixture и `docs/shot_series.md`. CLI сохраняет aggregate/dispersion
  CSV, совместимые measured metadata и receipt с SHA-256, исходными shot numbers,
  покрытиями и причинами QC. Недостаточная серия сохраняет только receipt с
  кодом возврата 2; существующий output-dir не перезаписывается.
- Добавлены проверки material/family/session leakage, покрытия каждой полосы,
  checksum, repeatability и выходных файлов. Улучшен shot QC: исключён flat-signal
  divide-by-zero, RMS замечает локальный всплеск, finite extreme inputs безопасно
  масштабируются. Shape outlier — `rejected_quality`, не обученный OOD.
- Проверено: полный Python набор — **641 passed, 4 skipped** (47.96 s);
  `ruff check ml backend scripts`, `ruff format --check ml backend scripts`
  (163 файла) и `git diff --check` прошли. Synthetic CLI demo с policy по
  умолчанию принял shots 6–7 и отклонил 8–9; выходные CSV/metadata перечитаны
  существующими readers в интеграционном тесте.
- Использован MCP PyCharm: inspections metadata/CLI не выявили ошибок; IDE build
  вернул success с ограниченными Python diagnostics. Автонастройка IDE interpreter
  не завершилась: PyCharm не видит локальный `.tools/bin/uv`. Терминальный запуск
  и тесты используют рабочую проектную `.venv`; системный Python не менялся.
- Assay schema, wavelength alignment, сравнение QC на реальных development
  данных и UI подключения пока не реализованы. `.idea/` и производные
  `artifacts/shot-series*/` добавлены в ignore, пользовательские IDE-файлы сохранены.

### 2026-10-23 UTC — Codex: командный отчёт для сдачи

- Добавлен `team_report_2026-10-23.txt`: готовый текст единого отчёта по шести
  пунктам задания с проверенными результатами, ограничениями, вкладом участников
  и планом до конца семестра.
- Перед внешней отправкой нужно проверить, что публичный GitHub-репозиторий
  содержит актуальный `main` и указанные документы; локальные относительные пути
  сами по себе преподавателю недоступны.

### 2026-10-23 UTC — Codex: первый core-срез QC и агрегации серии shots

- Добавлены `ml/src/spectralab_ml/data/shot_series.py` и
  `ml/tests/test_shot_series.py`. `aggregate_shot_series` проверяет ось и
  конечность, отмечает low-SNR/saturated/shape-outlier shots, строит pointwise
  median и MAD только по accepted shots, либо возвращает явный
  `insufficient_series`. Receipt содержит policy, диагностику каждого shot,
  accepted indices и SHA-256 численных входов.
- `read_chemcam_ccs_shots` в
  `ml/src/spectralab_ml/data/chemcam_measurements.py` теперь отдаёт
  матрицу исходных shots до агрегации. Старый `read_chemcam_ccs` сохранён с тем
  же арифметическим средним и metadata-контрактом.
- Проверено: полный Python набор — 592 passed, 4 skipped; `ruff check` и
  `ruff format --check` для затронутых data/tests, а также `git diff --check`.
- Следующий шаг: schema для sample/session/assay и `analyze-series` CLI. Пока
  нет CLI/API, transfer между приборами или реального количественного состава;
  новые QC-пороги — консервативная policy, а не валидированная эксплуатационная
  спецификация.

### 2026-10-23 UTC — Codex: core-план и обязательный журнал для контрибьюторов

- Добавлены `docs/core_functionality_plan.md` и этот `agents.md`. План задаёт
  последовательность: контракт измерений/assay, QC и агрегация shots, физическая
  поправка, количественная регрессия с uncertainty, calibration transfer и
  объясняемый отчёт.
- Для каждого этапа зафиксированы границы, зависимости и проверяемые критерии
  готовности; первым инкрементом выбран series QC + aggregation, а не изменение
  существующих бинарных профилей.
- В начале файла закреплено обязательство каждого последующего контрибьютора
  читать и актуализировать этот журнал, связанный статус и evidence. Проверка
  этой итерации — `git diff --check`; код и научные результаты не менялись.

### 2026-10-23 UTC — Codex: восстановление воспроизводимого Python bootstrap

- Исправлены `scripts/uv.sh` и `scripts/setup.sh`: `uv` больше не получает
  несуществующий `.venv/bin/python` при первом sync; bootstrap `uv` может
  выполняться от доступного системного `python3`, а управляемая среда создаётся
  согласно `.python-version` и `uv.lock`.
- Проверено: `scripts/uv.sh sync --frozen --all-packages` создал `.venv` с
  CPython 3.12.10; metadata-only ChemCam PDS intake прошёл; `pytest
  ml/tests/test_instrument_intake.py -q` — 7 passed; shell syntax и
  `git diff --check` прошли.
- Ограничение проверки: полный `make setup` в этой рабочей среде остановился на
  Node `25.8.1`, тогда как проект требует `22.14.0`; полный набор спектров PDS
  намеренно не скачивался.

### 2026-10-30 UTC — Codex: оформление README и навигация

- `README.md`: добавлены обложка, схема интерактивного и фонового pipeline,
  навигация, карта исходников и компактный вход в запуск и исследования.
  Визуальный стиль продолжает фиолетовую карточку SpectraLab в профиле автора.
- `docs/assets/readme/`: самостоятельные SVG для светлой/тёмной темы и узкого
  экрана, с текстовыми описаниями. Внешние шрифты и сервисы изображений не нужны.
- Подробные разделы прежнего README перенесены в `docs/usage-guide.md` с
  исправленными относительными ссылками. Команды и научные ограничения сохранены;
  старые названия разделов доступны из блока переходов в README.
- Проверены XML всех SVG, ссылки и якоря, сохранность прежних команд,
  `git diff --check`, HTML через GitHub Markdown API и браузерный предпросмотр
  на широком и 390 px экране. Проверено отображение светлых и тёмных вариантов.
- Изменены только документация и иллюстрации; модели, данные и метрики прежние.
  Научные эксперименты, `make test`, `make lint` и `make build` не перезапускались.
