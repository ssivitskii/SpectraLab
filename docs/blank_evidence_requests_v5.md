# Черновики запросов документов для Fe/Mg-контролей · v5

Дата подготовки: **2026-10-19**. Статус обоих запросов: **НЕ ОТПРАВЛЕНЫ**.
Это локальные заготовки для последующей ручной проверки и отправки пользователем.
Никаких сообщений авторам или изменений во внешних сервисах не выполнено.

Проект повторно использует существующие измерения; собственных лабораторных
измерений для этой проверки нет. Запросы не предполагают покупки материалов,
нового сбора данных или проведения дополнительных экспериментов. Нужны уже
существующие документы, которые авторы вправе предоставить, либо указание,
что таких документов нет.

Цель — проверить возможность независимого финального теста с химически
аттестованными контролями низкого содержания Fe и Mg. Рабочий критерий для
каждого элемента — документированная верхняя граница **≤0,1 wt% FeOT или MgO**
после обоснованного пересчёта. Это операционное ограничение содержания,
а не доказательство абсолютного отсутствия элемента. Раздельные Fe- и
Mg-контроли допустимы. Даже удовлетворительный ответ не означает автоматический
допуск: остаются независимость, приборная совместимость, поддержка по группам
и фиксация протокола до первого чтения final-интенсивностей.

Основания запросов: [аудит LANL/PDS](final_search_v5_pds.md) и
[аудит вне PDS](final_search_v5_other.md). Ни один из этих кандидатов пока не
допущен; **final-метрики отсутствуют**.

## 1. Anderson / Thomas: исходные соли LANL и кандидат Mix11O

**Зачем нужен запрос.** В Supplement Table S1 Anderson et al. для Na₂SO₄,
Batch 2, связанного с Mix11O, приведены MgO `<0,01 wt%` и Fe₂O₃(T)
`0,03 wt%` по независимому анализу ActLabs. Первое значение требует уточнения
семантики цензурирования и связи с партией; второе является точечной оценкой,
а не верхней границей. Не установлены сертификат фактически измеренного лота,
связь подготовки и сессии, доступный файловый manifest и лицензия внешнего
архива. Обе статьи явно описывают vacuum wavelengths, но заявленный диапазон
не доказывает полное покрытие текущего профиля.

