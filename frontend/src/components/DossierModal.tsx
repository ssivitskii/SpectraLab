import { useEffect, useState } from "react";
import {
  api,
  type MeasurementDossierResponse,
} from "../api/client";
import { Loading } from "./Feedback";
import { Icon } from "./Icon";

export function DossierModal({
  isOpen,
  onClose,
  spectrumId,
  wavelengthNm,
  intensity,
  transferProfileId = "identity",
  plasmaElement = "auto",
  confidenceLevel = 0.95,
  medium = "vacuum",
}: {
  isOpen: boolean;
  onClose: () => void;
  spectrumId?: string;
  wavelengthNm?: number[];
  intensity?: number[];
  transferProfileId?: string;
  plasmaElement?: string;
  confidenceLevel?: number;
  medium?: "air" | "vacuum";
}) {
  const [dossier, setDossier] = useState<MeasurementDossierResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"lines" | "assay" | "protocol">("lines");
  const [lineFilter, setLineFilter] = useState<string>("");

  useEffect(() => {
    if (!isOpen) return;

    let active = true;
    setLoading(true);
    setError(null);

    api
      .dossierGenerate({
        spectrum_id: spectrumId,
        wavelength_nm: spectrumId ? undefined : wavelengthNm,
        intensity: spectrumId ? undefined : intensity,
        transfer_profile_id: transferProfileId,
        plasma_element: plasmaElement,
        plasma_fwhm_nm: 0.10,
        confidence_level: confidenceLevel,
        medium,
      })
      .then((data) => {
        if (active) {
          setDossier(data);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (active) {
          setError(err instanceof Error ? err.message : "Не удалось сформировать досье");
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [
    isOpen,
    spectrumId,
    wavelengthNm,
    intensity,
    transferProfileId,
    plasmaElement,
    confidenceLevel,
    medium,
  ]);

  if (!isOpen) return null;

  const filteredLines = (dossier?.explainability.lines ?? []).filter((l) => {
    if (!lineFilter.trim()) return true;
    const q = lineFilter.toLowerCase();
    return (
      l.element.toLowerCase().includes(q) ||
      l.line_name.toLowerCase().includes(q) ||
      l.target_oxide.toLowerCase().includes(q)
    );
  });

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="dossier-modal-title"
      data-testid="dossier-modal"
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0, 0, 0, 0.75)",
        zIndex: 100,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "20px",
      }}
    >
      <div
        style={{
          backgroundColor: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          width: "100%",
          maxWidth: "1050px",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.5)",
          overflow: "hidden",
        }}
      >
        {/* Modal Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "16px 24px",
            borderBottom: "1px solid var(--border)",
            backgroundColor: "var(--surface-raised)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <Icon name="file" size={20} />
            <h2 id="dossier-modal-title" style={{ fontSize: "17px", fontWeight: 600 }}>
              Измерительное досье спектра
            </h2>
            {dossier && (
              <span
                style={{
                  fontSize: "12px",
                  padding: "2px 8px",
                  borderRadius: "4px",
                  backgroundColor: "var(--input)",
                  color: "var(--muted)",
                  fontFamily: "ui-monospace, monospace",
                }}
              >
                {dossier.dossier_id}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            style={{
              background: "transparent",
              border: "none",
              color: "var(--muted)",
              cursor: "pointer",
              padding: "6px",
              minHeight: "auto",
            }}
          >
            <Icon name="close" size={20} />
          </button>
        </div>

        {/* Modal Body */}
        <div style={{ padding: "24px", overflowY: "auto", flex: 1 }}>
          {loading && <Loading>Формирование комплексного досье и верификация линий NIST...</Loading>}

          {error && (
            <div
              style={{
                padding: "16px",
                borderRadius: "var(--radius-control)",
                backgroundColor: "var(--danger-soft)",
                color: "var(--danger)",
                border: "1px solid var(--danger)",
              }}
            >
              {error}
            </div>
          )}

          {dossier && !loading && (
            <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
              {/* Executive Summary & Badges */}
              <div
                style={{
                  padding: "16px",
                  backgroundColor: "var(--accent-soft)",
                  border: "1px solid var(--accent)",
                  borderRadius: "var(--radius-control)",
                  color: "var(--ink)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "12px",
                }}
              >
                <div style={{ fontSize: "14px", lineHeight: "1.5" }}>
                  {dossier.executive_summary}
                </div>

                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: "10px",
                    paddingTop: "6px",
                    borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                  }}
                >
                  <div
                    style={{
                      background: "var(--surface)",
                      padding: "6px 12px",
                      borderRadius: "6px",
                      fontSize: "12px",
                    }}
                  >
                    <span style={{ color: "var(--muted)" }}>Чистота спектра: </span>
                    <strong style={{ color: "var(--accent)" }}>
                      {(dossier.explainability.spectral_purity_score * 100).toFixed(1)}%
                    </strong>
                  </div>

                  <div
                    style={{
                      background: "var(--surface)",
                      padding: "6px 12px",
                      borderRadius: "6px",
                      fontSize: "12px",
                    }}
                  >
                    <span style={{ color: "var(--muted)" }}>Температура плазмы: </span>
                    <strong style={{ color: "var(--ink)" }}>
                      {dossier.plasma?.temperature_k ? `${dossier.plasma.temperature_k.toFixed(0)} K` : "N/A"}
                    </strong>
                  </div>

                  <div
                    style={{
                      background: "var(--surface)",
                      padding: "6px 12px",
                      borderRadius: "6px",
                      fontSize: "12px",
                    }}
                  >
                    <span style={{ color: "var(--muted)" }}>LTE Критерий: </span>
                    <strong
                      style={{
                        color: dossier.plasma?.lte_valid ? "var(--success)" : "var(--warning)",
                      }}
                    >
                      {dossier.plasma?.lte_valid ? "Соблюден" : "Не соблюден"}
                    </strong>
                  </div>

                  <div
                    style={{
                      background: "var(--surface)",
                      padding: "6px 12px",
                      borderRadius: "6px",
                      fontSize: "12px",
                    }}
                  >
                    <span style={{ color: "var(--muted)" }}>Масса оксидов: </span>
                    <strong style={{ color: "var(--ink)" }}>
                      {dossier.quantitative?.total_detected_mass_wt_percent?.toFixed(2)} wt%
                    </strong>
                  </div>

                  <div
                    style={{
                      background: "var(--surface)",
                      padding: "6px 12px",
                      borderRadius: "6px",
                      fontSize: "12px",
                      fontFamily: "ui-monospace, monospace",
                    }}
                  >
                    <span style={{ color: "var(--muted)" }}>SHA-256: </span>
                    <span>{dossier.sha256.slice(0, 16)}...</span>
                  </div>
                </div>
              </div>

              {/* Action Toolbar */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  flexWrap: "wrap",
                  gap: "10px",
                }}
              >
                {/* Navigation Tabs */}
                <div style={{ display: "flex", gap: "6px" }}>
                  <button
                    type="button"
                    onClick={() => setActiveTab("lines")}
                    className={activeTab === "lines" ? "primary" : ""}
                    style={{ minHeight: "36px", padding: "6px 14px", fontSize: "13px" }}
                  >
                    Линии NIST ({dossier.explainability.lines.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveTab("assay")}
                    className={activeTab === "assay" ? "primary" : ""}
                    style={{ minHeight: "36px", padding: "6px 14px", fontSize: "13px" }}
                  >
                    Оксидный состав (Assay)
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveTab("protocol")}
                    className={activeTab === "protocol" ? "primary" : ""}
                    style={{ minHeight: "36px", padding: "6px 14px", fontSize: "13px" }}
                  >
                    Протокол (Markdown)
                  </button>
                </div>

                {/* Export / Download Buttons */}
                <div style={{ display: "flex", gap: "8px" }}>
                  <a
                    href={api.dossierDownloadUrl(dossier.dossier_id, "html")}
                    target="_blank"
                    rel="noreferrer"
                    className="button"
                    style={{ minHeight: "36px", padding: "6px 12px", fontSize: "13px" }}
                  >
                    <Icon name="file" size={16} />
                    Печать / HTML
                  </a>
                  <a
                    href={api.dossierDownloadUrl(dossier.dossier_id, "csv")}
                    download={`${dossier.dossier_id}_lines.csv`}
                    className="button"
                    style={{ minHeight: "36px", padding: "6px 12px", fontSize: "13px" }}
                  >
                    <Icon name="download" size={16} />
                    Линии CSV
                  </a>
                  <a
                    href={api.dossierDownloadUrl(dossier.dossier_id, "json")}
                    download={`${dossier.dossier_id}.json`}
                    className="button"
                    style={{ minHeight: "36px", padding: "6px 12px", fontSize: "13px" }}
                  >
                    <Icon name="download" size={16} />
                    JSON
                  </a>
                </div>
              </div>

              {/* Tab: Lines NIST */}
              {activeTab === "lines" && (
                <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                  <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                    <input
                      type="text"
                      placeholder="Фильтр по элементу, оксиду или длине волны..."
                      value={lineFilter}
                      onChange={(e) => setLineFilter(e.target.value)}
                      style={{
                        padding: "8px 12px",
                        borderRadius: "var(--radius-control)",
                        border: "1px solid var(--border)",
                        background: "var(--input)",
                        color: "var(--ink)",
                        flex: 1,
                        fontSize: "13px",
                      }}
                    />
                    <span style={{ fontSize: "12px", color: "var(--muted)" }}>
                      Показано: {filteredLines.length}
                    </span>
                  </div>

                  <div
                    style={{
                      maxHeight: "380px",
                      overflowY: "auto",
                      border: "1px solid var(--border)",
                      borderRadius: "var(--radius-control)",
                    }}
                  >
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                      <thead>
                        <tr style={{ background: "var(--surface-raised)", position: "sticky", top: 0, zIndex: 1 }}>
                          <th style={{ padding: "8px 12px", textAlign: "left" }}>Линия</th>
                          <th style={{ padding: "8px 12px", textAlign: "right" }}>λ vac (нм)</th>
                          <th style={{ padding: "8px 12px", textAlign: "right" }}>λ meas (нм)</th>
                          <th style={{ padding: "8px 12px", textAlign: "right" }}>Пик</th>
                          <th style={{ padding: "8px 12px", textAlign: "right" }}>Фон</th>
                          <th style={{ padding: "8px 12px", textAlign: "right" }}>Net</th>
                          <th style={{ padding: "8px 12px", textAlign: "right" }}>SBR</th>
                          <th style={{ padding: "8px 12px", textAlign: "center" }}>Статус</th>
                          <th style={{ padding: "8px 12px", textAlign: "left" }}>Наложение</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredLines.map((line) => (
                          <tr
                            key={line.line_name}
                            style={{
                              borderTop: "1px solid var(--border)",
                              backgroundColor: line.detected ? "rgba(103, 213, 219, 0.05)" : "transparent",
                            }}
                          >
                            <td style={{ padding: "8px 12px", fontWeight: 600 }}>{line.line_name}</td>
                            <td style={{ padding: "8px 12px", textAlign: "right" }}>
                              {line.wavelength_vacuum_nm.toFixed(3)}
                            </td>
                            <td style={{ padding: "8px 12px", textAlign: "right" }}>
                              {line.measured_wavelength_nm.toFixed(3)}
                            </td>
                            <td style={{ padding: "8px 12px", textAlign: "right" }}>
                              {line.peak_intensity.toFixed(1)}
                            </td>
                            <td style={{ padding: "8px 12px", textAlign: "right", color: "var(--muted)" }}>
                              {line.background_intensity.toFixed(1)}
                            </td>
                            <td style={{ padding: "8px 12px", textAlign: "right", fontWeight: 600 }}>
                              {line.net_intensity.toFixed(1)}
                            </td>
                            <td style={{ padding: "8px 12px", textAlign: "right", color: line.signal_to_background_ratio >= 2.0 ? "var(--success)" : "var(--ink)" }}>
                              {line.signal_to_background_ratio.toFixed(2)}
                            </td>
                            <td style={{ padding: "8px 12px", textAlign: "center" }}>
                              <span
                                style={{
                                  fontSize: "11px",
                                  padding: "2px 6px",
                                  borderRadius: "4px",
                                  backgroundColor: line.detected ? "var(--success-soft)" : "var(--surface-hover)",
                                  color: line.detected ? "var(--success)" : "var(--muted)",
                                }}
                              >
                                {line.detected ? "Найдена" : "—"}
                              </span>
                            </td>
                            <td style={{ padding: "8px 12px", fontSize: "12px" }}>
                              {line.is_blend ? (
                                <span style={{ color: "var(--danger)" }}>
                                  Бленд ({line.interfering_elements.join(", ")})
                                </span>
                              ) : (
                                <span style={{ color: "var(--muted)" }}>Чистая</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Tab: Assay */}
              {activeTab === "assay" && (
                <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                  <div
                    style={{
                      border: "1px solid var(--border)",
                      borderRadius: "var(--radius-control)",
                      overflow: "hidden",
                    }}
                  >
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                      <thead>
                        <tr style={{ background: "var(--surface-raised)" }}>
                          <th style={{ padding: "8px 12px", textAlign: "left" }}>Оксид</th>
                          <th style={{ padding: "8px 12px", textAlign: "right" }}>Массовая доля (wt%)</th>
                          <th style={{ padding: "8px 12px", textAlign: "center" }}>Доверительный интервал</th>
                          <th style={{ padding: "8px 12px", textAlign: "center" }}>Область применимости</th>
                          <th style={{ padding: "8px 12px", textAlign: "right" }}>Leverage h(x)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(dossier.quantitative?.predictions ?? []).map((pred) => (
                          <tr key={pred.species} style={{ borderTop: "1px solid var(--border)" }}>
                            <td style={{ padding: "8px 12px", fontWeight: 600 }}>{pred.species}</td>
                            <td style={{ padding: "8px 12px", textAlign: "right", fontWeight: 600 }}>
                              {pred.value_wt_percent.toFixed(3)}
                            </td>
                            <td style={{ padding: "8px 12px", textAlign: "center", color: "var(--muted)" }}>
                              [{pred.lower_wt_percent.toFixed(3)} – {pred.upper_wt_percent.toFixed(3)}]
                            </td>
                            <td style={{ padding: "8px 12px", textAlign: "center" }}>
                              <span
                                style={{
                                  fontSize: "11px",
                                  padding: "2px 6px",
                                  borderRadius: "4px",
                                  backgroundColor:
                                    pred.status === "in_domain"
                                      ? "var(--success-soft)"
                                      : pred.status === "near_boundary"
                                        ? "var(--warning-soft)"
                                        : "var(--danger-soft)",
                                  color:
                                    pred.status === "in_domain"
                                      ? "var(--success)"
                                      : pred.status === "near_boundary"
                                        ? "var(--warning)"
                                        : "var(--danger)",
                                }}
                              >
                                {pred.status}
                              </span>
                            </td>
                            <td style={{ padding: "8px 12px", textAlign: "right" }}>
                              {pred.leverage.toFixed(2)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Tab: Protocol Markdown */}
              {activeTab === "protocol" && (
                <div
                  style={{
                    backgroundColor: "var(--input)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-control)",
                    padding: "16px",
                    maxHeight: "400px",
                    overflowY: "auto",
                  }}
                >
                  <pre
                    style={{
                      margin: 0,
                      padding: 0,
                      background: "transparent",
                      color: "var(--ink)",
                      fontSize: "12px",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {dossier.markdown_report}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
