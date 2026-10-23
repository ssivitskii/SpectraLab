import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  api,
  computeFileSha256,
  validateMeasuredCsv,
  type InstrumentProfileInfo,
  type MeasuredPredictionElement,
  type MeasuredPredictionResponse,
  type ParsedMeasuredCsv,
  type Spectrum,
} from "../api/client";
import { SpectrumChart } from "../components/Chart";
import { ErrorNotice, Loading } from "../components/Feedback";
import { Icon } from "../components/Icon";

const REASON_EXPLANATIONS: Record<string, string> = {
  unsupported_by_profile:
    "Элемент не входит в набор определяемых данным приборным профилем",
  low_snr:
    "Низкое отношение сигнал/шум аналитических линий (недостаточно для надёжного подтверждения)",
  coverage_gap:
    "Пропуск или неполное спектральное покрытие в области аналитических линий элемента",
  line_not_detected:
    "Характерные спектральные линии не зафиксированы выше уровня фонового шума",
  threshold_not_met:
    "Отклик модели ниже установленного порога обнаружения",
  out_of_bounds:
    "Длина волны находится вне калиброванного диапазона профиля",
};

function formatReason(reason: string | null | undefined): string {
  if (!reason) return "Причина не указана";
  return REASON_EXPLANATIONS[reason] ?? `Ограничение: ${reason}`;
}