**Опубликованные контакты:** D. E. Anderson — `deanders@caltech.edu`,
раздел Correspondence to в [исходной статье Anderson et al. 2017](https://authors.library.caltech.edu/records/he2eq-rm869/files/Anderson_et_al-2017-Journal_of_Geophysical_Research__Planets.pdf?download=1);
N. H. Thomas — `nhthomas@caltech.edu`, тот же раздел в
[исходной статье Thomas et al. 2018](https://authors.library.caltech.edu/records/20rt7-cvf02/files/Thomas_et_al-2018-Journal_of_Geophysical_Research__Planets.pdf?download=1).
Это адреса, опубликованные в статьях; их нынешняя доставляемость не проверялась.
Перед ручной отправкой пользователь выбирает основного адресата и необходимость
копии второму автору. Запрос ниже один, с контекстом обеих работ.

**Subject:** Existing assay and provenance records for LANL salt LIBS data (Mix11O / Na2SO4 Batch 2)

```text
Dear D. E. Anderson and N. H. Thomas,

I am preparing a reproducible computational validation of LIBS element
identification using existing published measurements. I am not collecting
new laboratory data and am not asking you to purchase materials or perform
new measurements. Could you point me to any existing, shareable records for
the salt measurements in Anderson et al. (2017, DOI 10.1002/2016JE005164) and
Thomas et al. (2018, DOI 10.1029/2017JE005467)?

The most promising candidate is the Na2SO4 Batch 2 endmember associated with
Mix11O. Anderson's supplementary Table S1 reports ActLabs MgO <0.01 wt% and
total iron as Fe2O3 at 0.03 wt%. I do not interpret the iron point estimate
as an upper bound, or the reported Mg detection limit as a complete lot
certification. Separate controls for Fe and Mg would also be useful.

Specifically, do the existing records include:

1. The original independent assay report or certificate, with laboratory,
   report/sample identifiers, date, method, and the source-material lot or
   batch? For Fe and Mg, I need the units and mass basis, censoring/LOD/LOQ
   definitions, and certified upper limits or uncertainty and coverage
   information. For iron, please distinguish total Fe from FeO alone.
   Our qualification criterion is an upper bound no greater than 0.1 wt%
   as total-Fe-equivalent FeO or MgO after documented conversion; I am
   requesting the existing evidence, not a new determination of these limits.

2. A mapping from the assayed powder to the prepared pellet(s), including
   supplier/lot, preparation identifiers, any binder or other added material,
   and recorded precautions or controls for preparation contamination?
   Please clarify whether Mix11O was made solely from this endmember and
   whether later preparations used the same stock powder or a different lot.

3. Physical specimen, parent-material/family, measurement-session/date and
   filename identifiers, including aliases and repeated measurements across
   the 2013-2017 series? This is needed to exclude overlap with previously
   used ChemCam reference data, including sessions dated 2017-04-06,
   2017-04-07, 2017-04-18 and 2017-05-16. New filenames alone would not establish
   an independent material or session.

4. A current official archive location and an existing file inventory for
   the numerical spectra? The two published Caltech Box links returned 404
   through our access route. Please identify the applicable data licence
   or reuse terms, including preprocessing and publication of derived results;
   I have not assumed that the article licence applies to the external archive.

5. Wavelength-axis metadata and processing documentation for those files:
   instrument/configuration, calibration version and vacuum convention,
   actual channel ranges and gaps, intensity units/corrections, and shot
   rejection or averaging. The stated 490-850 nm VNIR range does not alone
   establish coverage of our current 474-900 nm band. I would use the actual
   coverage rather than fill unobserved regions. Existing compatible reference
   measurements with independently characterized positive Fe/Mg composition
   would help assess whether a separate instrument profile is needed.

Metadata and documents are sufficient at this stage. I will keep new held-out
spectral intensities unopened until eligibility, independence and the model
profile are fixed. A stable archive link can therefore be supplied without
sending raw spectral attachments now. If some records are unavailable, a
statement of the gap is useful; I will not substitute nominal purity or an
absence of spectral lines for chemical evidence.

Thank you for considering this request for existing records.

[Your name]
[Affiliation, if applicable]
```

Первичные указатели, о которых идёт речь:
[Supplement Table S1/S2](https://authors.library.caltech.edu/records/he2eq-rm869/files/jgre20649-sup-0001-2016JE005164-S01.pdf?download=1),
[запись Thomas 2018](https://authors.library.caltech.edu/records/20rt7-cvf02),
[Box: pzvq…](https://caltech.box.com/s/pzvq5nsm1vsktgdglaf2pc179m3silvy),
[Box: hdwg…](https://caltech.box.com/s/hdwg164njmakrujiducqzuf03x8gnb54).
Недоступность этих маршрутов не доказывает утрату архива.

## 2. PLOS 2026: фактическая партия C14500

**Зачем нужен запрос.** Table 1 указывает для C14500 предел прочих элементов
`≤0,02 wt%`, но не устанавливает независимую аттестацию измеренной партии
и включение Mg в исчерпывающую сумму примесей. Если эти условия действительно
подтверждены документом, массовый баланс мог бы дать консервативные пределы
около `0,02573 wt% FeOT` и `0,03317 wt% MgO`. Сейчас это только условный расчёт.
Нужны также среда экспортированной оси и связи между 40 образцами настройки
прибора и образцами классификации. Этот прибор отличается от ChemCam;
совпадения номинального диапазона недостаточно для применения текущего профиля.

**Опубликованный адресат:** Tiantian Pan — `ttpan@zju.edu.cn`, отмечен как
corresponding author в [первичной статье PLOS One, 2026](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0342086).
Адрес подтверждён публикацией; доставляемость не проверялась.
[Указанный авторами репозиторий](https://github.com/ptthoshi-web/LIBS-Charging-pile-plug)
содержит CC0-1.0 и список архивов, включая `Dataset_full_spectra.rar`.
Новые спектральные интенсивности не открывались.

**Subject:** Existing C14500 lot certificate and spectral metadata for independent LIBS validation

```text
Dear Tiantian Pan,

I am preparing an independent computational validation of LIBS element
identification using existing published data. Your article, "Fast
identification of the charging pile plug materials using laser-induced
breakdown spectroscopy" (DOI 10.1371/journal.pone.0342086), and its public
repository provide a promising candidate. I am not conducting my own
laboratory measurements and am not requesting new data collection,
material purchases, or new certification work.

Table 1 lists "other elements" at no more than 0.02 wt% for C14500. A certified,
exhaustive impurity bound for the actual measured lot could be useful for
Fe and Mg controls, but a nominal alloy specification is insufficient.
Could you share or point to the following existing records, where available?

1. The independent certificate of analysis or assay report for the actual
   C14500 heat/lot used, including certificate/report identifiers, issuer,
   methods, date, units and uncertainty or coverage. Does the 0.02 wt% entry
   bound the sum of all residual impurities, including Fe and Mg, or only a
   specified subset? Please provide the definition and full scope of that
   limit. Separate certified Fe/Mg upper bounds would also be suitable.
   A nominal supplier grade or selected-element list would not establish
   an exhaustive mass-balance bound. Our criterion is an upper limit of
   0.1 wt% as total-Fe-equivalent FeO or MgO after documented conversion.

2. A traceable link from that certificate and lot to the measured pellets,
   with preparation IDs and any existing information on machining, cleaning,
   coatings, added materials and possible preparation contamination? Please
   distinguish the tellurium-copper pellets from the plated plug specimens.

3. A specimen/parent-stock/family/session-to-file inventory, including the
   40 C14500 pellets used for parameter optimization and those used for
   classification. Were these the same specimens, newly prepared pieces
   from the same stock, or independent lots? Dates, acquisition-session IDs,
   aliases and optimization/classification roles are needed to separate
   development from held-out evaluation. Multiple positions or laser shots
   on one specimen would remain one physical group.

4. Metadata for the exported full spectral axis: air or vacuum wavelength
   convention, calibration-line source/version and any air/vacuum conversion,
   channel assignment, actual observed ranges and gaps, resolution, and
   intensity units. Existing documentation of background/response correction,
   normalization and shot/site averaging would also help. Please distinguish
   the ME5000 echelle files from the SR500i monochromator files; the chamber
   atmosphere or a reference to NIST lines alone would not identify the
   exported wavelength convention.

5. Confirmation of the reuse terms for the measured spectra and any supplied
   metadata/certificates, and a stable repository version or file inventory.
   The repository currently declares CC0-1.0; I would like to ensure which
   files that covers and respect any separate rights in third-party reports.

6. If already available, independent chemically characterized positive Fe
   and Mg reference measurements acquired on the same instrument/configuration,
   with their lot, preparation and session mappings. Our present profile is
   for ChemCam, so a different instrument requires compatible development
   data and a fixed profile before held-out evaluation. If no such reference
   measurements exist, please simply indicate that limitation; no new
   measurements are requested.

I have inspected publication and repository metadata, not the new spectral
intensities. I would first review the documents and inventory, then fix
eligibility, group separation and the model profile before opening any new
held-out spectra. Please do not select examples based on how their spectra
or model predictions look; an existing complete inventory is preferable.
Separate Fe and Mg controls are acceptable, and missing evidence will remain
an explicit limitation rather than being treated as zero concentration.

Thank you for any existing records or pointers you can share.

[Your name]
[Affiliation, if applicable]
```

## Проверка ответа перед допуском

Нужна проверяемая цепочка **CoA/assay → lot → preparation → physical →
family/session → licensed file → wavelength axis/instrument**. Общая статья
или общий SHA всей химической таблицы не делают разные материалы одинаковыми;
при проверке независимости сравниваются именно material/assay identifiers
и их документированные связи. Ответ сохраняется как свидетельство, а не
автоматически принимается за доказательство каждого пункта.

| Проверка | Что фиксировать | Критерий принятия |
|---|---|---|
| Независимый химический документ | Issuer, report/CoA ID, material/assay ID, дата, URL/локальная копия и SHA-256, метод, единицы, basis, uncertainty/coverage, qualifiers | Документ относится к фактическому материалу; Fe означает total Fe с явным oxide basis. Точечное значение, nominal grade, формула соли или отсутствие линий не заменяют верхнюю границу. |
| Граница для каждого элемента | Отдельные Fe/Mg bounds, смысл `<`/LOD/LOQ, основание пересчёта и учёт неопределённости | После обоснованного пересчёта верхняя граница ≤0,1 wt% FeOT/MgO. Если используется остаточная чистота, её сертифицированная граница и исчерпывающий охват примесей подтверждены. Для неподтверждённого элемента метка остаётся unknown. |
| Lot → preparation | Supplier/heat/lot IDs, aliquot/powder/pellet mapping, binder/coating/water/substrate, очистка и вклад подготовки | Анализ связан с измеренным материалом; добавки и подготовка не оставляют необоснованного переноса границы с исходного реагента на готовый контроль. |
| Physical → family/session | Все алиасы, parent stock, физический образец, семейство, сессия/дата; роли настройки/обучения/проверки | Нет пересечения с ранее использованными материалами и сессиями по правилам протокола, включая транзитивные связи. Повторные shots/sites/files не считаются независимыми контролями. |
| Измеренный химический контроль | Назначение и подготовка method/matrix control, связь с химическим документом | Реальное измерение аттестованного материала. Dark/no-laser сигнал и номинальный ноль не подменяют Fe/Mg-контроль. Раздельные контроли допускаются и учитываются отдельно. |
| Licensed file | Стабильная версия, filename/measurement ID, числовой формат, размер, официальный checksum при наличии и применимая лицензия | Права относятся именно к данным и предоставленным документам; есть связь с physical/session. Метаданные и инвентарь проверяются до чтения final-интенсивностей. Байтовая целостность окончательно проверяется контролируемым этапом фиксации/запуска. |
| Axis/instrument | Явная air/vacuum convention, источник калибровки/конверсии, прибор и версия обработки, сетка/каналы/пробелы, response/normalization | Реально наблюдаемое покрытие совместимо с замороженным профилем. Атмосфера камеры не определяет среду оси; отсутствующие диапазоны не заполняются как измеренные. |
| Development и положительная поддержка | Совместимые положительные Fe/Mg references, независимые группы, plan/profile/threshold hashes | Для другого прибора профиль обучен и выбран только на development, затем заморожен до final. Число независимых физических материалов, семейств и сессий удовлетворяет заранее заданному критерию поддержки; один пригодный blank сам по себе не завершает валидацию. |
| Решение о допуске | Сохранённые источники/хеши, manifest, per-element decision, blockers, frozen protocol | Все требуемые условия проверены до первого чтения final-интенсивностей; любое недоказанное условие остаётся blocker. До успешного допуска и запуска статус — pending, без final-score. |

Эти черновики не являются разрешением на отправку. При отсутствии ответа или
документов соответствующие ограничения остаются в реестре; химические границы,
независимость и приборная совместимость не ослабляются.
