# Источники и происхождение

Первичные источники дополнены 2026-10-13. NIST экспорт сохранён локально вместе
с URL, параметрами и контрольной суммой. Работа API и обучение используют offline cache;
загрузка исходных данных выполняется отдельными воспроизводимыми скриптами.

| Источник | Путь / ссылка | Происхождение и назначение |
| --- | --- | --- |
| NIST ASD Lines Form | https://physics.nist.gov/PhysRefData/ASD/lines_form.html | NIST, первичный справочный ресурс |
| NIST ASD Lines Help | https://physics.nist.gov/PhysRefData/ASD/Html/lineshelp.html | NIST, описание выгрузки |
| Na I saved export | `data/fixtures/nist_na_i_vacuum_580_600.csv` | две строки; metadata 2026-10-01; SHA-256 `87333ee3e64987702a618ae825156e7900c8214cc113016a9794457633195053` |
| Na I provenance | `data/fixtures/nist_na_i_vacuum_580_600.metadata.json` | NIST ASD URL, Na I, 580–600 nm, vacuum CSV |
| NIST шесть элементов | `data/reference/nist_neutral_350_800/` | 3 853 линии H/He/Na/Mg/Ca/Fe I, 350–800 nm vacuum; raw CSV, metadata, import report и cache schema 2 |
| NIST UV/видимые линии v2 | [cache](../data/reference/nist_uv_visible_v2/) | Восемь выгрузок Na/Mg/Ca/Fe I–II, 240–900 nm, явный vacuum `show_av=3`; catalog SHA-256 `504072fb7469e4bd1ae18f07ec2576b56267676dcfb58f8a3fea8ad60790ac6c` |
| NASA PDS ChemCam preflight | [CALIB](https://pds-geosciences.wustl.edu/msl/msl-m-chemcam-libs-4_5-rdr-v1/mslccm_1xxx/calib/) | us-pd; vacuum по Wiens et al. 2013; 64 стандарта / 255 спектров; [manifest](../data/reference/chemcam_external_source.json), [внешняя проверка](external_validation.md) |
| NASA PDS ChemCam LANL testbed | [CALINFO](https://pds-geosciences.wustl.edu/msl/msl-m-chemcam-libs-4_5-rdr-v1/mslccm_1xxx/calib/calinfo.txt) | `calib_2015` development / `calib_2021` held-out test; [pinned intake](../data/reference/chemcam_adaptation_intake.json), [audit aliases](../data/reference/chemcam_adaptation_alias_audit.json); происхождение и ограничения ниже |
| Реальные pLIBS Z-300 | [PDS LIBS Reference Database](https://pds-geosciences.wustl.edu/speclib/urn-nasa-pds-libs_reference_database/) | пять спектров и состав; [протокол допуска](external_validation.md), метрики заблокированы до подтверждения medium и прав |
| Основной demo | `data/fixtures/demo_lines.csv`, `.metadata.json` | `demo_fixture`, 2026-10-01, `official_nist_export: false` |
| Импорт/генератор | `ml/src/spectralab_ml/data/reference.py`, `simulation/generator.py` | реализация проекта |
| Dataset/model/experiment | `datasets.py`, `models/`, `training.py`, `experiments.py` | реализация проекта |
| ML protocol references | https://scikit-learn.org/stable/common_pitfalls.html ; https://scikit-learn.org/stable/modules/model_evaluation.html ; https://scikit-learn.org/stable/modules/calibration.html | scikit-learn, внешняя документация |

Для каждой новой сохранённой выгрузки храните URL/параметры, дату, element/ion stage,
units, wavelength medium, SHA-256 и import report. `demo_fixture` нельзя называть NIST.
Справочник линий не подтверждает качество модели, метрики или переносимость на реальные
измерения; таких результатов в этом реестре нет.

Импорт предпочитает observed, если в одной строке перехода есть и observed, и Ritz;
при отсутствии observed выбирает Ritz. Только полностью совпадающие нормализованные
записи, включая исходные поля и Ritz/уровни, удаляются с отчётом. Одинаковые
округлённые observed wavelengths у разных переходов сохраняются.
Близкие линии не объединяются. Если переходы разнесены по отдельным строкам без
transition-ID, автоматически сопоставлять разные observed/Ritz значения нельзя:
такой экспорт следует заранее привести к одной записи на переход.

Отсутствующая/нечисловая интенсивность остаётся null, пометки и исходные поля сохраняются.
В генераторе null получает явно условный prior 1. Формулы вида `="589.1583264"`
разбираются строгим шаблоном без eval. Air-вход API отклоняется. Отдельное явное
преобразование каталога с provenance реализовано в `data/wavelengths.py`;
формула и область применения описаны в [физическом протоколе](physics_assumptions.md).

## ChemCam: перенос между калибровочными сериями

Новый исследовательский профиль использует `MSL-M-CHEMCAM-LIBS-4/5-RDR-V1.0`,
[DOI 10.17189/1519485](https://doi.org/10.17189/1519485). В
[NASA Data Catalog](https://data.nasa.gov/dataset/msl-mars-chemcam-libs-spectra-4-5-rdr-v1-0-f9501)
для набора явно указана лицензия `us-pd`; при загрузке проверяется совпадение
`DATA_SET_ID` и имени CSV с отдельным PDS label. URL, SHA-256 CSV/label и снимков
доказательств сохранены в
[chemcam_adaptation_intake.json](../data/reference/chemcam_adaptation_intake.json).

Основание для **vacuum nm** — цепочка методов калибровки:
[Clegg et al. 2017](https://doi.org/10.1016/j.sab.2016.12.003) описывает
привязку Ti wavelength/channel map к известным vacuum-линиям и пересэмплирование
на эту шкалу; [Gasda et al. 2021](https://doi.org/10.1016/j.sab.2021.106223),
§2.1 и §2.3.1, описывает тот же LANL testbed и Ti-калибровку перед измерением
образцов, ссылаясь на Clegg. [Доступный текст Gasda](https://www.osti.gov/pages/servlets/purl/1785483)
и CALINFO связывают методы с сериями `calib_2015`/`calib_2021`. Давление в камере
само по себе не служит доказательством wavelength medium. Интенсивность источника:
`photons/shot/mm²/sr/nm`.

Зафиксированный intake выбирает **128 development-спектров** из `calib_2015`
(даты 2013-08-06…2013-11-07) и **71 test-стандарт** из `calib_2021`
(даты 2017-04-06…2019-06-28). Имена каталогов обозначают серии калибровки, а не
обязательно год измерения. После объединения одинакового опубликованного
`SampleName` и исключения отсутствующего первого файла JSC1413 development
содержит **127 спектров / 124 физических материала**: три пары JSC
`SYMPCA5`/`HWMK104`/`TECNY1` не считаются независимыми материалами.
Test содержит 71 физический материал и 70 семейств: `WMB24a`/`WMB24b` объединены
в `family_id=wmb24`. Семейство и дата/сессия — отдельные ключи группировки.

[Audit aliases](../data/reference/chemcam_adaptation_alias_audit.json) сохраняет
сопоставления опубликованных имён и исключения: неоднозначные имена, материалы
предыдущей preflight-проверки, пересечения с серией 2015 и связанные производные
не допускаются в новый held-out test. Development дополнительно делится на
train/tune с защитой физических материалов, семейств и сессий. Один CSV означает
одно измерение первой выбранной пространственной точки; из него вычисляется
собственное среднее shots 6…N. Provider mean/median и отдельные shots не создают
дополнительных независимых наблюдений.

Химические метки берутся из
[опубликованной таблицы составов](https://pds-geosciences.wustl.edu/msl/msl-m-chemcam-libs-4_5-rdr-v1/mslccm_1xxx/calib/ccam_calibration_compositions.csv):
для `Na2O`, `MgO`, `CaO`, `FeOT` в wt% правило `0 < x <= 0.1` задаёт operational
negative, `x >= 1` — positive; ноль, пропуски и промежуточные значения остаются
unknown. Данные составов основаны на независимых анализах/сертификатах, описанных
в Clegg и Gasda; опубликованная оценка не является сертифицированной верхней
границей каждой ячейки. Такая метка не доказывает строгое отсутствие элемента
и не задаёт измеренный LoD. Score профиля — некалиброванная оценка модели,
не вероятность и не концентрация. `H`/`He` этим профилем не поддерживаются.
Порядок фиксации, применения и ограничения: [адаптация прибора](instrument_adaptation.md).

## Дополнение v2 и поиск независимых blank

Продолжение этой работы: [поиск v3](final_validation_search_v3.md),
[сводка и 79 проверенных снимков](final_validation_search_v3_summary.json).
В нём подтверждены CC0-1.0 для PDS DOI 10.17189/b2aj-cz96,
Open Government Licence — Canada для CSA и новые low-Fe кандидаты.
Предыдущий вывод об отсутствии новых low-Fe относится к прежнему ChemCam
intake; текущие кандидаты PDS/CSA ещё не допущены к численному тесту.
Новые интенсивности не открывались, final-метрики остаются null.

После раскрытия v1 test все его материалы считаются development/диагностикой.
[Intake v2](../data/reference/chemcam_adaptation_v2_intake.json) добавляет только
четыре новых low-Mg материала Macusanite, 27-07, JCH-1, JR-3. Последний связан
с Li-серией через CALINFO и общий CCS pipeline; недоступность прямого supplement
Gasda 2022 отражена в provenance. Независимая [GSJ таблица JR-3](https://www.gsj.jp/data/openfile/no0687/gsj_openfile_report_687.pdf)
подтверждает MgO 0.050 wt% и Fe₂O₃ total 4.72 wt%.

[ChemCam audit](../data/reference/chemcam_v2_candidate_audit.json) сохраняет
полный поиск low-Fe/Mg, псевдонимы и проверку кандидатов в blank. Новых low-Fe
с известными метками не осталось. Mn blank, Ti alloy и graphite не имеют нужного
подтверждения Fe/Mg blank-границ.

В [реестре внешних источников](../data/reference/blank_candidates_v2.json)
есть подтверждённые лицензии CC BY 4.0 для
[SuperLIBS](https://doi.org/10.5281/zenodo.10287455) и
[спектров водорослей на фильтрах](https://doi.org/10.5281/zenodo.15650002).
Лицензия проверена по первичным DataCite metadata; это не подтверждает wavelength
medium или химическую чистоту blank. Давление в названии SuperLIBS относится
к среде эксперимента, а не автоматически к шкале длин волн. Внешние кандидаты
не допущены к final, их интенсивности не использовались для выбора v2.
Точный [статус допуска](../data/reference/chemcam_v2_final_admission.json) — blocked.
[Отчёт v2](instrument_v2.md) отделяет development-метрики от невыполненного final.

При исходной проверке 2026-10-01 изучались также advisory разработчиков зависимостей:
[Vitest](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9),
[Vite](https://github.com/vitejs/vite/security/advisories/GHSA-fx2h-pf6j-xcff),
[React Router](https://github.com/remix-run/react-router/security/advisories/GHSA-chx6-hx7r-mcp5).
Закреплены Vitest 4.1.11, Vite 7.3.6 и React Router 7.18.4; установка использует lockfile.
