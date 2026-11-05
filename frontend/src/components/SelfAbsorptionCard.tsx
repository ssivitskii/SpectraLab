import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  type DoubletAbsorptionDto,
  type SelfAbsorptionResponse,
  type CFLibsResponse,
} from "../api/client";
import { Loading } from "./Feedback";
import { Icon } from "./Icon";

function getDoubletStatusBadge(status: DoubletAbsorptionDto["status"]) {
  switch (status) {
    case "absorbed":
      return <span className="badge badge-warning">Самопоглощение (τ₀ ≥ 0.2)</span>;
    case "thin":
      return <span className="badge badge-success">Оптически тонкий</span>;
    case "not_detected":
      return <span className="badge">Не обнаружен</span>;
    case "not_covered":
      return <span className="badge">Вне диапазона</span>;
    default:
      return <span className="badge">{status}</span>;
  }
}

export function SelfAbsorptionCard({
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
  const [showDetails, setShowDetails] = useState<boolean>(false);
  const manualTempK: number | undefined = undefined;

  const isEnabled = Boolean(
    spectrumId || (wavelengthNm && intensity && wavelengthNm.length >= 10),
  );

  const saQuery = useQuery<SelfAbsorptionResponse>({
    queryKey: [
      "plasmaSelfAbsorption",
      spectrumId,
      medium,
      wavelengthNm?.length,
    ],
    queryFn: ({ signal }) =>
      api.plasmaSelfAbsorption(
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

  const cfLibsQuery = useQuery<CFLibsResponse>({
    queryKey: [
      "plasmaCFLibs",
      spectrumId,
      manualTempK,
      medium,
      wavelengthNm?.length,
    ],
    queryFn: ({ signal }) =>
      api.plasmaCFLibs(
        {
          spectrum_id: spectrumId ?? null,
          wavelength_nm: spectrumId ? null : (wavelengthNm ?? null),
          intensity: spectrumId ? null : (intensity ?? null),
          temperature_k: manualTempK ?? null,
          medium: (medium === "air" ? "air" : "vacuum") as "air" | "vacuum",
        },
        { signal },
      ),
    enabled: isEnabled,
    staleTime: 60_000,
  });

  const saData = saQuery.data;
  const cfData = cfLibsQuery.data;
  const isLoading = saQuery.isLoading || cfLibsQuery.isLoading;
  const isError = saQuery.isError || cfLibsQuery.isError;

  return (
    <div className="card" style={{ marginTop: "1rem" }} data-testid="self-absorption-card">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: "1rem",
        }}
      >
        <div>
          <h3
            style={{
              margin: 0,
              fontSize: "1.1rem",
              fontWeight: 600,
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
            }}
          >
            <Icon name="activity" /> Оптическая толщина и CF-LIBS (LTE)
          </h3>
          <p
            style={{
              margin: "0.25rem 0 0",
              fontSize: "0.85rem",
              color: "var(--color-text-muted)",
            }}
          >
            Модель Cowan-Dieke по резонансным дублетам и безкалибровочная оценка элементного состава
          </p>
        </div>

        {saData && (
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <span
              className="badge"
              style={{
                background:
                  saData.mean_self_absorption_factor < 0.85
                    ? "var(--color-warning-subtle, #fef3c7)"
                    : "var(--color-success-subtle, #d1fae5)",
                color:
                  saData.mean_self_absorption_factor < 0.85
                    ? "var(--color-warning-text, #92400e)"
                    : "var(--color-success-text, #065f46)",
              }}
            >
              Коэфф. SA: {(saData.mean_self_absorption_factor * 100).toFixed(1)}%
            </span>
          </div>
        )}
      </div>

      {isLoading && (
        <div style={{ padding: "2rem", textAlign: "center" }}>
          <Loading>Расчёт оптической толщины и CF-LIBS инверсии...</Loading>
        </div>
      )}

      {isError && (
        <div
          style={{
            padding: "1rem",
            background: "var(--color-danger-subtle, #fee2e2)",
            color: "var(--color-danger-text, #991b1b)",
            borderRadius: "6px",
            fontSize: "0.9rem",
          }}
        >
          Ошибка расчёта самопоглощения плазмы или CF-LIBS. Проверьте спектральный диапазон.
        </div>
      )}

      {saData && !isLoading && (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
              gap: "1rem",
              marginBottom: "1.25rem",
            }}
          >
            {saData.doublets.map((d) => (
              <div
                key={d.name}
                style={{
                  border: "1px solid var(--color-border, #e5e7eb)",
                  borderRadius: "8px",
                  padding: "0.875rem",
                  background: "var(--color-surface-subtle, #fafafa)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "0.5rem",
                  }}
                >
                  <strong style={{ fontSize: "0.95rem" }}>{d.name}</strong>
                  {getDoubletStatusBadge(d.status)}
                </div>

                {d.status === "absorbed" || d.status === "thin" ? (
                  <div
                    style={{
                      fontSize: "0.85rem",
                      display: "flex",
                      flexDirection: "column",
                      gap: "0.35rem",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: "var(--color-text-muted)" }}>Отношение R_obs / R_theo:</span>
                      <span>
                        <strong>{d.observed_ratio.toFixed(2)}</strong> / {d.theoretical_ratio.toFixed(2)}
                      </span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: "var(--color-text-muted)" }}>Оптическая толщина τ₀:</span>
                      <span>
                        <strong>{d.tau_line1.toFixed(3)}</strong>
                      </span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: "var(--color-text-muted)" }}>Коэфф. пропускания SA:</span>
                      <span>{(d.sa_factor_line1 * 100).toFixed(1)}%</span>
                    </div>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        paddingTop: "0.25rem",
                        borderTop: "1px dashed var(--color-border, #e5e7eb)",
                      }}
                    >
                      <span style={{ color: "var(--color-text-muted)" }}>Коррекция I_obs → I₀:</span>
                      <span>
                        {d.line1_i_obs.toFixed(1)} → <strong>{d.line1_i_corrected.toFixed(1)}</strong>
                      </span>
                    </div>
                  </div>
                ) : (
                  <div style={{ fontSize: "0.85rem", color: "var(--color-text-muted)" }}>
                    Линии дублета не идентифицированы или вне диапазона спектра.
                  </div>
                )}
              </div>
            ))}
          </div>

          {cfData && cfData.elements.length > 0 && (
            <div
              style={{
                marginTop: "1.25rem",
                padding: "1rem",
                borderRadius: "8px",
                border: "1px solid var(--color-border, #e5e7eb)",
                background: "var(--color-surface, #ffffff)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: "0.75rem",
                }}
              >
                <div>
                  <h4 style={{ margin: 0, fontSize: "0.95rem", fontWeight: 600 }}>
                    Бескалибровочный элементный состав (CF-LIBS)
                  </h4>
                  <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
                    Температура плазмы: {cfData.temperature_k.toFixed(0)} K • Условие нормировки: ∑Cᵢ = 100%
                  </span>
                </div>
                <button
                  type="button"
                  className="button button-subtle button-sm"
                  onClick={() => setShowDetails(!showDetails)}
                  style={{ fontSize: "0.8rem" }}
                >
                  {showDetails ? "Скрыть детали" : "Подробнее"}
                </button>
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
                  gap: "0.75rem",
                }}
              >
                {cfData.elements.map((el) => (
                  <div
                    key={el.element}
                    style={{
                      padding: "0.6rem 0.75rem",
                      background: "var(--color-surface-subtle, #f9fafb)",
                      borderRadius: "6px",
                      border: "1px solid var(--color-border-subtle, #f3f4f6)",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                      <strong style={{ fontSize: "1rem", color: "var(--color-primary, #2563eb)" }}>
                        {el.element}
                      </strong>
                      <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                        {el.lines_used_count} лин.
                      </span>
                    </div>
                    <div style={{ fontSize: "1.1rem", fontWeight: 600, margin: "0.25rem 0" }}>
                      {el.weight_fraction_wt.toFixed(1)}%
                    </div>
                    <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                      Атомная: {(el.atomic_fraction * 100).toFixed(1)}% (±{el.uncertainty_wt.toFixed(1)}%)
                    </div>
                  </div>
                ))}
              </div>

              {showDetails && (
                <div
                  style={{
                    marginTop: "1rem",
                    paddingTop: "0.75rem",
                    borderTop: "1px solid var(--color-border, #e5e7eb)",
                    fontSize: "0.85rem",
                  }}
                >
                  <p style={{ margin: "0 0 0.5rem" }}>
                    <strong>Резюме:</strong> {cfData.summary}
                  </p>
                  <p style={{ margin: 0, color: "var(--color-text-muted)" }}>
                    Коэффициент нормировки плазмы F: {cfData.normalization_factor_f.toExponential(3)}.
                    Расчёт выполнен по интегральным населённостям линий NIST и статистическим суммам U(T).
                  </p>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
