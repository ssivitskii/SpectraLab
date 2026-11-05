import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type BatchAssayResponse } from "../api/client";
import { Loading } from "./Feedback";
import { Icon } from "./Icon";

export function BatchAssayCard({ batchId }: { batchId: string }) {
  const [confidenceLevel, setConfidenceLevel] = useState<number>(0.95);

  const assayQuery = useQuery<BatchAssayResponse>({
    queryKey: ["batchAssayMatrix", batchId, confidenceLevel],
    queryFn: ({ signal }) =>
      api.batchAssayMatrix(batchId, confidenceLevel, { signal }),
    enabled: Boolean(batchId),
    staleTime: 60_000,
  });

  const matrix = assayQuery.data;

  return (
    <div
      className="panel"
      data-testid="batch-assay-card"
      style={{
        marginTop: "20px",
        padding: "20px",
        backgroundColor: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: "12px",
          marginBottom: "16px",
          borderBottom: "1px solid var(--border)",
          paddingBottom: "12px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <Icon name="flask" size={20} />
          <div>
            <h2 style={{ fontSize: "16px", fontWeight: 600 }}>
              Количественный анализ оксидов и матричный аудит применимости (Assay Matrix)
            </h2>
            <p className="muted" style={{ margin: 0, fontSize: "12px" }}>
              Конформная Ridge-оценка оксидного состава с хемометрическим контролем leverage h(x)
            </p>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <label style={{ fontSize: "13px", color: "var(--muted)" }}>
            Доверие:
            <select
              aria-label="Уровень доверия конформного интервала"
              value={confidenceLevel}
              onChange={(e) => setConfidenceLevel(Number(e.target.value))}
              style={{
                marginLeft: "6px",
                padding: "4px 8px",
                borderRadius: "var(--radius-control)",
                background: "var(--input)",
                color: "var(--ink)",
                border: "1px solid var(--border)",
              }}
            >
              <option value={0.9}>90%</option>
              <option value={0.95}>95%</option>
              <option value={0.99}>99%</option>
            </select>
          </label>

          <a
            href={api.batchAssayMatrixDownloadUrl(batchId, confidenceLevel)}
            download={`batch_${batchId}_assay_matrix.csv`}
            className="button"
            style={{ minHeight: "36px", padding: "6px 12px", fontSize: "13px" }}
          >
            <Icon name="download" size={16} />
            Экспорт матрицы (CSV)
          </a>
        </div>
      </div>

      {assayQuery.isPending && (
        <Loading>Вычисление количественных содержаний и конформных интервалов...</Loading>
      )}

      {assayQuery.error && (
        <div
          style={{
            padding: "12px",
            backgroundColor: "var(--danger-soft)",
            color: "var(--danger)",
            borderRadius: "var(--radius-control)",
            fontSize: "13px",
          }}
        >
          {assayQuery.error instanceof Error
            ? assayQuery.error.message
            : "Ошибка загрузки матрицы оксидов"}
        </div>
      )}

      {matrix && (
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {/* Summary KPIs */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "12px",
            }}
          >
            <div
              style={{
                padding: "12px",
                borderRadius: "var(--radius-control)",
                background: "var(--surface-raised)",
                border: "1px solid var(--border)",
              }}
            >
              <div style={{ fontSize: "12px", color: "var(--muted)" }}>Образцы в пакете</div>
              <div style={{ fontSize: "20px", fontWeight: 700, marginTop: "4px" }}>
                {matrix.summary.total_samples}
              </div>
              <div style={{ fontSize: "11px", color: "var(--muted)", marginTop: "2px" }}>
                In-domain:{" "}
                <strong style={{ color: "var(--success)" }}>
                  {matrix.summary.in_domain_count}
                </strong>
                {matrix.summary.out_of_domain_count > 0 && (
                  <>
                    {" "}
                    | OOD:{" "}
                    <strong style={{ color: "var(--danger)" }}>
                      {matrix.summary.out_of_domain_count}
                    </strong>
                  </>
                )}
              </div>
            </div>

            <div
              style={{
                padding: "12px",
                borderRadius: "var(--radius-control)",
                background: "var(--surface-raised)",
                border: "1px solid var(--border)",
              }}
            >
              <div style={{ fontSize: "12px", color: "var(--muted)" }}>Средняя сумма оксидов</div>
              <div style={{ fontSize: "20px", fontWeight: 700, marginTop: "4px" }}>
                {matrix.summary.mean_total_mass_wt_percent.toFixed(2)} wt%
              </div>
              <div style={{ fontSize: "11px", color: "var(--muted)", marginTop: "2px" }}>
                Доверие: {(matrix.summary.confidence_level * 100).toFixed(0)}%
              </div>
            </div>

            {Object.entries(matrix.summary.mean_concentrations).map(([oxide, meanVal]) => (
              <div
                key={oxide}
                style={{
                  padding: "12px",
                  borderRadius: "var(--radius-control)",
                  background: "var(--surface-raised)",
                  border: "1px solid var(--border)",
                }}
              >
                <div style={{ fontSize: "12px", color: "var(--muted)" }}>{oxide} (среднее)</div>
                <div style={{ fontSize: "18px", fontWeight: 600, marginTop: "4px" }}>
                  {meanVal.toFixed(2)} wt%
                </div>
              </div>
            ))}
          </div>

          {/* Matrix Samples Table */}
          <div
            style={{
              maxHeight: "420px",
              overflowX: "auto",
              overflowY: "auto",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-control)",
            }}
          >
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
              <thead>
                <tr
                  style={{
                    background: "var(--surface-raised)",
                    position: "sticky",
                    top: 0,
                    zIndex: 1,
                  }}
                >
                  <th style={{ padding: "8px 12px", textAlign: "left" }}>Файл / Образец</th>
                  <th style={{ padding: "8px 12px", textAlign: "center" }}>Область применимости</th>
                  <th style={{ padding: "8px 12px", textAlign: "right" }}>Max Leverage</th>
                  <th style={{ padding: "8px 12px", textAlign: "right" }}>FeOT (wt%)</th>
                  <th style={{ padding: "8px 12px", textAlign: "right" }}>MgO (wt%)</th>
                  <th style={{ padding: "8px 12px", textAlign: "right" }}>CaO (wt%)</th>
                  <th style={{ padding: "8px 12px", textAlign: "right" }}>TiO2 (wt%)</th>
                  <th style={{ padding: "8px 12px", textAlign: "right" }}>SiO2 (wt%)</th>
                  <th style={{ padding: "8px 12px", textAlign: "right" }}>Al2O3 (wt%)</th>
                  <th style={{ padding: "8px 12px", textAlign: "right" }}>Сумма (wt%)</th>
                </tr>
              </thead>
              <tbody>
                {matrix.samples.map((s) => {
                  const stColor =
                    s.applicability_status === "in_domain"
                      ? "var(--success)"
                      : s.applicability_status === "near_boundary"
                        ? "var(--warning)"
                        : "var(--danger)";
                  const stBg =
                    s.applicability_status === "in_domain"
                      ? "var(--success-soft)"
                      : s.applicability_status === "near_boundary"
                        ? "var(--warning-soft)"
                        : "var(--danger-soft)";

                  const getOx = (ox: string) => {
                    const p = s.predictions[ox];
                    return p ? `${p.value_wt_percent.toFixed(2)}` : "—";
                  };

                  return (
                    <tr
                      key={s.file_id}
                      style={{ borderTop: "1px solid var(--border)" }}
                    >
                      <td style={{ padding: "8px 12px" }}>
                        <div style={{ fontWeight: 600 }}>{s.relative_path}</div>
                        {s.sample_id && (
                          <div style={{ fontSize: "11px", color: "var(--muted)" }}>
                            ID: {s.sample_id}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "center" }}>
                        <span
                          style={{
                            fontSize: "11px",
                            padding: "2px 8px",
                            borderRadius: "4px",
                            backgroundColor: stBg,
                            color: stColor,
                            fontWeight: 600,
                          }}
                        >
                          {s.applicability_status}
                        </span>
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "right" }}>
                        {s.max_leverage.toFixed(2)}
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "right" }}>{getOx("FeOT")}</td>
                      <td style={{ padding: "8px 12px", textAlign: "right" }}>{getOx("MgO")}</td>
                      <td style={{ padding: "8px 12px", textAlign: "right" }}>{getOx("CaO")}</td>
                      <td style={{ padding: "8px 12px", textAlign: "right" }}>{getOx("TiO2")}</td>
                      <td style={{ padding: "8px 12px", textAlign: "right" }}>{getOx("SiO2")}</td>
                      <td style={{ padding: "8px 12px", textAlign: "right" }}>{getOx("Al2O3")}</td>
                      <td style={{ padding: "8px 12px", textAlign: "right", fontWeight: 600 }}>
                        {s.total_mass_wt_percent.toFixed(2)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

