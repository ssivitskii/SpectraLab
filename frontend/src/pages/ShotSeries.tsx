import { useId, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  api,
  parseSeriesCsvPreview,
  type ParsedSeriesPreview,
  type SeriesAnalysisRequestJson,
  type SeriesAnalysisResponse,
} from "../api/client";
import { ErrorNotice, Loading } from "../components/Feedback";
import { Icon } from "../components/Icon";
import { SeriesChart } from "../components/SeriesChart";
import { PlasmaDiagnosticsCard } from "../components/PlasmaDiagnosticsCard";
import { QuantitativeAssayCard } from "../components/QuantitativeAssayCard";

const REASON_LABELS: Record<string, string> = {
  saturated: "Насыщение детектора",
  low_snr: "Низкий SNR",
  shape_outlier: "Аномалия формы (RMS outlier)",
  zero_dynamic_range: "Нулевой динамический диапазон",
};

const DEMO_SERIES_CSV = `# Synthetic demonstration only; not a real ChemCam measurement
# NSHOTS=9
# wave,shot1,shot2,shot3,shot4,shot5,shot6,shot7,shot8,shot9,median,mean
500.0,0,0,0,0,0,1,2,1,1,0,0
500.1,0,0,0,0,0,1,2,100,1,0,0
500.2,0,0,0,0,0,2,4,2,1,0,0
500.3,0,0,0,0,0,3,6,3,1,0,0
500.4,0,0,0,0,0,5,10,5,1,0,0
500.5,0,0,0,0,0,8,16,8,1,0,0
500.6,0,0,0,0,0,5,10,5,1,0,0
500.7,0,0,0,0,0,3,6,3,1,0,0
500.8,0,0,0,0,0,2,4,2,1,0,0
500.9,0,0,0,0,0,1,2,1,1,0,0
501.0,0,0,0,0,0,1,2,1,1,0,0`;

const DEMO_METADATA = {
  schema_version: 1,
  measurement_id: "synthetic-shot-series-demo",
  physical_sample_id: "synthetic-material-demo",
  family_id: "synthetic-family-demo",
  session_id: "synthetic-session-demo",
  instrument_id: "synthetic-fixture-only",
};

