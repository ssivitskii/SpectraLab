import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart } from "./Chart";
import { chartColors } from "./chartTheme";
import { Icon } from "./Icon";
import type { SeriesAggregatePoint, SeriesShotDiagnostic } from "../api/client";

export function SeriesChart({
  aggregate,
  shots,
  previewShots,
  previewWavelengths,
  activeShotNumber,
  units = "nm",
}: {
  aggregate?: SeriesAggregatePoint[] | null;
  shots?: SeriesShotDiagnostic[];
  previewShots?: { shotNumber: number; values: number[] }[];
  previewWavelengths?: number[];
  activeShotNumber?: number | null;
  units?: string;
}) {
  const [showMadBand, setShowMadBand] = useState(true);
  const [showRawShots, setShowRawShots] = useState(true);

  const plotData = useMemo<Data[]>(() => {
    const traces: Data[] = [];

    // 1. Individual shot traces (if raw data available)
    if (showRawShots && previewShots && previewWavelengths && previewWavelengths.length > 0) {
      const diagMap = new Map<number, SeriesShotDiagnostic>();
      if (shots) {
        for (const s of shots) {
          diagMap.set(s.shot_number, s);
        }
      }

      for (const shot of previewShots) {
        const diag = diagMap.get(shot.shotNumber);
        const isRejected = diag?.status === "rejected_quality";
        const isActive = activeShotNumber === shot.shotNumber;

        let lineColor = isRejected ? "rgba(242, 165, 165, 0.35)" : "rgba(167, 184, 194, 0.2)";
        let lineWidth = 1;

        if (isActive) {
          lineColor = isRejected ? "#f2a5a5" : "#67d5db";
          lineWidth = 2.5;
        }

        traces.push({
          x: previewWavelengths,
          y: shot.values,
          type: "scatter",
          mode: "lines",
          name: `Выстрел #${shot.shotNumber} (${isRejected ? "отклонён" : "принят"})`,
          line: {
            color: lineColor,
            width: lineWidth,
            dash: isRejected ? "dot" : "solid",
          },
          hoverinfo: "name+x+y",
          showlegend: isActive,
        });
      }
    }

    // 2. MAD Dispersion band (filled area around median)
    if (showMadBand && aggregate && aggregate.length > 0) {
      const wave = aggregate.map((p) => p.wavelength_nm);
      const upper = aggregate.map((p) => p.intensity + p.intensity_mad);
      const lower = aggregate.map((p) => Math.max(0, p.intensity - p.intensity_mad));

      traces.push({
        x: wave,
        y: upper,
        type: "scatter",
        mode: "lines",
        line: { color: "transparent", width: 0 },
        showlegend: false,
        hoverinfo: "skip",
      });

      traces.push({
        x: wave,
        y: lower,
        type: "scatter",
        mode: "lines",
        fill: "tonexty",
        fillcolor: "rgba(103, 213, 219, 0.18)",
        line: { color: "transparent", width: 0 },
        name: "Коридор ±1 MAD",
        hoverinfo: "skip",
      });
    }

    // 3. Robust Pointwise Median Aggregate
    if (aggregate && aggregate.length > 0) {
      traces.push({
        x: aggregate.map((p) => p.wavelength_nm),
        y: aggregate.map((p) => p.intensity),
        type: "scatter",
        mode: "lines",
        name: "Медианный агрегированный спектр",
        line: {
          color: chartColors.observed,
          width: 2.5,
        },
        hoverinfo: "name+x+y",
      });
    }

    return traces;
  }, [aggregate, shots, previewShots, previewWavelengths, showMadBand, showRawShots, activeShotNumber]);

  return (
    <div className="series-chart-wrapper">
      <div className="chart-toolbar" style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "8px", flexWrap: "wrap" }}>
        {aggregate && (
          <button
            type="button"
            className={`pill ${showMadBand ? "pill-active" : ""}`}
            onClick={() => setShowMadBand(!showMadBand)}
            aria-pressed={showMadBand}
          >
            <Icon name="activity" size={14} />
            <span>Коридор дисперсии (±1 MAD)</span>
          </button>
        )}
        {previewShots && previewShots.length > 0 && (
          <button
            type="button"
            className={`pill ${showRawShots ? "pill-active" : ""}`}
            onClick={() => setShowRawShots(!showRawShots)}
            aria-pressed={showRawShots}
          >
            <Icon name="layers" size={14} />
            <span>Все выстрелы ({previewShots.length})</span>
          </button>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: "8px", alignItems: "center", fontSize: "12px", color: "var(--muted)" }}>
          <span className="legend-sample" style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
            <span style={{ width: 12, height: 3, background: chartColors.observed, borderRadius: 2 }} />
            Медиана
          </span>
          {previewShots && (
            <>
              <span className="legend-sample" style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                <span style={{ width: 12, height: 2, background: "rgba(167, 184, 194, 0.6)", borderRadius: 1 }} />
                Принятые
              </span>
              <span className="legend-sample" style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                <span style={{ width: 12, height: 2, borderBottom: "2px dotted #f2a5a5" }} />
                Отклонённые
              </span>
            </>
          )}
        </div>
      </div>
      <Chart
        data={plotData}
        label="Спектральный график серии выстрелов"
        layout={{
          xaxis: { title: { text: `Длина волны (${units})` } },
          yaxis: { title: { text: "Интенсивность" } },
        }}
      />
    </div>
  );
}

