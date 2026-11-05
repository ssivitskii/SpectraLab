import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  type QuantitativePredictionDto,
  type QuantitativeReportResponse,
} from "../api/client";
import { ApplicabilityDomainChart } from "./ApplicabilityDomainChart";
import { Loading } from "./Feedback";
import { Icon } from "./Icon";

const SPECIES_LABELS: Record<string, { display: string; name: string }> = {
  FeOT: { display: "FeO(T)", name: "Общее железо" },
  MgO: { display: "MgO", name: "Оксид магния" },
  CaO: { display: "CaO", name: "Оксид кальция" },
  TiO2: { display: "TiO₂", name: "Диоксид титана" },
  SiO2: { display: "SiO₂", name: "Диоксид кремния" },
  Al2O3: { display: "Al₂O₃", name: "Оксид алюминия" },
};

function formatSpeciesDisplay(species: string): string {
  return SPECIES_LABELS[species]?.display ?? species;
}

function formatSpeciesName(species: string): string {
  return SPECIES_LABELS[species]?.name ?? "";
}

function getStatusBadge(status: QuantitativePredictionDto["status"]) {
  switch (status) {
    case "in_domain":
      return <span className="badge badge-success">В калибровке</span>;
    case "near_boundary":
      return <span className="badge badge-warning">Граница AD</span>;
    case "out_of_domain":
      return <span className="badge badge-danger">Экстраполяция (OOD)</span>;
    case "below_lod":
      return <span className="badge">&lt; LOD</span>;
    default:
      return <span className="badge">{status}</span>;
  }
}