export function ShotSeries() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlSeriesId = searchParams.get("series") ?? "";

  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvContent, setCsvContent] = useState<string>("");
  const [parsedPreview, setParsedPreview] = useState<ParsedSeriesPreview | null>(null);
  const [discardFirstShots, setDiscardFirstShots] = useState<number>(5);
  const [minSnr, setMinSnr] = useState<number>(3.0);
  const [minAcceptedShots, setMinAcceptedShots] = useState<number>(2);
  const [saturationCeiling, setSaturationCeiling] = useState<string>("");
  const [outlierMadZ, setOutlierMadZ] = useState<number>(6.0);

  const [measurementId, setMeasurementId] = useState<string>("");
  const [physicalSampleId, setPhysicalSampleId] = useState<string>("");
  const [familyId, setFamilyId] = useState<string>("");
  const [sessionId, setSessionId] = useState<string>("");
  const [instrumentId, setInstrumentId] = useState<string>("");

  const [activeShotNumber, setActiveShotNumber] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const discardInputId = useId();
  const minSnrInputId = useId();
  const minAcceptedInputId = useId();
  const saturationInputId = useId();
  const outlierInputId = useId();

  // Query existing series by ID from URL
  const seriesQuery = useQuery({
    queryKey: ["seriesDetail", urlSeriesId],
    queryFn: ({ signal }) => api.series(urlSeriesId, { signal }),
    enabled: Boolean(urlSeriesId),
    retry: false,
  });

  // Query series list for history
  const seriesListQuery = useQuery({
    queryKey: ["seriesList"],
    queryFn: ({ signal }) => api.seriesList({ signal, limit: 15 }),
  });

  // Analyze mutation
  const analyzeMutation = useMutation({
    mutationFn: async () => {
      setErrorMessage(null);
      let body: FormData | SeriesAnalysisRequestJson;

      const meta: Record<string, unknown> = {};
      if (measurementId.trim()) meta.measurement_id = measurementId.trim();
      if (physicalSampleId.trim()) meta.physical_sample_id = physicalSampleId.trim();
      if (familyId.trim()) meta.family_id = familyId.trim();
      if (sessionId.trim()) meta.session_id = sessionId.trim();
      if (instrumentId.trim()) meta.instrument_id = instrumentId.trim();

      const satVal = saturationCeiling.trim() ? Number(saturationCeiling.trim()) : undefined;

      if (csvFile) {
        const formData = new FormData();
        formData.append("file", csvFile);
        formData.append("discard_first_shots", String(discardFirstShots));
        formData.append("min_snr", String(minSnr));
        formData.append("min_accepted_shots", String(minAcceptedShots));
        formData.append("outlier_mad_z", String(outlierMadZ));
        if (satVal !== undefined && Number.isFinite(satVal)) {
          formData.append("saturation_ceiling", String(satVal));
        }
        if (Object.keys(meta).length > 0) {
          formData.append("metadata", JSON.stringify(meta));
        }
        body = formData;
      } else if (csvContent.trim()) {
        body = {
          csv_content: csvContent,
          metadata: Object.keys(meta).length > 0 ? meta : null,
          discard_first_shots: discardFirstShots,
          min_snr: minSnr,
          min_accepted_shots: minAcceptedShots,
          saturation_ceiling: satVal ?? null,
          outlier_mad_z: outlierMadZ,
        };
      } else {
        throw new Error("Выберите CSV-файл или загрузите демо-набор выстрелов.");
      }

      return api.analyzeSeries(body);
    },
    onSuccess: (data) => {
      setSearchParams({ series: data.series_id });
      void seriesListQuery.refetch();
    },
    onError: (err: Error) => {
      setErrorMessage(err.message || "Ошибка при анализе серии.");
    },
  });

  // Load demo series
  const handleLoadDemo = () => {
    setErrorMessage(null);
    setCsvFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    setCsvContent(DEMO_SERIES_CSV);
    setDiscardFirstShots(5);
    setMinSnr(3.0);
    setMinAcceptedShots(2);
    setSaturationCeiling("");
    setOutlierMadZ(6.0);
    setMeasurementId(DEMO_METADATA.measurement_id);
    setPhysicalSampleId(DEMO_METADATA.physical_sample_id);
    setFamilyId(DEMO_METADATA.family_id);
    setSessionId(DEMO_METADATA.session_id);
    setInstrumentId(DEMO_METADATA.instrument_id);
    try {
      const parsed = parseSeriesCsvPreview(DEMO_SERIES_CSV);
      setParsedPreview(parsed);
    } catch {
      setParsedPreview(null);
    }
  };

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    setErrorMessage(null);
    if (!file) {
      setCsvFile(null);
      setParsedPreview(null);
      return;
    }
    setCsvFile(file);
    try {
      const text = await file.text();
      setCsvContent(text);
      const parsed = parseSeriesCsvPreview(text);
      setParsedPreview(parsed);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Не удалось разобрать CSV файл";
      setErrorMessage(msg);
      setParsedPreview(null);
    }
  };

  const handleStartNew = () => {
    setSearchParams({});
    setCsvFile(null);
    setCsvContent("");
    setParsedPreview(null);
    setErrorMessage(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    analyzeMutation.reset();
  };

  const handleDeleteSeries = async (id: string) => {
    try {
      await api.deleteSeries(id);
      if (urlSeriesId === id) {
        handleStartNew();
      }
      void seriesListQuery.refetch();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Ошибка при удалении серии";
      setErrorMessage(msg);
    }
  };

  const activeSeries: SeriesAnalysisResponse | undefined =
    seriesQuery.data ?? analyzeMutation.data;

  const rejectedCount = useMemo(() => {
    if (!activeSeries) return 0;
    return activeSeries.shots.filter((s) => s.status === "rejected_quality").length;
  }, [activeSeries]);

  return (
    <>
      <header className="page-title">
        <div>
          <h1>Серии выстрелов и контроль качества</h1>
          <p>
            Пакетный контроль качества ChemCam/LIBS выстрелов, исключение насыщения детектора и аномалий лазерного факела.
          </p>
        </div>
        <div className="measured-page-actions">
          {activeSeries && (
            <button type="button" onClick={handleStartNew} id="new-series-button">
              <Icon name="plus" size={17} />
              Новая серия
            </button>
          )}
          <button type="button" onClick={handleLoadDemo} id="load-demo-series-button" className="button">
            <Icon name="flask" size={17} />
            Демо-серия
          </button>
        </div>
      </header>

      <div className="notice info measured-scope" role="note">
        <Icon name="info" />
        <div>
          <strong>Робастная медианная агрегация</strong>
          <p>
            Выстрелы одной серии — зависимые повторные наблюдения. Контроль качества отсекает насыщение и shape-outliers,
            формируя медианный агрегат и дисперсию MAD.
          </p>
        </div>
      </div>

      <div className="workspace measured-workspace">
        {/* Панель параметров */}
        <aside className="panel measured-controls" aria-label="Параметры серии выстрелов">
          <div className="panel-heading">
            <h2>Параметры серии и QC</h2>
            <Icon name="series" />
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              analyzeMutation.mutate();
            }}
            noValidate
          >
            <fieldset className="measured-fieldset" disabled={analyzeMutation.isPending}>
              <section className="measured-form-section">
                <h3>Входные спектры серии</h3>
                <div className="field-group">
                  <label htmlFor="series-file-input">
                    CSV файл серии выстрелов <span aria-hidden="true">*</span>
                  </label>
                  <input
                    ref={fileInputRef}
                    id="series-file-input"
                    type="file"
                    accept=".csv,text/csv"
                    onChange={handleFileChange}
                  />
                  {parsedPreview && (
                    <div className="field-hint" style={{ marginTop: "4px" }}>
                      Обнаружено: {parsedPreview.shotCount} выстрелов, {parsedPreview.pointCount} точек ({parsedPreview.minWavelength}–{parsedPreview.maxWavelength} нм)
                    </div>
                  )}
                  {csvContent && !csvFile && (
                    <div className="badge badge-info" style={{ marginTop: "6px" }}>
                      Используется демо-набор (9 выстрелов)
                    </div>
                  )}
                </div>
              </section>

              <section className="measured-form-section">
                <h3>Политика контроля качества (QC)</h3>
                <div className="field-group">
                  <label htmlFor={discardInputId}>Пропуск первых выстрелов (зачистка)</label>
                  <input
                    id={discardInputId}
                    type="number"
                    min="0"
                    value={discardFirstShots}
                    onChange={(e) => setDiscardFirstShots(Math.max(0, parseInt(e.target.value) || 0))}
                  />
                  <span className="field-hint">Лазерная зачистка пыли с поверхности образца (обычно 5)</span>
                </div>

                <div className="field-group">
                  <label htmlFor={minSnrInputId}>Минимальный SNR аналитических линий</label>
                  <input
                    id={minSnrInputId}
                    type="number"
                    step="0.5"
                    min="0"
                    value={minSnr}
                    onChange={(e) => setMinSnr(Math.max(0, parseFloat(e.target.value) || 0))}
                  />
                  <span className="field-hint">Порог отношения сигнал/шум для отсева слабых выстрелов</span>
                </div>

                <div className="field-group">
                  <label htmlFor={minAcceptedInputId}>Минимум годных выстрелов</label>
                  <input
                    id={minAcceptedInputId}
                    type="number"
                    min="1"
                    value={minAcceptedShots}
                    onChange={(e) => setMinAcceptedShots(Math.max(1, parseInt(e.target.value) || 1))}
                  />
                  <span className="field-hint">Минимально допустимое число принятых выстрелов</span>
                </div>

                <div className="field-group">
                  <label htmlFor={outlierInputId}>Порог выбросов формы (MAD Z-Score)</label>
                  <input
                    id={outlierInputId}
                    type="number"
                    step="0.5"
                    min="1"
                    value={outlierMadZ}
                    onChange={(e) => setOutlierMadZ(Math.max(1, parseFloat(e.target.value) || 1))}
                  />
                  <span className="field-hint">Отсечение аномалий формы лазерного факела</span>
                </div>

                <div className="field-group">
                  <label htmlFor={saturationInputId}>Порог насыщения детектора (опц.)</label>
                  <input
                    id={saturationInputId}
                    type="number"
                    placeholder="Не задан"
                    value={saturationCeiling}
                    onChange={(e) => setSaturationCeiling(e.target.value)}
                  />
                  <span className="field-hint">Максимальный предел линейности АЦП</span>
                </div>
              </section>

              <section className="measured-form-section">
                <h3>Метаданные серии (опционально)</h3>
                <div className="field-group">
                  <label htmlFor="series-meas-id">ID измерения</label>
                  <input
                    id="series-meas-id"
                    type="text"
                    placeholder="Например, ChemCam_Sol0123"
                    value={measurementId}
                    onChange={(e) => setMeasurementId(e.target.value)}
                  />
                </div>
                <div className="field-group">
                  <label htmlFor="series-sample-id">Физический образец</label>
                  <input
                    id="series-sample-id"
                    type="text"
                    placeholder="Образец / мишень"
                    value={physicalSampleId}
                    onChange={(e) => setPhysicalSampleId(e.target.value)}
                  />
                </div>
              </section>

              <div className="form-actions" style={{ marginTop: "16px" }}>
                <button
                  type="submit"
                  className="primary full"
                  disabled={analyzeMutation.isPending || (!csvFile && !csvContent.trim())}
                >
                  {analyzeMutation.isPending ? "Обработка серии…" : "Анализировать серию"}
                </button>
              </div>
            </fieldset>
          </form>
        </aside>

        {/* Основная рабочая область */}
        <section className="measured-results" aria-label="Результаты контроля качества серии">
          {errorMessage && !analyzeMutation.isError && (
            <ErrorNotice error={new Error(errorMessage)} id="series-client-error" />
          )}
          {analyzeMutation.isError && <ErrorNotice error={analyzeMutation.error} id="series-api-error" />}
          {seriesQuery.isError && <ErrorNotice error={seriesQuery.error} id="series-query-error" />}

          {analyzeMutation.isPending && (
            <div className="panel" style={{ padding: "40px", textAlign: "center" }}>
              <Loading>Выполняется проверка качества выстрелов и расчет медианного спектра…</Loading>
            </div>
          )}

          {!activeSeries && !analyzeMutation.isPending && (
            <div className="panel" style={{ textAlign: "center", padding: "48px 24px" }}>
              <Icon name="series" size={48} className="muted" />
              <h2 style={{ marginTop: "16px", marginBottom: "8px" }}>Серия выстрелов не загружена</h2>
              <p className="muted" style={{ maxWidth: "480px", margin: "0 auto 20px" }}>
                Загрузите многоколоночный CSV выстрелов ChemCam или нажмите «Демо-серия» в верхней панели для ознакомления.
              </p>
              <button type="button" className="button" onClick={handleLoadDemo}>
                Загрузить демонстрационную серию
              </button>
            </div>
          )}

          {activeSeries && (
            <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
              {/* Сводка качества */}
              <div className="panel">
                <div className="panel-heading">
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <h2>Сводка качества серии</h2>
                    <span
                      className={`badge ${
                        activeSeries.status === "completed" ? "badge-success" : "badge-danger"
                      }`}
                    >
                      {activeSeries.status === "completed" ? "Успешно агрегирована" : "Недостаточно выстрелов"}
                    </span>
                  </div>
                  <span className="mono muted" style={{ fontSize: "11px" }}>
                    ID: {activeSeries.series_id}
                  </span>
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
                    gap: "12px",
                    marginTop: "12px",
                  }}
                >
                  <div className="stat-card" style={{ padding: "12px", background: "var(--input)", borderRadius: "6px" }}>
                    <div className="muted" style={{ fontSize: "11px" }}>Всего выстрелов</div>
                    <div style={{ fontSize: "20px", fontWeight: 600 }}>{activeSeries.shot_count}</div>
                  </div>
                  <div className="stat-card" style={{ padding: "12px", background: "var(--input)", borderRadius: "6px" }}>
                    <div className="muted" style={{ fontSize: "11px" }}>Принято QC</div>
                    <div style={{ fontSize: "20px", fontWeight: 600, color: "var(--success)" }}>
                      {activeSeries.accepted_shot_count}
                    </div>
                  </div>
                  <div className="stat-card" style={{ padding: "12px", background: "var(--input)", borderRadius: "6px" }}>
                    <div className="muted" style={{ fontSize: "11px" }}>Отклонено QC</div>
                    <div style={{ fontSize: "20px", fontWeight: 600, color: rejectedCount > 0 ? "var(--danger)" : "var(--muted)" }}>
                      {rejectedCount}
                    </div>
                  </div>
                  <div className="stat-card" style={{ padding: "12px", background: "var(--input)", borderRadius: "6px" }}>
                    <div className="muted" style={{ fontSize: "11px" }}>Зачистка (пропуск)</div>
                    <div style={{ fontSize: "20px", fontWeight: 600 }}>{activeSeries.discard_first_shots}</div>
                  </div>
                </div>

                {activeSeries.measurement && (
                  <div style={{ marginTop: "14px", fontSize: "12px", color: "var(--muted)" }}>
                    <span>Образец: <strong>{String(activeSeries.measurement.physical_sample_id ?? "—")}</strong></span>
                    <span style={{ margin: "0 8px" }}>·</span>
                    <span>Сессия: <strong>{String(activeSeries.measurement.session_id ?? "—")}</strong></span>
                  </div>
                )}
              </div>

              {/* Интерактивный график серии */}
              <div className="panel">
                <div className="panel-heading">
                  <h2>Спектральный профиль серии</h2>
                  <span className="muted" style={{ fontSize: "12px" }}>
                    Медиана и полоса разброса MAD
                  </span>
                </div>
                <SeriesChart
                  aggregate={activeSeries.aggregate}
                  shots={activeSeries.shots}
                  previewShots={parsedPreview?.previewShots}
                  previewWavelengths={parsedPreview?.previewWavelengths}
                  activeShotNumber={activeShotNumber}
                />
              </div>

              {/* Физическая диагностика плазмы по агрегированному спектру */}
              {activeSeries.aggregate && activeSeries.aggregate.length >= 10 && (
                <>
                  <PlasmaDiagnosticsCard
                    wavelengthNm={activeSeries.aggregate.map((p) => p.wavelength_nm)}
                    intensity={activeSeries.aggregate.map((p) => p.intensity)}
                    medium="vacuum"
                  />
                  <QuantitativeAssayCard
                    wavelengthNm={activeSeries.aggregate.map((p) => p.wavelength_nm)}
                    intensity={activeSeries.aggregate.map((p) => p.intensity)}
                    medium="vacuum"
                  />
                </>
              )}

              {/* Таблица контроля качества по каждому выстрелу */}
              <div className="panel">
                <div className="panel-heading">
                  <h2>Диагностика каждого выстрела серии</h2>
                  <span className="muted" style={{ fontSize: "12px" }}>
                    Наведите на строку для выделения выстрела на графике
                  </span>
                </div>

                <div style={{ overflowX: "auto" }}>
                  <table className="table" style={{ width: "100%", fontSize: "12px" }}>
                    <thead>
                      <tr>
                        <th>Выстрел #</th>
                        <th>Статус</th>
                        <th>SNR</th>
                        <th>Отклонение формы (RMS)</th>
                        <th>Причина отбраковки</th>
                      </tr>
                    </thead>
                    <tbody>
                      {activeSeries.shots.map((shot) => {
                        const isRejected = shot.status === "rejected_quality";
                        const isActive = activeShotNumber === shot.shot_number;
                        return (
                          <tr
                            key={shot.shot_number}
                            onMouseEnter={() => setActiveShotNumber(shot.shot_number)}
                            onMouseLeave={() => setActiveShotNumber(null)}
                            style={{
                              background: isActive ? "var(--surface-raised)" : undefined,
                              cursor: "pointer",
                            }}
                          >
                            <td className="mono">
                              <strong>#{shot.shot_number}</strong> (индекс {shot.shot_index})
                            </td>
                            <td>
                              <span
                                className={`badge ${
                                  !isRejected ? "badge-success" : "badge-danger"
                                }`}
                              >
                                {!isRejected ? "Принят" : "Отклонён"}
                              </span>
                            </td>
                            <td className="mono">
                              {shot.snr !== null ? shot.snr.toFixed(2) : "—"}
                            </td>
                            <td className="mono">
                              {shot.normalised_shape_distance !== null
                                ? shot.normalised_shape_distance.toFixed(4)
                                : "—"}
                            </td>
                            <td>
                              {shot.reasons.length > 0 ? (
                                <div style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
                                  {shot.reasons.map((r) => (
                                    <span key={r} className="badge badge-warning" title={r}>
                                      {REASON_LABELS[r] ?? r}
                                    </span>
                                  ))}
                                </div>
                              ) : (
                                <span className="muted">—</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Экспорт артефактов и квитанций */}
              <div className="panel">
                <div className="panel-heading">
                  <h2>Экспорт воспроизводимых результатов</h2>
                  <Icon name="download" />
                </div>
                <p className="muted" style={{ fontSize: "13px", marginTop: "4px" }}>
                  Все файлы зафиксированы контрольными суммами SHA-256 в квитанции исследования.
                </p>

                <div className="export-actions" style={{ display: "flex", gap: "10px", flexWrap: "wrap", marginTop: "12px" }}>
                  <button
                    type="button"
                    className="button"
                    onClick={() => api.downloadSeriesFile(activeSeries.series_id, "receipt.json")}
                  >
                    <Icon name="file" size={16} />
                    Скачать receipt.json
                  </button>

                  {activeSeries.outputs?.["aggregate.csv"] && (
                    <button
                      type="button"
                      className="button"
                      onClick={() => api.downloadSeriesFile(activeSeries.series_id, "aggregate.csv")}
                    >
                      <Icon name="download" size={16} />
                      Скачать aggregate.csv
                    </button>
                  )}

                  {activeSeries.outputs?.["dispersion.csv"] && (
                    <button
                      type="button"
                      className="button"
                      onClick={() => api.downloadSeriesFile(activeSeries.series_id, "dispersion.csv")}
                    >
                      <Icon name="download" size={16} />
                      Скачать dispersion.csv
                    </button>
                  )}

                  {activeSeries.outputs?.["metadata.json"] && (
                    <button
                      type="button"
                      className="button"
                      onClick={() => api.downloadSeriesFile(activeSeries.series_id, "metadata.json")}
                    >
                      <Icon name="file" size={16} />
                      Скачать metadata.json
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* История серий */}
          {seriesListQuery.data && seriesListQuery.data.items.length > 0 && (
            <div className="panel" style={{ marginTop: "24px" }}>
              <div className="panel-heading">
                <h2>Недавние серии измерений</h2>
                <span className="muted" style={{ fontSize: "12px" }}>
                  Всего сохранено: {seriesListQuery.data.total}
                </span>
              </div>
              <div style={{ overflowX: "auto" }}>
                <table className="table" style={{ width: "100%", fontSize: "12px" }}>
                  <thead>
                    <tr>
                      <th>ID серии</th>
                      <th>Дата</th>
                      <th>Статус</th>
                      <th>Принято / Всего</th>
                      <th>Образец</th>
                      <th>Действия</th>
                    </tr>
                  </thead>
                  <tbody>
                    {seriesListQuery.data.items.map((item) => (
                      <tr key={item.series_id}>
                        <td className="mono">
                          <Link to={`/series?series=${item.series_id}`} style={{ color: "var(--accent)" }}>
                            {item.series_id.slice(0, 12)}…
                          </Link>
                        </td>
                        <td className="muted">{item.created_at ? new Date(item.created_at).toLocaleString("ru") : "—"}</td>
                        <td>
                          <span
                            className={`badge ${
                              item.status === "completed" ? "badge-success" : "badge-danger"
                            }`}
                          >
                            {item.status}
                          </span>
                        </td>
                        <td className="mono">
                          {item.accepted_shot_count} / {item.shot_count}
                        </td>
                        <td>{item.physical_sample_id || item.measurement_id || "—"}</td>
                        <td>
                          <div style={{ display: "flex", gap: "6px" }}>
                            <Link className="button" to={`/series?series=${item.series_id}`} style={{ padding: "4px 8px", fontSize: "11px" }}>
                              Открыть
                            </Link>
                            <button
                              type="button"
                              className="button"
                              onClick={() => handleDeleteSeries(item.series_id)}
                              style={{ padding: "4px 8px", fontSize: "11px", color: "var(--danger)" }}
                            >
                              Удалить
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
