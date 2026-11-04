import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Data, Layout } from "plotly.js";
import { api, type PlasmaDiagnosticsResponse } from "../api/client";
import { Chart } from "./Chart";
import { chartColors, photonChartLayout } from "./chartTheme";
import { Icon } from "./Icon";
import { Loading } from "./Feedback";

export function PlasmaDiagnosticsCard({
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
  const [selectedElement, setSelectedElement] = useState<string>("auto");
  const [fwhmNm, setFwhmNm] = useState<number>(0.1);
  const [showTable, setShowTable] = useState<boolean>(false);

  const isEnabled = Boolean(
    spectrumId || (wavelengthNm && intensity && wavelengthNm.length >= 10),
  );

  const query = useQuery<PlasmaDiagnosticsResponse>({
    queryKey: [
      "plasmaDiagnostics",
      spectrumId,
      selectedElement,
      fwhmNm,
      medium,
      wavelengthNm?.length,
    ],
    queryFn: ({ signal }) =>
      api.plasmaDiagnostics(
        {
          spectrum_id: spectrumId ?? null,
          wavelength_nm: spectrumId ? null : (wavelengthNm ?? null),
          intensity: spectrumId ? null : (intensity ?? null),
          element: selectedElement,
          instrumental_fwhm_nm: fwhmNm,
          medium: (medium === "air" ? "air" : "vacuum") as "air" | "vacuum",
        },
        { signal },
      ),
    enabled: isEnabled,
    staleTime: 60_000,
  });

  const data = query.data;
  const boltzmann = data?.boltzmann;
  const stark = data?.stark;
  const mcwhirter = data?.mcwhirter;
  const summary = data?.summary;

  const chartTraces = useMemo<Data[]>(() => {
    if (!boltzmann || !boltzmann.points || boltzmann.points.length === 0) {
      return [];
    }

    const points = boltzmann.points;
    const xPoints = points.map((p) => p.ek_ev);
    const yPoints = points.map((p) => p.y_value);
    const hoverText = points.map(
      (p) =>
        `λ: ${p.wavelength_nm.toFixed(2)} нм<br>` +
        `E<sub>k</sub>: ${p.ek_ev.toFixed(2)} эВ (E<sub>i</sub>: ${p.ei_ev.toFixed(2)} эВ)<br>` +
        `g<sub>k</sub>: ${p.gk}, A<sub>ki</sub>: ${p.aki.toExponential(2)} с⁻¹<br>` +
        `ln(Iλ/gA): ${p.y_value.toFixed(2)}<br>` +
        `Невязка: ${p.residual >= 0 ? "+" : ""}${p.residual.toFixed(2)}`,
    );

    const scatterTrace: Data = {
      x: xPoints,
      y: yPoints,
      text: hoverText,
      hoverinfo: "text",
      mode: "markers",
      type: "scatter",
      name: `Линии ${boltzmann.element} ${boltzmann.ion_stage === 1 ? "I" : "II"}`,
      marker: {
        color: chartColors.observed,
        size: 9,
        line: { color: "#ffffff", width: 1.5 },
      },
    };

    // Regression line
    const minX = Math.min(...xPoints) - 0.2;
    const maxX = Math.max(...xPoints) + 0.2;
    const lineX = [minX, maxX];
    const lineY = lineX.map((x) => boltzmann.slope * x + boltzmann.intercept);

    const lineTrace: Data = {
      x: lineX,
      y: lineY,
      mode: "lines",
      type: "scatter",
      name: `Регрессия (T = ${Math.round(boltzmann.temperature_k)} K, R² = ${boltzmann.r_squared.toFixed(3)})`,
      line: {
        color: chartColors.reference,
        width: 2.2,
        dash: "solid",
      },
      hoverinfo: "name",
    };

    return [lineTrace, scatterTrace];
  }, [boltzmann]);

  const chartLayout = useMemo<Partial<Layout>>(() => {
    const base = photonChartLayout({});
    return {
      ...base,
      title: undefined,
      xaxis: {
        ...base.xaxis,
        title: { text: "Энергия верхнего уровня E<sub>k</sub> (эВ)" },
      },
      yaxis: {
        ...base.yaxis,
        title: { text: "ln( I<sub>ki</sub> · λ<sub>ki</sub> / g<sub>k</sub> A<sub>ki</sub> )" },
      },
      showlegend: true,
      legend: {
        orientation: "h",
        y: 1.15,
        x: 0,
        font: { color: "#a7b8c2", size: 11 },
      },
      margin: { t: 36, r: 16, b: 45, l: 56 },
    };
  }, []);

  return (
    <section className="card card-stacked" data-testid="plasma-diagnostics-card">
      <header className="card-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.75rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <Icon name="flame" className="icon-accent" size={22} />
          <div>
            <h3 style={{ margin: 0, fontSize: "1.1rem" }}>Параметры плазмы и LTE</h3>
            <span className="muted" style={{ fontSize: "0.82rem" }}>
              График Больцмана (T<sub>e</sub>), штарковская плотность (n<sub>e</sub>) и критерий МакВиртера
            </span>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
            <label htmlFor="plasma-fwhm-select" style={{ fontSize: "0.82rem", color: "var(--muted)" }}>
              FWHM прибора:
            </label>
            <select
              id="plasma-fwhm-select"
              className="button button-small"
              value={fwhmNm}
              onChange={(e) => setFwhmNm(Number(e.target.value))}
              style={{ padding: "2px 8px", fontSize: "0.82rem" }}
            >
              <option value={0.05}>0.05 нм</option>
              <option value={0.10}>0.10 нм</option>
              <option value={0.20}>0.20 нм</option>
            </select>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
            <label style={{ fontSize: "0.82rem", color: "var(--muted)" }}>Элемент:</label>
            <div className="segmented-control" role="group" aria-label="Выбор элемента">
              {["auto", "Fe", "Ti", "Mg", "Ca"].map((el) => (
                <button
                  key={el}
                  type="button"
                  className={`button button-small ${selectedElement === el ? "button-active" : ""}`}
                  onClick={() => setSelectedElement(el)}
                >
                  {el === "auto" ? "Авто" : `${el} I`}
                </button>
              ))}
            </div>
          </div>
        </div>
      </header>

      {query.isPending && (
        <div style={{ padding: "2rem 0", display: "flex", justifyContent: "center" }}>
          <Loading>Расчёт параметров плазмы...</Loading>
        </div>
      )}

      {query.isError && (
        <div className="notice notice-error" data-testid="plasma-error" style={{ margin: "1rem 0" }}>
          <span>Не удалось выполнить расчёт физических параметров плазмы.</span>
        </div>
      )}

      {query.isSuccess && summary && (
        <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem", marginTop: "0.5rem" }}>
          {/* Key Metrics Row */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: "0.75rem",
            }}
          >
            {/* Te Card */}
            <div className="metric-box">
              <span className="metric-label">Температура T<sub>e</sub> (Больцман)</span>
              {summary.diagnostics_ready && boltzmann && boltzmann.temperature_k > 0 ? (
                <>
                  <div className="metric-value">
                    {Math.round(boltzmann.temperature_k).toLocaleString("ru-RU")}{" "}
                    <span style={{ fontSize: "0.9rem", color: "var(--muted)" }}>
                      ± {Math.round(boltzmann.temperature_uncertainty_k).toLocaleString("ru-RU")} K
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: "0.35rem", marginTop: "0.35rem", flexWrap: "wrap" }}>
                    <span className={`badge ${boltzmann.status === "valid" ? "badge-success" : "badge-warning"}`}>
                      {boltzmann.status === "valid" ? "LTE оценка" : "Слабая сходимость"}
                    </span>
                    <span className="badge badge-neutral">R² = {boltzmann.r_squared.toFixed(3)}</span>
                    <span className="badge badge-neutral">{boltzmann.points.length} линий ({boltzmann.element})</span>
                  </div>
                </>
              ) : (
                <>
                  <div className="metric-value" style={{ color: "var(--muted)" }}>—</div>
                  <span className="muted" style={{ fontSize: "0.8rem" }}>
                    {boltzmann?.status_message || "Недостаточно неперекрытых линий"}
                  </span>
                </>
              )}
            </div>

            {/* ne Card */}
            <div className="metric-box">
              <span className="metric-label">Плотность электронов n<sub>e</sub></span>
              {stark && stark.status === "detected" && stark.electron_density_cm3 ? (
                <>
                  <div className="metric-value">
                    {stark.electron_density_cm3.toExponential(2)}{" "}
                    <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>см⁻³</span>
                  </div>
                  <div style={{ display: "flex", gap: "0.35rem", marginTop: "0.35rem" }}>
                    <span className="badge badge-success">Штарк H<sub>α</sub></span>
                    <span className="badge badge-neutral">w = {stark.stark_fwhm_nm.toFixed(2)} нм</span>
                  </div>
                </>
              ) : (
                <>
                  <div className="metric-value" style={{ color: "var(--muted)" }}>—</div>
                  <span className="muted" style={{ fontSize: "0.8rem" }}>
                    {stark?.status_message || "H-alpha вне диапазона спектрометра"}
                  </span>
                </>
              )}
            </div>

            {/* McWhirter Criterion Card */}
            <div className="metric-box">
              <span className="metric-label">Критерий МакВиртера (LTE)</span>
              {mcwhirter && mcwhirter.min_electron_density_cm3 > 0 ? (
                <>
                  <div className="metric-value" style={{ fontSize: "1.1rem" }}>
                    {mcwhirter.criterion_satisfied === true ? (
                      <span style={{ color: "#34d399" }}>Выполнен</span>
                    ) : mcwhirter.criterion_satisfied === false ? (
                      <span style={{ color: "#f87171" }}>Не выполнен</span>
                    ) : (
                      <span style={{ color: "var(--muted)" }}>Требует n<sub>e</sub></span>
                    )}
                  </div>
                  <span className="muted" style={{ fontSize: "0.8rem" }}>
                    Мин. n<sub>e</sub>: {mcwhirter.min_electron_density_cm3.toExponential(2)} см⁻³
                  </span>
                </>
              ) : (
                <>
                  <div className="metric-value" style={{ color: "var(--muted)" }}>—</div>
                  <span className="muted" style={{ fontSize: "0.8rem" }}>Необходима температура Te</span>
                </>
              )}
            </div>
          </div>

          {/* Boltzmann Chart */}
          {boltzmann && boltzmann.points && boltzmann.points.length > 0 && (
            <div style={{ border: "1px solid var(--border-subtle)", borderRadius: "var(--radius-md)", padding: "0.75rem", background: "var(--surface-sunken)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>
                  График Больцмана для {boltzmann.element} I
                </span>
                <button
                  type="button"
                  className="button button-small button-ghost"
                  onClick={() => setShowTable(!showTable)}
                >
                  <Icon name={showTable ? "chevronDown" : "sliders"} size={14} />
                  {showTable ? "Скрыть таблицу линий" : `Показать линии (${boltzmann.points.length})`}
                </button>
              </div>

              <div style={{ minHeight: "260px" }}>
                <Chart data={chartTraces} layout={chartLayout} label="График Больцмана" />
              </div>

              {/* Table of Points */}
              {showTable && (
                <div style={{ marginTop: "1rem", maxHeight: "220px", overflowY: "auto" }}>
                  <table className="table" style={{ fontSize: "0.82rem" }}>
                    <thead>
                      <tr>
                        <th>λ (нм)</th>
                        <th>E<sub>k</sub> (эВ)</th>
                        <th>E<sub>i</sub> (эВ)</th>
                        <th>g<sub>k</sub></th>
                        <th>A<sub>ki</sub> (с⁻¹)</th>
                        <th>I<sub>net</sub></th>
                        <th>Невязка</th>
                      </tr>
                    </thead>
                    <tbody>
                      {boltzmann.points.map((p, idx) => (
                        <tr key={idx}>
                          <td>{p.wavelength_nm.toFixed(2)}</td>
                          <td>{p.ek_ev.toFixed(2)}</td>
                          <td>{p.ei_ev.toFixed(2)}</td>
                          <td>{p.gk}</td>
                          <td>{p.aki.toExponential(2)}</td>
                          <td>{p.net_intensity.toExponential(2)}</td>
                          <td style={{ color: Math.abs(p.residual) > 0.5 ? "var(--warning)" : "inherit" }}>
                            {p.residual >= 0 ? "+" : ""}{p.residual.toFixed(2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
