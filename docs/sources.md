# Источники и происхождение

Первичные источники проверены 2026-10-01. NIST экспорт сохранён локально вместе
с URL, параметрами и контрольной суммой. Сетевой загрузчик не входит в приложение.

| Источник | Путь / ссылка | Происхождение и назначение |
| --- | --- | --- |
| NIST ASD Lines Form | https://physics.nist.gov/PhysRefData/ASD/lines_form.html | NIST, первичный справочный ресурс |
| NIST ASD Lines Help | https://physics.nist.gov/PhysRefData/ASD/Html/lineshelp.html | NIST, описание выгрузки |
| Na I saved export | `data/fixtures/nist_na_i_vacuum_580_600.csv` | две строки; metadata 2026-10-01; SHA-256 `87333ee3e64987702a618ae825156e7900c8214cc113016a9794457633195053` |
| Na I provenance | `data/fixtures/nist_na_i_vacuum_580_600.metadata.json` | NIST ASD URL, Na I, 580–600 nm, vacuum CSV |
| Основной demo | `data/fixtures/demo_lines.csv`, `.metadata.json` | `demo_fixture`, 2026-10-01, `official_nist_export: false` |
| Импорт/генератор | `ml/src/spectralab_ml/data/reference.py`, `simulation/generator.py` | реализация проекта |
| Dataset/model/experiment | `datasets.py`, `models/`, `training.py`, `experiments.py` | реализация проекта |
| ML protocol references | https://scikit-learn.org/stable/common_pitfalls.html ; https://scikit-learn.org/stable/modules/model_evaluation.html ; https://scikit-learn.org/stable/modules/calibration.html | scikit-learn, внешняя документация |

Для каждой новой сохранённой выгрузки храните URL/параметры, дату, element/ion stage,
units, wavelength medium, SHA-256 и import report. `demo_fixture` нельзя называть NIST.
Справочник линий не подтверждает качество модели, метрики или переносимость на реальные
измерения; таких результатов в этом реестре нет.

Импорт предпочитает observed, если в одной строке перехода есть и observed, и Ritz;
при отсутствии observed выбирает Ritz. Точные совпадения ключа
(element, ion_stage, wavelength_nm, wavelength_kind, source_id) удаляются с отчётом.
Близкие линии не объединяются. Если переходы разнесены по отдельным строкам без
transition-ID, автоматически сопоставлять разные observed/Ritz значения нельзя:
такой экспорт следует заранее привести к одной записи на переход.

Отсутствующая/нечисловая интенсивность остаётся null, пометки и исходные поля сохраняются.
В генераторе null получает явно условный prior 1. Формулы вида `="589.1583264"`
разбираются строгим шаблоном без eval. Air-вход отклоняется, преобразование не выполняется.

Перед завершением были проверены также advisory разработчиков зависимостей:
[Vitest](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9),
[Vite](https://github.com/vitejs/vite/security/advisories/GHSA-fx2h-pf6j-xcff),
[React Router](https://github.com/remix-run/react-router/security/advisories/GHSA-chx6-hx7r-mcp5).
Закреплены Vitest 4.1.11, Vite 7.3.6 и React Router 7.18.4; установка использует lockfile.
