import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Data, Layout } from "plotly.js";
import { api, type ApplicabilitySpaceResponse } from "../api/client";
import { Chart } from "./Chart";
import { photonChartLayout } from "./chartTheme";
import { Icon } from "./Icon";
import { Loading } from "./Feedback";

function getStatusBadge(status: ApplicabilitySpaceResponse["status"]) {
  switch (status) {
    case "in_domain":
      return <span className="badge badge-success">Внутри области (AD)</span>;
    case "near_boundary":
      return <span className="badge badge-warning">Граница AD (95%)</span>;
    case "out_of_domain":
      return <span className="badge badge-danger">Экстраполяция (OOD)</span>;
    default:
      return <span className="badge">{status}</span>;
  }
}

export function ApplicabilityDomainChart({
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
  const [showContributions, setShowContributions] = useState<boolean>(true);

  const isEnabled = Boolean(
    spectrumId || (wavelengthNm && intensity && wavelengthNm.length >= 10),
  );

  const query = useQuery<ApplicabilitySpaceResponse>({
    queryKey: [
      "applicabilitySpace",
      spectrumId,
      medium,
      wavelengthNm?.length,
    ],
    queryFn: ({ signal }) =>
      api.quantitativeApplicabilitySpace(
        {
          spectrum_id: spectrumId ?? null,
          wavelength_nm: spectrumId ? null : (wavelengthNm ?? null),
          intensity: spectrumId ? null : (intensity ?? null),
          medium: (medium === "air" ? "air" : "vacuum") as "air" | "vacuum",
        },
        { signal },
      ),
    enabled: isEnabled,
    staleTime: 60_000,
  });

  const data = query.data;

  const chartTraces = useMemo<Data[]>(() => {
    if (!data) return [];

    const traces: Data[] = [];

    // 1. Group calibration scores by label
    const groups: Record<string, { x: number[]; y: number[] }> = {};
    for (const item of data.calibration_scores) {
      const x = Number(item[0]);
      const y = Number(item[1]);
      const lbl = String(item[2] || "Стандарт");
      if (!groups[lbl]) {
        groups[lbl] = { x: [], y: [] };
      }
      groups[lbl].x.push(x);
      groups[lbl].y.push(y);
    }

    const groupColors: Record<string, string> = {
      Базальт: "#64748b",
      Андезит: "#0ea5e9",
      Гранит: "#f59e0b",
      Перидотит: "#10b981",
    };

    for (const [lbl, coords] of Object.entries(groups)) {
      traces.push({
        x: coords.x,
        y: coords.y,
        mode: "markers",
        type: "scatter",
        name: lbl,
        marker: {
          size: 7,
          color: groupColors[lbl] ?? "#94a3b8",
          opacity: 0.65,
        },
        hoverinfo: "text",
        hovertext: coords.x.map(
          (_, i) => `${lbl}<br>PC1: ${coords.x[i].toFixed(2)}<br>PC2: ${coords.y[i].toFixed(2)}`,
        ),
      });
    }

    // 2. Hotelling T^2 95% ellipse
    if (data.ellipse_95 && data.ellipse_95.length > 0) {
      traces.push({
        x: data.ellipse_95.map((pt) => pt[0]),
        y: data.ellipse_95.map((pt) => pt[1]),
        mode: "lines",
        type: "scatter",
        name: "Граница T² (95%)",
        line: {
          color: "#f59e0b",
          width: 1.5,
          dash: "dash",
        },
        hoverinfo: "name",
      });
    }

    // 3. Hotelling T^2 99% ellipse
    if (data.ellipse_99 && data.ellipse_99.length > 0) {
      traces.push({
        x: data.ellipse_99.map((pt) => pt[0]),
        y: data.ellipse_99.map((pt) => pt[1]),
        mode: "lines",
        type: "scatter",
        name: "Граница T² (99%)",
        line: {
          color: "#ef4444",
          width: 1.5,
          dash: "dot",
        },
        hoverinfo: "name",
      });
    }

    // 4. Current sample point
    const sampleColor =
      data.status === "in_domain"
        ? "#10b981"
        : data.status === "near_boundary"
          ? "#f59e0b"
          : "#ef4444";

    traces.push({
      x: [data.sample_pc1],
      y: [data.sample_pc2],
      mode: "markers",
      type: "scatter",
      name: "Текущий образец",
      marker: {
        size: 13,
        color: sampleColor,
        symbol: "cross",
        line: {
          color: "#000000",
          width: 1.5,
        },
      },
      hoverinfo: "text",
      hovertext: [
        `Текущий образец<br>` +
          `Статус: ${data.status}<br>` +
          `PC1: ${data.sample_pc1.toFixed(2)}<br>` +
          `PC2: ${data.sample_pc2.toFixed(2)}<br>` +
          `T²: ${data.t_squared.toFixed(2)} (порог 95%: ${data.t_squared_limit_95.toFixed(2)})<br>` +
          `Q: ${data.q_residual.toFixed(2)} (порог 95%: ${data.q_residual_limit_95.toFixed(2)})`,
      ],
    });

    return traces;
  }, [data]);

  const chartLayout = useMemo<Partial<Layout>>(() => {
    const pc1Var = data?.variance_explained[0]
      ? (data.variance_explained[0] * 100).toFixed(1)
      : "0";
    const pc2Var = data?.variance_explained[1]
      ? (data.variance_explained[1] * 100).toFixed(1)
      : "0";

    return photonChartLayout({
      title: undefined,
      xaxis: {
        title: { text: `PC1 (${pc1Var}% дисперсии)` },
      },
      yaxis: {
        title: { text: `PC2 (${pc2Var}% дисперсии)` },
      },
      legend: {
        orientation: "h",
        y: -0.22,
        x: 0.5,
        xanchor: "center",
      },
      height: 380,
      margin: { l: 50, r: 20, t: 20, b: 65 },
    });
  }, [data]);

  return (
    <div
      style={{
        marginTop: "1.25rem",
        padding: "1rem",
        borderRadius: "8px",
        border: "1px solid var(--color-border, #e5e7eb)",
        background: "var(--color-surface, #ffffff)",
      }}
      data-testid="applicability-domain-card"
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: "0.75rem",
        }}
      >
        <div>
          <h4
            style={{
              margin: 0,
              fontSize: "1rem",
              fontWeight: 600,
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
            }}
          >
            <Icon name="search" /> Пространство применимости (PCA & Hotelling T²)
          </h4>
          <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
            Многомерный хемометрический контроль экстраполяции и ортогональных Q-невязок
          </span>
        </div>

        {data && (
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            {getStatusBadge(data.status)}
          </div>
        )}
      </div>

      {query.isLoading && (
        <div style={{ padding: "2rem", textAlign: "center" }}>
          <Loading>Построение хемометрической проекции...</Loading>
        </div>
      )}

      {query.isError && (
        <div
          style={{
            padding: "0.75rem 1rem",
            background: "var(--color-danger-subtle, #fee2e2)",
            color: "var(--color-danger-text, #991b1b)",
            borderRadius: "6px",
            fontSize: "0.85rem",
          }}
        >
          Не удалось построить пространство применимости.
        </div>
      )}

      {data && !query.isLoading && (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: "0.75rem",
              marginBottom: "1rem",
            }}
          >
            <div
              style={{
                padding: "0.5rem 0.75rem",
                borderRadius: "6px",
                background: "var(--color-surface-subtle, #f9fafb)",
                border: "1px solid var(--color-border-subtle, #f3f4f6)",
                fontSize: "0.85rem",
              }}
            >
              <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>
                Hotelling T² (порог 95%)
              </div>
              <div style={{ fontWeight: 600, fontSize: "1.05rem" }}>
                {data.t_squared.toFixed(2)}{" "}
                <span style={{ fontSize: "0.8rem", fontWeight: 400, color: "var(--color-text-muted)" }}>
                  / {data.t_squared_limit_95.toFixed(2)}
                </span>
              </div>
            </div>

            <div
              style={{
                padding: "0.5rem 0.75rem",
                borderRadius: "6px",
                background: "var(--color-surface-subtle, #f9fafb)",
                border: "1px solid var(--color-border-subtle, #f3f4f6)",
                fontSize: "0.85rem",
              }}
            >
              <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>
                Q-residual (порог 95%)
              </div>
              <div style={{ fontWeight: 600, fontSize: "1.05rem" }}>
                {data.q_residual.toFixed(2)}{" "}
                <span style={{ fontSize: "0.8rem", fontWeight: 400, color: "var(--color-text-muted)" }}>
                  / {data.q_residual_limit_95.toFixed(2)}
                </span>
              </div>
            </div>

            <div
              style={{
                padding: "0.5rem 0.75rem",
                borderRadius: "6px",
                background: "var(--color-surface-subtle, #f9fafb)",
                border: "1px solid var(--color-border-subtle, #f3f4f6)",
                fontSize: "0.85rem",
              }}
            >
              <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>
                Координаты проекции
              </div>
              <div style={{ fontWeight: 600, fontSize: "0.95rem" }}>
                PC1: {data.sample_pc1.toFixed(2)}, PC2: {data.sample_pc2.toFixed(2)}
              </div>
            </div>
          </div>

          <div style={{ width: "100%", height: "380px" }}>
            <Chart data={chartTraces} layout={chartLayout} />
          </div>

          {data.top_contributions && data.top_contributions.length > 0 && (
            <div style={{ marginTop: "1rem" }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  cursor: "pointer",
                }}
                onClick={() => setShowContributions(!showContributions)}
              >
                <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                  Вклад спектральных линий в невязку (Influence decomposition)
                </span>
                <span style={{ fontSize: "0.8rem", color: "var(--color-primary, #2563eb)" }}>
                  {showContributions ? "Скрыть" : "Показать"}
                </span>
              </div>

              {showContributions && (
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                    gap: "0.5rem",
                    marginTop: "0.5rem",
                  }}
                >
                  {data.top_contributions.map((c, idx) => (
                    <div
                      key={idx}
                      style={{
                        padding: "0.5rem 0.75rem",
                        borderRadius: "6px",
                        background: "var(--color-surface-subtle, #f9fafb)",
                        border: "1px solid var(--color-border-subtle, #e5e7eb)",
                        fontSize: "0.8rem",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                      }}
                    >
                      <div>
                        <strong>{c.species}</strong> ({c.line_wl_nm.toFixed(2)} нм)
                        <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>
                          {c.deviation_direction === "high" ? "Превышение нормы" : "Ниже нормы"}
                        </div>
                      </div>
                      <span
                        style={{
                          fontWeight: 600,
                          color:
                            c.contribution_percent > 30 ? "var(--color-danger-text, #dc2626)" : "inherit",
                        }}
                      >
                        {c.contribution_percent.toFixed(1)}%
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
