import { useEffect, useId, useMemo, useState } from "react";
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

  // CSV file selection and parsing
  const handleCsvChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      setCsvFile(null);
      setCsvPreview(null);
      setCsvError(null);
      return;
    }
    setCsvFile(file);
    setCsvError(null);
    try {
      const parsed = await validateMeasuredCsv(file);
      setCsvPreview(parsed);
      setClientErrors((prev) => {
        const next = { ...prev };
        delete next.csv;
        return next;
      });
    } catch (err: unknown) {
      setCsvPreview(null);
      const message = err instanceof Error ? err.message : "Ошибка валидации CSV файла";
      setCsvError(message);
      setClientErrors((prev) => ({ ...prev, csv: message }));
    }
  };

  // Explicit helper to calculate SHA-256 of user's local calibration file
  const handleCalibFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setCalibFileName(file.name);
    setIsCalibHashing(true);
    try {
      const hash = await computeFileSha256(file);
      setSourceSha256(hash);
      setClientErrors((prev) => {
        const next = { ...prev };
        delete next.sourceSha256;
        return next;
      });
    } catch {
      setClientErrors((prev) => ({
        ...prev,
        sourceSha256: "Не удалось вычислить SHA-256 калибровочного файла.",
      }));
    } finally {
      setIsCalibHashing(false);
    }
  };

  // Submit mutation
  const analyzeMutation = useMutation({
    mutationFn: async () => {
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

      return api.createMeasuredPrediction(formData);
    },
    onSuccess: (data) => {
      setLastSubmittedFingerprint(
        `${profileId}:${instrumentId}:${medium}:${source}:${sourceSha256}:${csvFile?.name}:${csvFile?.size}`
      );
      setSearchParams({ prediction: data.prediction_id });
    },
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
    analyzeMutation.mutate();
  };

  // Reset form and URL to start a fresh measurement analysis
  const handleStartNew = () => {
    setSearchParams({});
    analyzeMutation.reset();
    setLastSubmittedFingerprint("");
  };

  // Determine active prediction result
  const activePrediction: MeasuredPredictionResponse | undefined =
    predictionQuery.data ?? analyzeMutation.data;

  // Stale detection: if user changed inputs after receiving a result
  const currentFingerprint = `${profileId}:${instrumentId}:${medium}:${source}:${sourceSha256}:${csvFile?.name}:${csvFile?.size}`;
  const isInputsModifiedAfterAnalysis =
    Boolean(lastSubmittedFingerprint) &&
    lastSubmittedFingerprint !== currentFingerprint &&
    Boolean(activePrediction);

  // Convert CSV preview to synthetic Spectrum object for Chart component
  const previewSpectrum = useMemo<Spectrum | null>(() => {
    if (!csvPreview) return null;
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
        <div>
          <h1>Измерения</h1>
          <p>
            Анализ экспериментальных спектров через валидированные приборные
            профили с учётом среды и калибровки оси.
          </p>
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          {activePrediction && (
            <button
              type="button"
              className="button btn-secondary btn-sm"
              onClick={handleStartNew}
              id="new-analysis-button"
            >
              + Новый анализ
            </button>
          )}
          <Link className="button btn-sm" to="/analysis">
            Синтетический анализ →
          </Link>
        </div>
      </header>

      <div className="workspace">
        {/* LEFT COLUMN: Controls & Form */}
        <aside className="panel controls" aria-label="Параметры анализа измерения">
          <h2>Параметры измерения</h2>
          <p className="muted">
            Выберите приборный профиль, загрузите двухколоночный CSV спектр и
            явно подтвердите прибор и калибровку шкалы.
          </p>

          <form onSubmit={handleSubmit} noValidate>
            {/* 1. Instrument Profile */}
            <div className="field-group">
              <label htmlFor={profileSelectId}>
                Приборный профиль <span aria-hidden="true">*</span>
              </label>
              {profilesQuery.isLoading && <Loading children="Загрузка профилей…" />}
              {profilesQuery.error && (
                <ErrorNotice error={profilesQuery.error} id="profiles-error" />
              )}
              {profilesQuery.data && (
                <select
                  id={profileSelectId}
                  value={profileId}
                  onChange={(e) => {
                    setProfileId(e.target.value);
                    setClientErrors((prev) => {
                      const next = { ...prev };
                      delete next.profileId;
                      return next;
                    });
                  }}
                  aria-invalid={Boolean(clientErrors.profileId)}
                  aria-describedby={
                    clientErrors.profileId ? "profile-error" : undefined
                  }
                >
                  <option value="">-- Выберите профиль --</option>
                  {profilesQuery.data.map((prof) => (
                    <option key={prof.profile_id} value={prof.profile_id}>
                      {prof.identity || prof.profile_id} (
                      {prof.ready ? "готов" : "недоступен"})
                    </option>
                  ))}
                </select>
              )}
              {clientErrors.profileId && (
                <span className="field-error-text" id="profile-error" role="alert">
                  {clientErrors.profileId}
                </span>
              )}
            </div>

            {/* Profile Requirements Card */}
            {selectedProfile && (
              <div
                className="profile-meta-card"
                aria-label="Сведения о приборном профиле"
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <strong>{selectedProfile.identity || selectedProfile.profile_id}</strong>
                  <span
                    className={`badge ${selectedProfile.ready ? "badge-success" : "badge-danger"}`}
                  >
                    {selectedProfile.ready ? "Профиль готов" : "Недоступен"}
                  </span>
                </div>
                {selectedProfile.unavailable_reason && (
                  <p className="field-error-text">
                    Причина: {selectedProfile.unavailable_reason}
                  </p>
                )}
                <div className="metric-row">
                  <span className="metric-label">Назначение:</span>
                  <span className="metric-value">{selectedProfile.scope}</span>
                </div>
                <div className="metric-row">
                  <span className="metric-label">Квалификация:</span>
                  <span className="metric-value">{selectedProfile.qualification}</span>
                </div>
                <div className="metric-row">
                  <span className="metric-label">Ожидаемый прибор:</span>
                  <span className="metric-value">
                    <code>{selectedProfile.instrument_id}</code>
                  </span>
                </div>
                <div className="metric-row">
                  <span className="metric-label">Хеш профиля (SHA-256):</span>
                  <span className="metric-value">
                    <code>{selectedProfile.sha256.slice(0, 16)}…</code>
                  </span>
                </div>
                <div>
                  <small style={{ fontWeight: 600, color: "#475569" }}>
                    Поддерживаемые элементы:
                  </small>
                  <div className="meta-chips" style={{ marginTop: "4px" }}>
                    {selectedProfile.supported_elements.map((el) => (
                      <span key={el} className="badge badge-info">
                        {el}
                      </span>
                    ))}
                  </div>
                </div>
                <div>
                  <small style={{ fontWeight: 600, color: "#475569" }}>
                    Неподдерживаемые элементы (Unknown):
                  </small>
                  <div className="meta-chips" style={{ marginTop: "4px" }}>
                    {selectedProfile.unknown_elements.map((el) => (
                      <span key={el} className="badge badge-neutral">
                        {el}
                      </span>
                    ))}
                  </div>
                </div>
                <div style={{ fontSize: "11px", color: "#64748b", marginTop: "4px" }}>
                  <strong>Требования к спектру:</strong>
                  <br />
                  Диапазоны:{" "}
                  {selectedProfile.coverage_requirements.bands_nm
                    .map((b) => `${b[0]}–${b[1]} нм`)
                    .join(", ")}
                  . Макс. разрыв:{" "}
                  {selectedProfile.coverage_requirements.max_gap_nm} нм. Базовая
                  система: {selectedProfile.scale_requirements.medium} (допустим
                  пересчёт из{" "}
                  {selectedProfile.scale_requirements.conversion_supported.join(
                    ", "
                  )}
                  ).
                </div>
              </div>
            )}

            <hr />

            {/* 2. CSV Upload */}
            <div className="field-group">
              <label htmlFor={csvInputId}>
                Файл спектра (CSV) <span aria-hidden="true">*</span>
              </label>
              <input
                id={csvInputId}
                type="file"
                accept=".csv"
                onChange={handleCsvChange}
                aria-invalid={Boolean(clientErrors.csv || csvError)}
                aria-describedby={
                  clientErrors.csv || csvError ? "csv-error" : "csv-help"
                }
              />
              <span className="field-helper" id="csv-help">
                Заголовок точно <code>wavelength_nm,intensity</code>. От 2 до
                100&nbsp;000 точек, размер до 16&nbsp;МБ.
              </span>
              {(clientErrors.csv || csvError) && (
                <span className="field-error-text" id="csv-error" role="alert">
                  {clientErrors.csv || csvError}
                </span>
              )}
              {csvPreview && (
                <div className="badge badge-success" style={{ marginTop: "4px" }}>
                  ✓ Загружено {csvPreview.pointCount.toLocaleString("ru")} точек (
                  {csvPreview.minWavelength.toFixed(1)}–
                  {csvPreview.maxWavelength.toFixed(1)} нм)
                </div>
              )}
            </div>

            <hr />

            {/* 3. Instrument ID */}
            <div className="field-group">
              <label htmlFor={instrumentInputId}>
                Идентификатор прибора измерения{" "}
                <span aria-hidden="true">*</span>
              </label>
              <input
                id={instrumentInputId}
                type="text"
                maxLength={128}
                value={instrumentId}
                placeholder="например: chemcam-lanl-testbed"
                onChange={(e) => {
                  setInstrumentId(e.target.value);
                  setClientErrors((prev) => {
                    const next = { ...prev };
                    delete next.instrumentId;
                    return next;
                  });
                }}
                aria-invalid={Boolean(clientErrors.instrumentId)}
                aria-describedby={
                  clientErrors.instrumentId
                    ? "inst-error"
                    : "inst-help"
                }
              />
              <span className="field-helper" id="inst-help">
                Не подставляется автоматически. Прибор должен соответствовать
                паспорту профиля (до 128 символов).
              </span>
              {selectedProfile && (
                <div style={{ marginTop: "4px" }}>
                  <button
                    type="button"
                    className="button btn-secondary btn-sm"
                    onClick={() => {
                      setInstrumentId(selectedProfile.instrument_id);
                      setClientErrors((prev) => {
                        const next = { ...prev };
                        delete next.instrumentId;
                        return next;
                      });
                    }}
                  >
                    Подставить прибор профиля: {selectedProfile.instrument_id}
                  </button>
                </div>
              )}
              {clientErrors.instrumentId && (
                <span className="field-error-text" id="inst-error" role="alert">
                  {clientErrors.instrumentId}
                </span>
              )}
            </div>

            {/* 4. Axis Metadata */}
            <div className="field-group">
              <label className="check" htmlFor={unitsCheckId}>
                <input
                  id={unitsCheckId}
                  type="checkbox"
                  checked={unitsConfirmed}
                  onChange={(e) => {
                    setUnitsConfirmed(e.target.checked);
                    setClientErrors((prev) => {
                      const next = { ...prev };
                      delete next.units;
                      return next;
                    });
                  }}
                  aria-invalid={Boolean(clientErrors.units)}
                  aria-describedby={
                    clientErrors.units ? "units-error" : "units-help"
                  }
                />
                <span>
                  Подтверждаю, что длина волны в CSV представлена в <strong>нм (nm)</strong>{" "}
                  <span aria-hidden="true">*</span>
                </span>
              </label>
              <span className="field-helper" id="units-help">
                Единицы шкалы не предполагаются по умолчанию и требуют вашего
                прямого подтверждения.
              </span>
              {clientErrors.units && (
                <span className="field-error-text" id="units-error" role="alert">
                  {clientErrors.units}
                </span>
              )}
            </div>

            <div className="field-group">
              <label htmlFor={mediumSelectId}>
                Среда измерения шкалы <span aria-hidden="true">*</span>
              </label>
              <select
                id={mediumSelectId}
                value={medium}
                onChange={(e) => {
                  setMedium(e.target.value as "air" | "vacuum" | "");
                  setClientErrors((prev) => {
                    const next = { ...prev };
                    delete next.medium;
                    return next;
                  });
                }}
                aria-invalid={Boolean(clientErrors.medium)}
                aria-describedby={
                  clientErrors.medium ? "medium-error" : "medium-help"
                }
              >
                <option value="">-- Выберите среду измерения --</option>
                <option value="air">
                  Воздух (air) — спектр измерен в воздухе, требуется пересчёт в вакуум
                </option>
                <option value="vacuum">
                  Вакуум (vacuum) — спектр снят в вакуумной системе
                </option>
              </select>
              <span className="field-helper" id="medium-help">
                Среда не выбирается молча. Для воздуха сервер применит
                стандартное дисперсионное преобразование Peck-Reeder 1972.
              </span>
              {clientErrors.medium && (
                <span className="field-error-text" id="medium-error" role="alert">
                  {clientErrors.medium}
                </span>
              )}
            </div>

            <div className="field-group">
              <label htmlFor={sourceInputId}>
                Источник калибровки оси (source) <span aria-hidden="true">*</span>
              </label>
              <input
                id={sourceInputId}
                type="text"
                maxLength={200}
                value={source}
                placeholder="например: ChemCam Testbed Calibration Protocol v2"
                onChange={(e) => {
                  setSource(e.target.value);
                  setClientErrors((prev) => {
                    const next = { ...prev };
                    delete next.source;
                    return next;
                  });
                }}
                aria-invalid={Boolean(clientErrors.source)}
                aria-describedby={
                  clientErrors.source ? "source-error" : "source-help"
                }
              />
              <span className="field-helper" id="source-help">
                Идентификатор или описание документа/протокола калибровки сетки
                длин волн (до 200 символов). Не оставляйте произвольные фиктивные
                строки.
              </span>
              {clientErrors.source && (
                <span className="field-error-text" id="source-error" role="alert">
                  {clientErrors.source}
                </span>
              )}
            </div>

            <div className="field-group">
              <label htmlFor={sourceShaInputId}>
                SHA-256 источника оси (source_sha256){" "}
                <span aria-hidden="true">*</span>
              </label>
              <input
                id={sourceShaInputId}
                type="text"
                maxLength={64}
                value={sourceSha256}
                placeholder="64 hex-символа SHA-256 документа калибровки"
                onChange={(e) => {
                  setSourceSha256(e.target.value);
                  setClientErrors((prev) => {
                    const next = { ...prev };
                    delete next.sourceSha256;
                    return next;
                  });
                }}
                aria-invalid={Boolean(clientErrors.sourceSha256)}
                aria-describedby={
                  clientErrors.sourceSha256 ? "sha-error" : "sha-help"
                }
              />
              <span className="field-helper" id="sha-help">
                Криптографический хеш файла или спецификации калибровки шкалы
                (64 hex-символа). Запрещено использовать хеш модели или спектра
                вместо источника калибровки.
              </span>
              {/* Optional Helper to Hash Local Calibration File */}
              <div style={{ marginTop: "6px" }}>
                <label
                  htmlFor={calibFileInputId}
                  style={{
                    fontSize: "11px",
                    fontWeight: "normal",
                    color: "#1e40af",
                    cursor: "pointer",
                    textDecoration: "underline",
                  }}
                >
                  {isCalibHashing
                    ? "Вычисление SHA-256…"
                    : "Рассчитать SHA-256 локального файла калибровки ↗"}
                </label>
                <input
                  id={calibFileInputId}
                  type="file"
                  style={{ display: "none" }}
                  onChange={handleCalibFileChange}
                />
                {calibFileName && (
                  <small style={{ color: "#475569", marginTop: "2px" }}>
                    Рассчитано для файла: {calibFileName}
                  </small>
                )}
              </div>
              {clientErrors.sourceSha256 && (
                <span className="field-error-text" id="sha-error" role="alert">
                  {clientErrors.sourceSha256}
                </span>
              )}
            </div>

            {/* 5. Optional Identifiers */}
            <details style={{ marginTop: "16px" }}>
              <summary>
                Необязательные метаданные (образцы, семейства, сессии)
              </summary>
              <div className="field-group">
                <label htmlFor={measIdInputId}>Идентификатор измерения (measurement_id)</label>
                <input
                  id={measIdInputId}
                  type="text"
                  maxLength={128}
                  value={measurementId}
                  placeholder="meas-..."
                  onChange={(e) => setMeasurementId(e.target.value)}
                />
                <span className="field-helper">До 128 символов. Если не указан, сформируется автоматически.</span>
              </div>
              <div className="field-group">
                <label htmlFor={sampleIdInputId}>Идентификатор образца (physical_sample_id)</label>
                <input
                  id={sampleIdInputId}
                  type="text"
                  maxLength={128}
                  value={physicalSampleId}
                  placeholder="sample-..."
                  onChange={(e) => setPhysicalSampleId(e.target.value)}
                />
              </div>
              <div className="field-group">
                <label htmlFor={familyIdInputId}>Семейство образцов (family_id)</label>
                <input
                  id={familyIdInputId}
                  type="text"
                  maxLength={128}
                  value={familyId}
                  placeholder="family-..."
                  onChange={(e) => setFamilyId(e.target.value)}
                />
              </div>
              <div className="field-group">
                <label htmlFor={sessionIdInputId}>Идентификатор сессии (session_id)</label>
                <input
                  id={sessionIdInputId}
                  type="text"
                  maxLength={128}
                  value={sessionId}
                  placeholder="session-..."
                  onChange={(e) => setSessionId(e.target.value)}
                />
              </div>
            </details>

            {/* Error banner from mutation */}
            {analyzeMutation.error && (
              <ErrorNotice
                error={analyzeMutation.error}
                id="mutation-error"
              />
            )}

            {/* Submit Button */}
            <button
              className="button primary full"
              type="submit"
              disabled={analyzeMutation.isPending}
              style={{ marginTop: "20px" }}
              id="submit-measured-analysis"
            >
              {analyzeMutation.isPending
                ? "Выполняется анализ…"
                : "Выполнить анализ измерения"}
            </button>
          </form>
        </aside>

        {/* RIGHT COLUMN: Results & Spectral Chart */}
        <section className="panel" aria-label="Результаты анализа">
          {/* Stale result warning if inputs changed */}
          {isInputsModifiedAfterAnalysis && (
            <div
              className="notice"
              style={{
                background: "#fffbeb",
                borderColor: "#fde047",
                color: "#854d0e",
                marginBottom: "16px",
              }}
              role="status"
            >
              <strong>Входные данные изменены:</strong>
              <span>
                Параметры формы изменились. Отображаемый ниже результат относится
                к предыдущему расчёту (
                <code>{activePrediction?.prediction_id}</code>). Нажмите «Выполнить
                анализ измерения» для пересчёта.
              </span>
            </div>
          )}

          {/* Pending state */}
          {analyzeMutation.isPending && (
            <Loading children="Сервер выполняет приборный анализ спектра и проверку покрытия…" />
          )}

          {/* URL query error */}
          {predictionQuery.error && (
            <ErrorNotice
              error={predictionQuery.error}
              id="prediction-query-error"
            />
          )}

          {/* Loading prediction by URL */}
          {predictionQuery.isLoading && (
            <Loading children="Загрузка сохранённого результата измерения…" />
          )}

          {/* Display Prediction Result */}
          {activePrediction ? (
            <div className="result" id="prediction-result-view">
              <div className="panel-heading" style={{ flexWrap: "wrap", gap: "12px" }}>
                <div>
                  <span className="eyebrow">РЕЗУЛЬТАТ ИЗМЕРЕНИЯ</span>
                  <h2>ID: {activePrediction.prediction_id}</h2>
                  <small style={{ color: "#64748b" }}>
                    Создано: {new Date(activePrediction.created_at).toLocaleString("ru")} · Прибор:{" "}
                    <strong>{activePrediction.instrument_id}</strong>
                  </small>
                </div>
                <div className="export-actions">
                  <button
                    type="button"
                    className="button btn-sm"
                    onClick={() =>
                      void api.exportMeasured(activePrediction.prediction_id, "json")
                    }
                  >
                    Экспорт JSON
                  </button>
                  <button
                    type="button"
                    className="button btn-sm"
                    onClick={() =>
                      void api.exportMeasured(activePrediction.prediction_id, "csv")
                    }
                  >
                    Экспорт CSV
                  </button>
                  <button
                    type="button"
                    className="button btn-sm primary"
                    onClick={() =>
                      void api.exportMeasured(activePrediction.prediction_id, "zip")
                    }
                  >
                    Экспорт ZIP
                  </button>
                </div>
              </div>

              {/* Summary Cards */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                  gap: "12px",
                  margin: "18px 0",
                }}
              >
                <div className="metric-box">
                  <small>ПРИБОРНЫЙ ПРОФИЛЬ</small>
                  <strong style={{ fontSize: "14px" }}>
                    {activePrediction.profile_identity || activePrediction.profile_id}
                  </strong>
                  <span style={{ fontSize: "11px", color: "#64748b" }}>
                    {activePrediction.scope}
                  </span>
                </div>
                <div className="metric-box">
                  <small>ОБНАРУЖЕНО ЭЛЕМЕНТОВ</small>
                  <strong style={{ color: "#166244" }}>
                    {activePrediction.detected_elements.length > 0
                      ? activePrediction.detected_elements.join(", ")
                      : "0 элементов"}
                  </strong>
                  <span style={{ fontSize: "11px", color: "#64748b" }}>
                    Классификация: multi-label
                  </span>
                </div>
                <div className="metric-box">
                  <small>ПРЕОБРАЗОВАНИЕ ОСИ</small>
                  <strong style={{ fontSize: "13px" }}>
                    {activePrediction.wavelength_conversion
                      ? `${activePrediction.wavelength_conversion.from} → ${activePrediction.wavelength_conversion.to}`
                      : "Без пересчёта (вакуум)"}
                  </strong>
                  <span style={{ fontSize: "11px", color: "#64748b" }}>
                    {activePrediction.wavelength_conversion
                      ? String(activePrediction.wavelength_conversion.method ?? "Peck-Reeder 1972")
                      : "Исходная система: вакуум"}
                  </span>
                </div>
                <div className="metric-box">
                  <small>СТАТУС КАЛИБРОВКИ</small>
                  <strong>
                    {activePrediction.calibrated ? "Калиброван" : "Исследовательский"}
                  </strong>
                  <span style={{ fontSize: "11px", color: "#64748b" }}>
                    {activePrediction.calibration_scope}
                  </span>
                </div>
              </div>

              {/* Elements Table */}
              <div style={{ margin: "24px 0" }}>
                <h3>Оценки присутствия элементов</h3>
                <p className="muted" style={{ marginBottom: "12px" }}>
                  Score отражает аналитический отклик модели и не является
                  вероятностью. Статус «Не определено» (unknown) означает отказ от
                  классификации профилем, а не доказательство отсутствия элемента.
                </p>

                <div className="table-scroll" tabIndex={0} aria-label="Таблица оценок элементов">
                  <table className="table-compact">
                    <thead>
                      <tr>
                        <th scope="col">Элемент</th>
                        <th scope="col">Статус профиля</th>
                        <th scope="col">Обнаружение</th>
                        <th scope="col">
                          Score <small>(отклик модели)</small>
                        </th>
                        <th scope="col">Порог</th>
                        <th scope="col">Line SNR</th>
                        <th scope="col">Диагностика и причина</th>
                      </tr>
                    </thead>
                    <tbody>
                      {activePrediction.predictions.map((item: MeasuredPredictionElement) => {
                        let detectionBadge = (
                          <span className="badge badge-warning">? Не определено</span>
                        );
                        if (item.status === "evaluated" && item.accepted) {
                          if (item.detected === true) {
                            detectionBadge = (
                              <span className="badge badge-success">✓ Обнаружен</span>
                            );
                          } else if (item.detected === false) {
                            detectionBadge = (
                              <span className="badge badge-neutral">✕ Не обнаружен</span>
                            );
                          }
                        }

                        const statusBadge =
                          item.status === "evaluated" ? (
                            <span className="badge badge-info">Оценен</span>
                          ) : (
                            <span className="badge badge-warning">Не поддерживается</span>
                          );

                        return (
                          <tr key={item.element}>
                            <th scope="row">
                              <strong>{item.element}</strong>
                            </th>
                            <td>{statusBadge}</td>
                            <td>{detectionBadge}</td>
                            <td className="mono">
                              {item.score !== null && item.score !== undefined
                                ? item.score.toFixed(4)
                                : "—"}
                            </td>
                            <td className="mono">
                              {item.threshold !== null && item.threshold !== undefined
                                ? item.threshold.toFixed(4)
                                : "—"}
                            </td>
                            <td className="mono">
                              {item.line_snr !== null && item.line_snr !== undefined
                                ? item.line_snr.toFixed(2)
                                : "—"}
                            </td>
                            <td>
                              <div>{formatReason(item.reason)}</div>
                              {item.reason && (
                                <code style={{ fontSize: "10px", marginTop: "2px", display: "inline-block" }}>
                                  код: {item.reason}
                                </code>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Chart: if we have local preview arrays in session */}
              {previewSpectrum ? (
                <div style={{ marginTop: "24px" }}>
                  <h3>Спектральный график загруженного измерения</h3>
                  <SpectrumChart
                    spectrum={previewSpectrum}
                    units="nm"
                    medium={medium || "vacuum"}
                  />
                </div>
              ) : (
                <div
                  className="notice"
                  style={{ marginTop: "20px" }}
                  role="note"
                >
                  <strong>График спектра:</strong>
                  <span>
                    Результат открыт по сохранённому идентификатору. Серверные
                    точки спектра доступны в полной записи архива (файл{" "}
                    <code>input.csv</code> в ZIP-экспорте).
                  </span>
                </div>
              )}

              {/* Technical provenance block */}
              <details style={{ marginTop: "24px" }}>
                <summary>Происхождение данных и контрольные суммы</summary>
                <div className="profile-meta-card">
                  <div className="metric-row">
                    <span className="metric-label">Хеш входного CSV (SHA-256):</span>
                    <span className="metric-value">
                      <code>{String(activePrediction.input_provenance?.input_sha256 ?? "—")}</code>
                    </span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Число точек спектра:</span>
                    <span className="metric-value">
                      {String(activePrediction.input_provenance?.input_points ?? "—")}
                    </span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Хеш метаданных (SHA-256):</span>
                    <span className="metric-value">
                      <code>{String(activePrediction.input_provenance?.metadata_sha256 ?? "—")}</code>
                    </span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Источник калибровки шкалы:</span>
                    <span className="metric-value">
                      {String(activePrediction.input_axis?.source ?? "—")}
                    </span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Хеш источника калибровки:</span>
                    <span className="metric-value">
                      <code>{String(activePrediction.input_axis?.source_sha256 ?? "—")}</code>
                    </span>
                  </div>
                </div>
              </details>

              {/* Expandable JSON details */}
              <details>
                <summary>Полный технический протокол ответа (JSON)</summary>
                <pre>{JSON.stringify(activePrediction, null, 2)}</pre>
              </details>
            </div>
          ) : previewSpectrum ? (
            /* When CSV uploaded but analysis not executed yet */
            <div>
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">ПРЕДВАРИТЕЛЬНЫЙ ПРОСМОТР</span>
                  <h2>Загруженный спектр ({csvFile?.name})</h2>
                  <small style={{ color: "#64748b" }}>
                    Точек: {csvPreview?.pointCount.toLocaleString("ru")} · Диапазон:{" "}
                    {csvPreview?.minWavelength.toFixed(2)}–{csvPreview?.maxWavelength.toFixed(2)} нм
                  </small>
                </div>
              </div>
              <p className="muted" style={{ margin: "10px 0 16px" }}>
                Спектр проверен на соответствие формату. Для запуска приборного анализа
                заполните обязательные поля оси и нажмите «Выполнить анализ измерения».
              </p>
              <SpectrumChart
                spectrum={previewSpectrum}
                units="nm"
                medium={medium || "vacuum"}
              />
            </div>
          ) : (
            /* Empty initial state */
            <div className="empty" style={{ padding: "40px 20px" }}>
              <span className="brand-mark" style={{ margin: "0 auto 16px" }}>
                ∿
              </span>
              <h3>Измерение не выбрано</h3>
              <p className="muted" style={{ maxWidth: "480px", margin: "8px auto" }}>
                Загрузите спектральный CSV файл в левой панели, подтвердите калибровку
                шкалы длин волн и прибор измерения, затем нажмите кнопку анализа.
              </p>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