export function Measured() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlPredictionId = searchParams.get("prediction") ?? "";

  // Profiles query
  const profilesQuery = useQuery({
    queryKey: ["instrumentProfiles"],
    queryFn: ({ signal }) => api.instrumentProfiles({ signal }),
  });

  // Prediction by ID from URL
  const predictionQuery = useQuery({
    queryKey: ["measuredPrediction", urlPredictionId],
    queryFn: ({ signal }) => api.measuredPrediction(urlPredictionId, { signal }),
    enabled: Boolean(urlPredictionId),
    retry: false,
  });

  // Form State
  const [profileId, setProfileId] = useState<string>("");
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvPreview, setCsvPreview] = useState<ParsedMeasuredCsv | null>(null);
  const [csvError, setCsvError] = useState<string | null>(null);
  const [instrumentId, setInstrumentId] = useState<string>("");
  const [unitsConfirmed, setUnitsConfirmed] = useState<boolean>(false);
  const [medium, setMedium] = useState<"air" | "vacuum" | "">("");
  const [source, setSource] = useState<string>("");
  const [sourceSha256, setSourceSha256] = useState<string>("");
  const [measurementId, setMeasurementId] = useState<string>("");
  const [physicalSampleId, setPhysicalSampleId] = useState<string>("");
  const [familyId, setFamilyId] = useState<string>("");
  const [sessionId, setSessionId] = useState<string>("");

  // Validation & UI State
  const [clientErrors, setClientErrors] = useState<Record<string, string>>({});
  const [calibFileName, setCalibFileName] = useState<string>("");
  const [isCalibHashing, setIsCalibHashing] = useState<boolean>(false);
  const [lastSubmittedFingerprint, setLastSubmittedFingerprint] = useState<string>("");

  const formRef = useRef<HTMLFormElement>(null);
  const csvSequence = useRef(0);
  const sourceSequence = useRef(0);
  const [isCsvParsing, setIsCsvParsing] = useState(false);
  const clearError = (key: string) => setClientErrors((previous) => {
    const next = { ...previous };
    delete next[key];
    return next;
  });

  // Accessibility unique IDs
  const profileSelectId = useId();
  const csvInputId = useId();
  const instrumentInputId = useId();
  const unitsCheckId = useId();
  const mediumSelectId = useId();
  const sourceInputId = useId();
  const sourceShaInputId = useId();
  const calibFileInputId = useId();
  const measIdInputId = useId();
  const sampleIdInputId = useId();
  const familyIdInputId = useId();
  const sessionIdInputId = useId();

  // Selected profile info
  const selectedProfile = useMemo<InstrumentProfileInfo | undefined>(() => {
    return profilesQuery.data?.find((p) => p.profile_id === profileId);
  }, [profilesQuery.data, profileId]);

  // Set default profile if only one is available and none selected yet
  useEffect(() => {
    if (!profileId && profilesQuery.data && profilesQuery.data.length > 0) {
      setProfileId(profilesQuery.data[0].profile_id);
    }
  }, [profilesQuery.data, profileId]);

  // Ignore stale reads when a second file is selected before parsing finishes.
  const handleCsvChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const sequence = ++csvSequence.current;
    const file = event.target.files?.[0];
    setCsvFile(file ?? null);
    setCsvPreview(null);
    setCsvError(null);
    clearError("csv");
    setIsCsvParsing(Boolean(file));
    if (!file) return;
    try {
      const parsed = await validateMeasuredCsv(file);
      if (sequence !== csvSequence.current) return;
      setCsvPreview(parsed);
    } catch (error: unknown) {
      if (sequence !== csvSequence.current) return;
      const message = error instanceof Error ? error.message : "Ошибка валидации CSV файла";
      setCsvError(message);
      setClientErrors((previous) => ({ ...previous, csv: message }));
    } finally {
      if (sequence === csvSequence.current) setIsCsvParsing(false);
    }
  };

  const handleCalibFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const sequence = ++sourceSequence.current;
    const file = event.target.files?.[0];
    setIsCalibHashing(Boolean(file));
    if (!file) return;
    setCalibFileName(file.name);
    try {
      const hash = await computeFileSha256(file);
      if (sequence !== sourceSequence.current) return;
      setSourceSha256(hash);
      clearError("sourceSha256");
    } catch {
      if (sequence !== sourceSequence.current) return;
      setClientErrors((previous) => ({ ...previous, sourceSha256: "Не удалось вычислить SHA-256 калибровочного файла." }));
    } finally {
      if (sequence === sourceSequence.current) setIsCalibHashing(false);
    }
  };

  // Submit mutation
  const analyzeMutation = useMutation({
    mutationFn: async (submittedFingerprint: string) => {
      if (!csvFile) {
        throw new Error("Необходимо выбрать CSV файл спектра.");
      }
      if (!profileId) {
        throw new Error("Необходимо выбрать приборный профиль.");
      }
      if (!instrumentId.trim()) {
        throw new Error("Необходимо указать идентификатор прибора.");
      }
      if (!unitsConfirmed) {
        throw new Error("Необходимо подтвердить единицы шкалы (нм / nm).");
      }
      if (!medium) {
        throw new Error("Необходимо выбрать среду измерения (air или vacuum).");
      }
      if (!source.trim()) {
        throw new Error("Необходимо указать источник калибровки оси.");
      }
      if (!sourceSha256.trim() || !/^[a-f0-9]{64}$/i.test(sourceSha256.trim())) {
        throw new Error("Необходимо указать корректный 64-символьный SHA-256 источника калибровки.");
      }

      const metadataPayload = {
        schema_version: 1,
        instrument_id: instrumentId.trim(),
        measurement_id: measurementId.trim() || undefined,
        physical_sample_id: physicalSampleId.trim() || undefined,
        family_id: familyId.trim() || undefined,
        session_id: sessionId.trim() || undefined,
        axis: {
          units: "nm" as const,
          medium,
          source: source.trim(),
          source_sha256: sourceSha256.trim().toLowerCase(),
        },
      };

      const formData = new FormData();
      formData.append("file", csvFile);
      formData.append("profile_id", profileId);
      formData.append("metadata", JSON.stringify(metadataPayload));

      return { prediction: await api.createMeasuredPrediction(formData), fingerprint: submittedFingerprint };
    },
    onSuccess: ({ prediction, fingerprint }) => {
      setLastSubmittedFingerprint(fingerprint);
      setSearchParams({ prediction: prediction.prediction_id });
    },
  });

  const exportMutation = useMutation({
    mutationFn: ({ id, format }: { id: string; format: "json" | "csv" | "zip" }) => api.exportMeasured(id, format),
  });

  // Client-side validation prior to submit
  const validateForm = (): boolean => {
    const errors: Record<string, string> = {};

    if (!profileId) {
      errors.profileId = "Выберите приборный профиль.";
    } else if (selectedProfile && !selectedProfile.ready) {
      errors.profileId = `Выбранный профиль недоступен: ${selectedProfile.unavailable_reason ?? "не квалифицирован"}.`;
    }

    if (!csvFile) {
      errors.csv = "Загрузите файл спектра в формате CSV.";
    } else if (csvError) {
      errors.csv = csvError;
    } else if (isCsvParsing || !csvPreview) {
      errors.csv = "Дождитесь завершения проверки CSV.";
    }

    if (!instrumentId.trim()) {
      errors.instrumentId = "Укажите идентификатор прибора измерения.";
    } else if (instrumentId.trim().length > 128) {
      errors.instrumentId = "Идентификатор прибора не может превышать 128 символов.";
    }

    if (!unitsConfirmed) {
      errors.units = "Требуется явное подтверждение единиц шкалы (нм / nm).";
    }

    if (!medium) {
      errors.medium = "Выберите среду измерения: воздух (air) или вакуум (vacuum).";
    }

    if (!source.trim()) {
      errors.source = "Укажите источник калибровки или описание шкалы длин волн.";
    } else if (source.trim().length > 200) {
      errors.source = "Описание источника калибровки не должно превышать 200 символов.";
    }

    if (!sourceSha256.trim()) {
      errors.sourceSha256 = "Укажите SHA-256 хеш источника калибровки шкалы.";
    } else if (!/^[a-f0-9]{64}$/i.test(sourceSha256.trim())) {
      errors.sourceSha256 = "SHA-256 должен содержать ровно 64 hex-символа (0-9, a-f).";
    }

    if (measurementId.trim().length > 128) {
      errors.measurementId = "Идентификатор измерения не должен превышать 128 символов.";
    }
    if (physicalSampleId.trim().length > 128) {
      errors.physicalSampleId = "Идентификатор образца не должен превышать 128 символов.";
    }
    if (familyId.trim().length > 128) {
      errors.familyId = "Идентификатор семейства не должен превышать 128 символов.";
    }
    if (sessionId.trim().length > 128) {
      errors.sessionId = "Идентификатор сессии не должен превышать 128 символов.";
    }

    setClientErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!validateForm()) return;
    if (isCalibHashing || isCsvParsing) return;
    analyzeMutation.mutate(currentFingerprint);
  };

  // Reset form and URL to start a fresh measurement analysis
  const handleStartNew = () => {
    setSearchParams({});
    analyzeMutation.reset();
    setLastSubmittedFingerprint("");
    csvSequence.current += 1;
    sourceSequence.current += 1;
    formRef.current?.reset();
    setCsvFile(null);
    setCsvPreview(null);
    setCsvError(null);
    setIsCsvParsing(false);
    setIsCalibHashing(false);
    setInstrumentId("");
    setUnitsConfirmed(false);
    setMedium("");
    setSource("");
    setSourceSha256("");
    setMeasurementId("");
    setPhysicalSampleId("");
    setFamilyId("");
    setSessionId("");
    setCalibFileName("");
    setClientErrors({});
    exportMutation.reset();
  };

  // Determine active prediction result
  const activePrediction: MeasuredPredictionResponse | undefined =
    predictionQuery.data ?? analyzeMutation.data?.prediction;

  // Stale detection: if user changed inputs after receiving a result
  const currentFingerprint = JSON.stringify([profileId, instrumentId, unitsConfirmed, medium, source, sourceSha256, csvSequence.current, measurementId, physicalSampleId, familyId, sessionId]);
  const isInputsModifiedAfterAnalysis =
    Boolean(lastSubmittedFingerprint) &&
    lastSubmittedFingerprint !== currentFingerprint &&
    Boolean(activePrediction);

  // Convert CSV preview to synthetic Spectrum object for Chart component
  const previewSpectrum = useMemo<Spectrum | null>(() => {
    if (!csvPreview || !medium) return null;
    return {
      spectrum_id: "preview_local",
      wavelength_nm: csvPreview.wavelength_nm,
      intensity: csvPreview.intensity,
      parameters: {},
      reference_source: source || "measured_preview",
      origin: "uploaded",
      schema_version: 1,
      created_at: new Date().toISOString(),
      measurement_metadata: {},
      units: "nm",
      wavelength_medium: medium || "vacuum",
    };
  }, [csvPreview, source, medium]);

  return (
    <>
      <header className="page-title">
        <div><h1>Измерения</h1><p>Загрузите приборный спектр, подтвердите шкалу и получите оценку элементов с диагностикой профиля.</p></div>
        <div className="measured-page-actions">
          {activePrediction && <button type="button" onClick={handleStartNew} id="new-analysis-button"><Icon name="plus" size={17} />Новый анализ</button>}
          <Link className="button" to="/analysis">Синтетический анализ<Icon name="arrowRight" size={17} /></Link>
        </div>
      </header>
      <div className="notice warning measured-scope" role="note"><Icon name="info" /><div><strong>Исследовательский режим</strong><p>Независимая валидация с подтверждёнными blank-контролями Fe/Mg ещё не завершена. Результат профиля не подтверждает состав образца.</p></div></div>
      <div className="workspace measured-workspace">
        <aside className="panel measured-controls" aria-label="Параметры анализа измерения">
          <div className="panel-heading"><h2>Параметры измерения</h2><Icon name="sliders" /></div>
          <form ref={formRef} onSubmit={handleSubmit} noValidate>
            <fieldset className="measured-fieldset" disabled={analyzeMutation.isPending}>
              <section className="measured-form-section">
                <h3>Спектр и прибор</h3>
                <div className="field-group"><label htmlFor={profileSelectId}>Приборный профиль <span aria-hidden="true">*</span></label>
                  {profilesQuery.isLoading && <Loading>Загрузка профилей…</Loading>}
                  {profilesQuery.error && <ErrorNotice error={profilesQuery.error} id="profiles-error" />}
                  {profilesQuery.data && <select id={profileSelectId} value={profileId} onChange={(event) => { setProfileId(event.target.value); clearError("profileId"); }} aria-invalid={Boolean(clientErrors.profileId)} aria-describedby={clientErrors.profileId ? "profile-error" : undefined}>
                    <option value="">Выберите профиль</option>{profilesQuery.data.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.profile_id} ({profile.ready ? "готов" : "недоступен"})</option>)}
                  </select>}
                  {clientErrors.profileId && <span className="field-error-text" id="profile-error" role="alert">{clientErrors.profileId}</span>}
                  {profilesQuery.data?.length === 0 && <p className="field-helper">Приборных профилей нет. Установите профиль на сервере и обновите страницу.</p>}
                </div>
                {selectedProfile && <details className="measured-profile" aria-label="Сведения о приборном профиле"><summary>Требования профиля<span className={`badge ${selectedProfile.ready ? "badge-success" : "badge-danger"}`}>{selectedProfile.ready ? "Профиль готов" : "Недоступен"}</span></summary>
                  {selectedProfile.unavailable_reason && <p className="field-error-text">Причина: {selectedProfile.unavailable_reason}</p>}
                  <dl className="status-facts"><div><dt>Ожидаемый прибор:</dt><dd><code>{selectedProfile.instrument_id}</code></dd></div><div><dt>Диапазоны</dt><dd>{selectedProfile.coverage_requirements.bands_nm.map((band) => `${band[0]}–${band[1]}`).join(", ")} нм</dd></div><div><dt>Максимальный разрыв</dt><dd>{selectedProfile.coverage_requirements.max_gap_nm} нм</dd></div><div><dt>Базовая шкала</dt><dd>{selectedProfile.scale_requirements.medium}</dd></div></dl>
                  <p className="field-helper">Допустим пересчёт из {selectedProfile.scale_requirements.conversion_supported.join(", ") || "—"}.</p>
                  <p className="field-helper">Поддерживаемые элементы:</p><div className="meta-chips">{selectedProfile.supported_elements.map((element) => <span key={element} className="badge badge-info">{element}</span>)}</div>
                  <p className="field-helper">Неподдерживаемые элементы (Unknown):</p><div className="meta-chips">{selectedProfile.unknown_elements.map((element) => <span key={element} className="badge badge-neutral">{element}</span>)}</div>
                  <details className="profile-technical"><summary>Идентификаторы профиля</summary><p className="field-helper">Назначение: {selectedProfile.scope}<br />Квалификация: {selectedProfile.qualification}<br />SHA-256: <code>{selectedProfile.sha256}</code></p></details>
                </details>}
                <div className="field-group"><label htmlFor={csvInputId}>Файл спектра (CSV) <span aria-hidden="true">*</span></label><input id={csvInputId} type="file" accept=".csv" onChange={handleCsvChange} aria-invalid={Boolean(clientErrors.csv || csvError)} aria-describedby={clientErrors.csv || csvError ? "csv-error" : "csv-help"} />
                  <span className="field-helper" id="csv-help">Заголовок <code>wavelength_nm,intensity</code>. От 2 до 100 000 точек, до 16 МБ (16 000 000 байт).</span>
                  {isCsvParsing && <small className="muted" role="status">Проверка CSV…</small>}
                  {(clientErrors.csv || csvError) && <span className="field-error-text" id="csv-error" role="alert">{clientErrors.csv || csvError}</span>}
                  {csvPreview && <small className="status-success" role="status"><Icon name="check" size={15} /> Загружено {csvPreview.pointCount.toLocaleString("ru")} точек · {csvPreview.minWavelength.toFixed(1)}–{csvPreview.maxWavelength.toFixed(1)} нм</small>}
                </div>
                <div className="field-group"><label htmlFor={instrumentInputId}>Идентификатор прибора измерения <span aria-hidden="true">*</span></label><input id={instrumentInputId} type="text" maxLength={128} value={instrumentId} placeholder="например: chemcam-lanl-testbed" onChange={(event) => { setInstrumentId(event.target.value); clearError("instrumentId"); }} aria-invalid={Boolean(clientErrors.instrumentId)} aria-describedby={clientErrors.instrumentId ? "inst-error" : "inst-help"} /><span className="field-helper" id="inst-help">Прибор должен соответствовать паспорту профиля. До 128 символов.</span>
                  {selectedProfile && <button type="button" className="measured-helper-button" onClick={() => { setInstrumentId(selectedProfile.instrument_id); clearError("instrumentId"); }}>Подставить прибор профиля: {selectedProfile.instrument_id}</button>}
                  {clientErrors.instrumentId && <span className="field-error-text" id="inst-error" role="alert">{clientErrors.instrumentId}</span>}
                </div>
              </section>
              <section className="measured-form-section">
                <h3>Шкала длин волн</h3>
                <div className="field-group"><label className="check" htmlFor={unitsCheckId}><input id={unitsCheckId} type="checkbox" checked={unitsConfirmed} onChange={(event) => { setUnitsConfirmed(event.target.checked); clearError("units"); }} aria-invalid={Boolean(clientErrors.units)} aria-describedby={clientErrors.units ? "units-error" : "units-help"} /><span>Подтверждаю, что длина волны в CSV представлена в <strong>нм (nm)</strong> <span aria-hidden="true">*</span></span></label><span className="field-helper" id="units-help">Подтверждение относится к исходному файлу.</span>{clientErrors.units && <span className="field-error-text" id="units-error" role="alert">{clientErrors.units}</span>}</div>
                <div className="field-group"><label htmlFor={mediumSelectId}>Среда измерения шкалы <span aria-hidden="true">*</span></label><select id={mediumSelectId} value={medium} onChange={(event) => { setMedium(event.target.value as "air" | "vacuum" | ""); clearError("medium"); }} aria-invalid={Boolean(clientErrors.medium)} aria-describedby={clientErrors.medium ? "medium-error" : "medium-help"}><option value="">Выберите шкалу</option><option value="air">Воздух (air)</option><option value="vacuum">Вакуум (vacuum)</option></select><span className="field-helper" id="medium-help">Для шкалы air сервер выполнит пересчёт в vacuum по Peck–Reeder (1972).</span>{clientErrors.medium && <span className="field-error-text" id="medium-error" role="alert">{clientErrors.medium}</span>}</div>
                <div className="field-group"><label htmlFor={sourceInputId}>Источник калибровки оси (source) <span aria-hidden="true">*</span></label><input id={sourceInputId} type="text" maxLength={200} value={source} placeholder="например: ChemCam Testbed Calibration Protocol v2" onChange={(event) => { setSource(event.target.value); clearError("source"); }} aria-invalid={Boolean(clientErrors.source)} aria-describedby={clientErrors.source ? "source-error" : "source-help"} /><span className="field-helper" id="source-help">Название документа или протокола калибровки шкалы. До 200 символов.</span>{clientErrors.source && <span className="field-error-text" id="source-error" role="alert">{clientErrors.source}</span>}</div>
                <div className="field-group"><label htmlFor={sourceShaInputId}>SHA-256 источника оси (source_sha256) <span aria-hidden="true">*</span></label><input id={sourceShaInputId} type="text" maxLength={64} value={sourceSha256} placeholder="64 hex-символа SHA-256 документа калибровки" onChange={(event) => { sourceSequence.current += 1; setIsCalibHashing(false); setSourceSha256(event.target.value); clearError("sourceSha256"); }} aria-invalid={Boolean(clientErrors.sourceSha256)} aria-describedby={clientErrors.sourceSha256 ? "sha-error" : "sha-help"} /><span className="field-helper" id="sha-help">Хеш документа калибровки шкалы. Хеш спектра или модели не подходит.</span>
                  <label className="calibration-file-label" htmlFor={calibFileInputId}>Рассчитать SHA-256 локального файла калибровки</label><input id={calibFileInputId} type="file" onChange={handleCalibFileChange} aria-describedby="calibration-file-help" /><small className="muted" id="calibration-file-help">{isCalibHashing ? "Вычисление SHA-256…" : calibFileName ? `Рассчитано для файла: ${calibFileName}` : "Файл остаётся на вашем компьютере."}</small>
                  {clientErrors.sourceSha256 && <span className="field-error-text" id="sha-error" role="alert">{clientErrors.sourceSha256}</span>}
                </div>
              </section>
              <details className="measured-optional"><summary>Образец и сессия</summary>
                <div className="field-group"><label htmlFor={measIdInputId}>Идентификатор измерения (measurement_id)</label><input id={measIdInputId} type="text" maxLength={128} value={measurementId} placeholder="meas-..." onChange={(event) => setMeasurementId(event.target.value)} /><span className="field-helper">До 128 символов. Если не указан, сформируется автоматически.</span></div>
                <div className="field-group"><label htmlFor={sampleIdInputId}>Идентификатор образца (physical_sample_id)</label><input id={sampleIdInputId} type="text" maxLength={128} value={physicalSampleId} placeholder="sample-..." onChange={(event) => setPhysicalSampleId(event.target.value)} /></div>
                <div className="field-group"><label htmlFor={familyIdInputId}>Семейство образцов (family_id)</label><input id={familyIdInputId} type="text" maxLength={128} value={familyId} placeholder="family-..." onChange={(event) => setFamilyId(event.target.value)} /></div>
                <div className="field-group"><label htmlFor={sessionIdInputId}>Идентификатор сессии (session_id)</label><input id={sessionIdInputId} type="text" maxLength={128} value={sessionId} placeholder="session-..." onChange={(event) => setSessionId(event.target.value)} /></div>
              </details>
              {analyzeMutation.error && <ErrorNotice error={analyzeMutation.error} id="mutation-error" />}
              <button className="primary full measured-submit" type="submit" disabled={analyzeMutation.isPending || isCsvParsing || isCalibHashing} id="submit-measured-analysis"><Icon name="play" size={17} />{analyzeMutation.isPending ? "Выполняется анализ…" : "Выполнить анализ измерения"}</button>
            </fieldset>
          </form>
        </aside>
        <section className="panel measured-results" aria-label="Результаты анализа" aria-busy={analyzeMutation.isPending}>
          {isInputsModifiedAfterAnalysis && <div className="notice warning" role="status"><Icon name="info" /><div><strong>Входные данные изменены:</strong><p>Ниже сохранён предыдущий расчёт. Запустите анализ повторно, чтобы применить параметры формы.</p></div></div>}
          {analyzeMutation.isPending && <Loading>Сервер выполняет приборный анализ спектра и проверку покрытия…</Loading>}
          {predictionQuery.error && <ErrorNotice error={predictionQuery.error} id="prediction-query-error" />}
          {predictionQuery.isLoading && <Loading>Загрузка сохранённого результата измерения…</Loading>}
          {activePrediction ? <div className="result" id="prediction-result-view">
            <div className="panel-heading measured-result-heading"><div><h2>Результат измерения</h2><small className="muted measured-record-id">ID: {activePrediction.prediction_id}</small><small className="muted">{new Date(activePrediction.created_at).toLocaleString("ru")} · {activePrediction.instrument_id}</small></div>
              <div className="export-actions">{(["json", "csv", "zip"] as const).map((format) => <button key={format} type="button" disabled={exportMutation.isPending} onClick={() => exportMutation.mutate({ id: activePrediction.prediction_id, format })}><Icon name="download" size={15} />Экспорт {format.toUpperCase()}</button>)}</div>
            </div>
            {exportMutation.error && <ErrorNotice error={exportMutation.error} />}
            <dl className="measured-summary"><div><dt>Приборный профиль</dt><dd>{activePrediction.profile_id}</dd></div><div><dt>Обнаружены</dt><dd className="status-success">{activePrediction.detected_elements.length ? activePrediction.detected_elements.join(", ") : "Не обнаружены"}</dd></div><div><dt>Преобразование оси</dt><dd>{activePrediction.wavelength_conversion ? `${activePrediction.wavelength_conversion.from} → ${activePrediction.wavelength_conversion.to}` : "Без пересчёта (вакуум)"}</dd></div><div><dt>Режим</dt><dd>{activePrediction.calibrated ? "Калиброванный отклик" : "Исследовательский"}</dd></div></dl>
            <section className="measured-element-section"><h3>Оценки присутствия элементов</h3><p className="muted">Score — отклик модели, а не вероятность. «Не определено» означает отказ от классификации, а не отсутствие элемента.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Таблица оценок элементов"><table className="table-compact measured-element-table"><thead><tr><th scope="col">Элемент</th><th scope="col">Статус профиля</th><th scope="col">Обнаружение</th><th scope="col">Score</th><th scope="col">Порог</th><th scope="col">Line SNR</th><th scope="col">Диагностика и причина</th></tr></thead><tbody>{activePrediction.predictions.map((item: MeasuredPredictionElement) => <tr key={item.element}><th scope="row">{item.element}</th><td><span className={`badge ${item.status === "evaluated" ? "badge-info" : "badge-warning"}`}>{item.status === "evaluated" ? "Оценен" : "Не поддерживается"}</span></td><td><span className={`badge ${item.status === "evaluated" && item.accepted && item.detected === true ? "badge-success" : item.status === "evaluated" && item.accepted && item.detected === false ? "badge-neutral" : "badge-warning"}`}>{item.status === "evaluated" && item.accepted && item.detected === true ? "Обнаружен" : item.status === "evaluated" && item.accepted && item.detected === false ? "Не обнаружен" : "Не определено"}</span></td><td className="mono">{item.score?.toFixed(4) ?? "—"}</td><td className="mono">{item.threshold?.toFixed(4) ?? "—"}</td><td className="mono">{item.line_snr?.toFixed(2) ?? "—"}</td><td>{formatReason(item.reason)}{item.reason && <small><code>код: {item.reason}</code></small>}</td></tr>)}</tbody></table></div>
            </section>
            {previewSpectrum && lastSubmittedFingerprint && !isInputsModifiedAfterAnalysis ? <section className="measured-chart-section"><h3>Спектральный график загруженного измерения</h3><SpectrumChart spectrum={previewSpectrum} units="nm" medium={medium || "vacuum"} /></section> : <div className="notice" role="note"><Icon name="file" /><div><strong>Исходный спектр</strong><p>Точки сохранённого расчёта доступны в <code>input.csv</code> ZIP-экспорта. График текущего черновика не подменяет сохранённый результат.</p></div></div>}
            <details className="measured-provenance"><summary>Происхождение данных и контрольные суммы</summary><dl className="status-facts"><div><dt>Хеш входного CSV (SHA-256)</dt><dd><code>{String(activePrediction.input_provenance?.input_sha256 ?? "—")}</code></dd></div><div><dt>Число точек спектра</dt><dd>{String(activePrediction.input_provenance?.input_points ?? "—")}</dd></div><div><dt>Хеш метаданных (SHA-256)</dt><dd><code>{String(activePrediction.input_provenance?.metadata_sha256 ?? "—")}</code></dd></div><div><dt>Источник калибровки шкалы</dt><dd>{String(activePrediction.input_axis?.source ?? "—")}</dd></div><div><dt>Хеш источника калибровки</dt><dd><code>{String(activePrediction.input_axis?.source_sha256 ?? "—")}</code></dd></div></dl></details>
            <details><summary>Полный технический протокол ответа (JSON)</summary><pre>{JSON.stringify(activePrediction, null, 2)}</pre></details>
          </div> : previewSpectrum ? <div><div className="panel-heading"><div><h2>Загруженный спектр</h2><small className="muted measured-record-id">{csvFile?.name}</small><small className="muted">Точек: {csvPreview?.pointCount.toLocaleString("ru")} · {csvPreview?.minWavelength.toFixed(2)}–{csvPreview?.maxWavelength.toFixed(2)} нм · {medium}</small></div><span className="badge badge-info">Предпросмотр</span></div><SpectrumChart spectrum={previewSpectrum} units="nm" medium={medium || "vacuum"} /><p className="muted">Формат проверен. Подтвердите калибровку оси и прибор, чтобы выполнить анализ. Предпросмотр показывает исходную шкалу файла.</p></div> : <div className="measured-empty"><div className="empty-icon"><Icon name="spectrum" size={32} /></div><h2>{csvPreview ? "Подтвердите шкалу спектра" : "Измерение не выбрано"}</h2><p>{csvPreview ? "Выберите air или vacuum в параметрах. После подтверждения шкалы появится график исходного файла." : "Загрузите двухколоночный CSV и укажите происхождение шкалы. Здесь появятся спектр, оценки элементов и диагностика покрытия."}</p><ol className="measured-steps"><li><Icon name="upload" /><div><strong>Загрузите спектр</strong><small>До 100 000 точек и 16 МБ</small></div></li><li><Icon name="sliders" /><div><strong>Подтвердите прибор и шкалу</strong><small>Единицы нм, air/vacuum и источник калибровки</small></div></li><li><Icon name="spectrum" /><div><strong>Изучите результат</strong><small>Отклик, пороги и причины отказа по каждому элементу</small></div></li></ol></div>}
        </section>
      </div>
    </>
  );
}