export function QuantitativeAssayCard({
  spectrumId,
  wavelengthNm,
  intensity,
  medium = "vacuum",
}: {
  spectrumId?: string;
  wavelengthNm?: number[];
  intensity?: number[];
  medium?: string;
}) {
  const [confidenceLevel, setConfidenceLevel] = useState<number>(0.95);
  const [showTable, setShowTable] = useState<boolean>(false);
  const [showPCA, setShowPCA] = useState<boolean>(false);

  const isEnabled = Boolean(
    spectrumId || (wavelengthNm && intensity && wavelengthNm.length >= 10),
  );

  const query = useQuery<QuantitativeReportResponse>({
    queryKey: [
      "quantitativePredict",
      spectrumId,
      confidenceLevel,
      medium,
      wavelengthNm?.length,
    ],
    queryFn: ({ signal }) =>
      api.quantitativePredict(
        {
          spectrum_id: spectrumId ?? null,
          wavelength_nm: spectrumId ? null : (wavelengthNm ?? null),
          intensity: spectrumId ? null : (intensity ?? null),
          confidence_level: confidenceLevel,
          medium: (medium === "air" ? "air" : "vacuum") as "air" | "vacuum",
        },
        { signal },
      ),
    enabled: isEnabled,
    staleTime: 60_000,
  });

  const report = query.data;

  return (
    <section
      className="panel"
      data-testid="quantitative-assay-card"
      style={{ display: "flex", flexDirection: "column", gap: "1rem" }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "0.75rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <Icon name="flask" size={20} />
          <div>
            <h3 style={{ margin: 0, fontSize: "1.05rem", fontWeight: 600 }}>
              Количественный анализ (Assay & Конформные интервалы)
            </h3>
            <span className="muted" style={{ fontSize: "0.82rem" }}>
              Модель Ridge с гарантированным покрытием неопределенности и контролем применимости (AD)
            </span>
          </div>
        </div>

        {/* Controls */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
          <label
            htmlFor="confidence-select"
            style={{ fontSize: "0.85rem", color: "var(--muted)", display: "flex", alignItems: "center", gap: "0.3rem" }}
          >
            Доверие (1-α):
          </label>
          <select
            id="confidence-select"
            data-testid="confidence-select"
            className="select"
            style={{ padding: "0.25rem 0.6rem", fontSize: "0.85rem" }}
            value={confidenceLevel}
            onChange={(e) => setConfidenceLevel(Number(e.target.value))}
          >
            <option value={0.9}>90%</option>
            <option value={0.95}>95% (стандарт)</option>
            <option value={0.99}>99% (консервативный)</option>
          </select>
        </div>
      </div>

      {/* Loading state */}
      {query.isLoading && (
        <div style={{ padding: "2rem", display: "flex", justifyContent: "center" }}>
          <Loading>Расчёт количественных концентраций и конформных интервалов...</Loading>
        </div>
      )}

      {/* Error state */}
      {query.isError && (
        <div
          data-testid="quantitative-error"
          className="badge badge-danger"
          style={{ padding: "0.75rem", width: "100%", justifyContent: "flex-start" }}
        >
          <Icon name="info" size={16} />
          <span>
            Ошибка количественной оценки:{" "}
            {query.error instanceof Error
              ? query.error.message
              : "Не удалось получить концентрации"}
          </span>
        </div>
      )}

      {/* Success Content */}
      {query.isSuccess && report && (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {/* Summary Banner */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "0.75rem",
              padding: "0.75rem 1rem",
              background: "var(--surface-sunken)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--border-subtle)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
              <span style={{ fontSize: "0.88rem", fontWeight: 500 }}>
                {report.applicability_domain_summary}
              </span>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
              <div style={{ textAlign: "right" }}>
                <span className="muted" style={{ fontSize: "0.75rem", display: "block" }}>
                  Сумма определенных оксидов:
                </span>
                <span style={{ fontSize: "1rem", fontWeight: 700, color: "var(--foreground)" }}>
                  {report.total_detected_mass_wt_percent.toFixed(2)} wt%
                </span>
              </div>
              <button
                type="button"
                data-testid="toggle-pca-btn"
                className="button button-small button-ghost"
                onClick={() => setShowPCA(!showPCA)}
              >
                <Icon name={showPCA ? "chevronDown" : "search"} size={14} />
                {showPCA ? "Скрыть AD" : "Пространство AD (PCA)"}
              </button>
              <button
                type="button"
                data-testid="toggle-table-btn"
                className="button button-small button-ghost"
                onClick={() => setShowTable(!showTable)}
              >
                <Icon name={showTable ? "chevronDown" : "sliders"} size={14} />
                {showTable ? "Скрыть таблицу" : "Сводная таблица"}
              </button>
            </div>
          </div>

          {/* Applicability Domain Space Chart */}
          {showPCA && (
            <ApplicabilityDomainChart
              spectrumId={spectrumId}
              wavelengthNm={wavelengthNm}
              intensity={intensity}
              medium={medium}
            />
          )}

          {/* Oxide Grid */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
              gap: "0.75rem",
            }}
          >
            {report.predictions.map((p) => {
              const ppmValue = Math.round(p.value_wt_percent * 10_000);
              const maxDisplay = Math.max(p.upper_wt_percent * 1.25, 20.0);
              const barLeftPercent = Math.min(100, Math.max(0, (p.lower_wt_percent / maxDisplay) * 100));
              const barWidthPercent = Math.min(100 - barLeftPercent, Math.max(3, ((p.upper_wt_percent - p.lower_wt_percent) / maxDisplay) * 100));
              const pointPercent = Math.min(100, Math.max(0, (p.value_wt_percent / maxDisplay) * 100));

              return (
                <div
                  key={p.species}
                  data-testid={`oxide-card-${p.species}`}
                  className="metric-box"
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "0.5rem",
                    padding: "0.85rem 1rem",
                    background: "var(--surface)",
                  }}
                >
                  {/* Card Title & Badges */}
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "flex-start",
                    }}
                  >
                    <div>
                      <div style={{ display: "flex", alignItems: "baseline", gap: "0.4rem" }}>
                        <span style={{ fontSize: "1.05rem", fontWeight: 700 }}>
                          {formatSpeciesDisplay(p.species)}
                        </span>
                        <span className="muted" style={{ fontSize: "0.82rem" }}>
                          ({p.element})
                        </span>
                      </div>
                      <span className="muted" style={{ fontSize: "0.78rem" }}>
                        {formatSpeciesName(p.species)}
                      </span>
                    </div>
                    {getStatusBadge(p.status)}
                  </div>

                  {/* Value and Interval */}
                  <div style={{ marginTop: "0.15rem" }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: "0.5rem" }}>
                      <span className="metric-value" style={{ fontSize: "1.45rem", lineHeight: 1.1 }}>
                        {p.status === "below_lod" ? (
                          <span style={{ color: "var(--muted)", fontSize: "1.1rem" }}>
                            &lt; {p.lod_wt_percent.toFixed(2)} wt%
                          </span>
                        ) : (
                          `${p.value_wt_percent.toFixed(2)} wt%`
                        )}
                      </span>
                      {p.status !== "below_lod" && (
                        <span className="muted" style={{ fontSize: "0.82rem" }}>
                          ({ppmValue.toLocaleString("ru-RU")} ppm)
                        </span>
                      )}
                    </div>

                    <div style={{ fontSize: "0.8rem", color: "var(--muted)", marginTop: "0.2rem" }}>
                      Интервал ({Math.round(confidenceLevel * 100)}%):{" "}
                      <strong style={{ color: "var(--foreground)" }}>
                        [{p.lower_wt_percent.toFixed(2)} — {p.upper_wt_percent.toFixed(2)}] wt%
                      </strong>
                    </div>
                  </div>

                  {/* Visual Conformal Range Bar */}
                  <div style={{ marginTop: "0.2rem" }}>
                    <div
                      style={{
                        position: "relative",
                        height: "8px",
                        background: "var(--surface-sunken)",
                        borderRadius: "4px",
                        overflow: "hidden",
                        border: "1px solid var(--border-subtle)",
                      }}
                    >
                      {/* Interval range bar */}
                      <div
                        style={{
                          position: "absolute",
                          left: `${barLeftPercent}%`,
                          width: `${barWidthPercent}%`,
                          top: 0,
                          bottom: 0,
                          background:
                            p.status === "out_of_domain"
                              ? "var(--danger-soft)"
                              : p.status === "near_boundary"
                              ? "var(--warning-soft)"
                              : "var(--accent-soft)",
                          borderLeft: "1px solid var(--accent)",
                          borderRight: "1px solid var(--accent)",
                        }}
                      />
                      {/* Point marker */}
                      <div
                        style={{
                          position: "absolute",
                          left: `calc(${pointPercent}% - 2px)`,
                          width: "4px",
                          top: 0,
                          bottom: 0,
                          background:
                            p.status === "out_of_domain"
                              ? "var(--danger)"
                              : p.status === "near_boundary"
                              ? "var(--warning)"
                              : "var(--accent)",
                          borderRadius: "2px",
                        }}
                      />
                    </div>
                  </div>

                  {/* Leverage & Warning footer */}
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      fontSize: "0.75rem",
                      color: "var(--muted)",
                      marginTop: "0.2rem",
                    }}
                  >
                    <span>LOD: {p.lod_wt_percent.toFixed(2)} wt%</span>
                    <span>h(x): {p.leverage.toFixed(2)}</span>
                  </div>

                  {p.warning && (
                    <div
                      style={{
                        fontSize: "0.75rem",
                        color: p.status === "out_of_domain" ? "var(--danger)" : "var(--warning)",
                        lineHeight: 1.25,
                      }}
                    >
                      {p.warning}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Full Collapsible Table */}
          {showTable && (
            <div
              style={{
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-md)",
                overflow: "hidden",
                background: "var(--surface)",
              }}
            >
              <table className="table" style={{ fontSize: "0.85rem", width: "100%" }}>
                <thead>
                  <tr>
                    <th>Оксид</th>
                    <th>Элемент</th>
                    <th>Оценка (wt%)</th>
                    <th>Интервал ({Math.round(confidenceLevel * 100)}%)</th>
                    <th>LOD (wt%)</th>
                    <th>Leverage h(x)</th>
                    <th>Статус AD</th>
                  </tr>
                </thead>
                <tbody>
                  {report.predictions.map((p) => (
                    <tr key={p.species}>
                      <td>
                        <strong>{formatSpeciesDisplay(p.species)}</strong>
                        <span className="muted" style={{ fontSize: "0.75rem", marginLeft: "0.4rem" }}>
                          {formatSpeciesName(p.species)}
                        </span>
                      </td>
                      <td>{p.element}</td>
                      <td>
                        {p.status === "below_lod"
                          ? `< ${p.lod_wt_percent.toFixed(2)}`
                          : p.value_wt_percent.toFixed(2)}
                      </td>
                      <td>
                        [{p.lower_wt_percent.toFixed(2)} — {p.upper_wt_percent.toFixed(2)}]
                      </td>
                      <td>{p.lod_wt_percent.toFixed(2)}</td>
                      <td>{p.leverage.toFixed(2)}</td>
                      <td>{getStatusBadge(p.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
