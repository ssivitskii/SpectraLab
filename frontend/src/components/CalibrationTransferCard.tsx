import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Data, Layout } from "plotly.js";
import {
  api,
  type TransferApplyResponse,
  type TransferProfilesListResponse,
} from "../api/client";
import { Chart } from "./Chart";
import { chartColors, photonChartLayout } from "./chartTheme";
import { Loading } from "./Feedback";
import { Icon } from "./Icon";

function formatMethodName(method: string): string {
  switch (method) {
    case "pds":
      return "PDS стандартизация";
    case "wavelength_alignment":
      return "Совмещение длин волн";
    case "combined":
      return "PDS + Совмещение шкалы";
    case "identity":
      return "Прямой спектр (Raw)";
    default:
      return method;
  }
}

export function CalibrationTransferCard({
  spectrumId,
  wavelengthNm,
  intensity,
  medium = "vacuum",
  onTransferApplied,
}: {
  spectrumId?: string;
  wavelengthNm?: number[];
  intensity?: number[];
  medium?: string;
  onTransferApplied?: (
    correctedWave: number[],
    correctedFlux: number[],
    profileId: string,
  ) => void;
}) {
  const [selectedProfile, setSelectedProfile] = useState<string>("identity");
  const [showDiff, setShowDiff] = useState<boolean>(false);

  // 1. Fetch available profiles
  const profilesQuery = useQuery<TransferProfilesListResponse>({
    queryKey: ["transferProfiles"],
    queryFn: ({ signal }) => api.transferProfiles({ signal }),
    staleTime: 120_000,
  });

  const isEnabled = Boolean(
    spectrumId || (wavelengthNm && intensity && wavelengthNm.length >= 10),
  );

  // 2. Apply transfer transformation
  const applyQuery = useQuery<TransferApplyResponse>({
    queryKey: [
      "transferApply",
      spectrumId,
      selectedProfile,
      medium,
      wavelengthNm?.length,
    ],
    queryFn: ({ signal }) =>
      api.transferApply(
        {
          spectrum_id: spectrumId ?? null,
          wavelength_nm: spectrumId ? null : (wavelengthNm ?? null),
          intensity: spectrumId ? null : (intensity ?? null),
          profile_id: selectedProfile,
          medium: (medium === "air" ? "air" : "vacuum") as "air" | "vacuum",
        },
        { signal },
      ),
    enabled: isEnabled,
    staleTime: 60_000,
  });

  const transferResult = applyQuery.data;

  // Propagate applied transfer to parent if listener provided
  useEffect(() => {
    if (transferResult && onTransferApplied) {
      onTransferApplied(
        transferResult.wavelength_nm,
        transferResult.intensity,
        transferResult.profile_id,
      );
    }
  }, [transferResult, onTransferApplied]);

  // Chart data
  const chartTraces = useMemo<Data[]>(() => {
    if (!transferResult || transferResult.is_identity || !wavelengthNm || !intensity) {
      return [];
    }

    const traces: Data[] = [
      {
        x: wavelengthNm,
        y: intensity,
        type: "scatter",
        mode: "lines",
        name: "Исходный спектр",
        line: { color: chartColors.muted, width: 1.5, dash: "dot" },
        opacity: 0.7,
      },
      {
        x: transferResult.wavelength_nm,
        y: transferResult.intensity,
        type: "scatter",
        mode: "lines",
        name: `Скорректированный (${transferResult.profile_id})`,
        line: { color: chartColors.observed, width: 2 },
      },
    ];

    if (showDiff && wavelengthNm.length === transferResult.wavelength_nm.length) {
      const diff = transferResult.intensity.map((v, i) => v - (intensity[i] ?? 0));
      traces.push({
        x: transferResult.wavelength_nm,
        y: diff,
        type: "scatter",
        mode: "lines",
        name: "Разность (Δ = Corr - Raw)",
        line: { color: chartColors.active, width: 1.5 },
      });
    }

    return traces;
  }, [transferResult, wavelengthNm, intensity, showDiff]);

  const chartLayout = useMemo<Partial<Layout>>(() => {
    return photonChartLayout({
      title: { text: "Сравнение спектра: до и после калибровочного переноса", font: { size: 13 } },
      xaxis: { title: { text: "Длина волны, нм" } },
      yaxis: { title: { text: "Интенсивность, отн. ед." } },
      height: 280,
      margin: { l: 55, r: 25, t: 35, b: 40 },
      showlegend: true,
      legend: { orientation: "h", y: -0.25 },
    });
  }, []);

  const profiles = profilesQuery.data?.profiles ?? [];
  const currentProfile = profiles.find((p) => p.profile_id === selectedProfile);

  return (
    <section
      className="panel"
      data-testid="calibration-transfer-card"
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
          <Icon name="sliders" size={20} />
          <div>
            <h3 style={{ margin: 0, fontSize: "1.05rem", fontWeight: 600 }}>
              Калибровочный перенос спектра (Calibration Transfer / PDS)
            </h3>
            <span className="muted" style={{ fontSize: "0.82rem" }}>
              Адаптация к аппаратным сдвигам, размытию линий и межприборной чувствительности
            </span>
          </div>
        </div>

        {/* Profile Selector */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
          <label
            htmlFor="transfer-profile-select"
            style={{ fontSize: "0.85rem", color: "var(--muted)" }}
          >
            Профиль переноса:
          </label>
          <select
            id="transfer-profile-select"
            data-testid="transfer-profile-select"
            className="select"
            style={{ padding: "0.25rem 0.6rem", fontSize: "0.85rem", maxWidth: "260px" }}
            value={selectedProfile}
            onChange={(e) => setSelectedProfile(e.target.value)}
          >
            {profiles.map((p) => (
              <option key={p.profile_id} value={p.profile_id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Loading state */}
      {applyQuery.isLoading && (
        <div style={{ padding: "1.5rem", display: "flex", justifyContent: "center" }}>
          <Loading>Применение калибровочной трансформации...</Loading>
        </div>
      )}

      {/* Error state */}
      {applyQuery.isError && (
        <div
          data-testid="transfer-error"
          className="badge badge-danger"
          style={{ padding: "0.75rem", width: "100%", justifyContent: "flex-start" }}
        >
          <Icon name="info" size={16} />
          <span>
            Ошибка переноса:{" "}
            {applyQuery.error instanceof Error
              ? applyQuery.error.message
              : "Не удалось применить трансформацию"}
          </span>
        </div>
      )}

      {/* Success Content */}
      {applyQuery.isSuccess && transferResult && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.9rem" }}>
          {/* Diagnostic Metrics Row */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
              gap: "0.75rem",
            }}
          >
            {/* Shift Card */}
            <div className="metric-box">
              <span className="metric-label">Сдвиг оси длины волны (Δλ)</span>
              <div className="metric-value">
                {transferResult.diagnostics.shift_detected_nm !== 0
                  ? `${transferResult.diagnostics.shift_detected_nm > 0 ? "+" : ""}${transferResult.diagnostics.shift_detected_nm.toFixed(3)} нм`
                  : "0.000 нм"}
              </div>
              <span className="muted" style={{ fontSize: "0.78rem" }}>
                {transferResult.diagnostics.shift_detected_nm !== 0
                  ? "Скомпенсирован полиномом"
                  : "Сдвиг не обнаружен / Identity"}
              </span>
            </div>

            {/* RMSD Change Card */}
            <div className="metric-box">
              <span className="metric-label">RMSD изменения формы</span>
              <div className="metric-value">
                {transferResult.diagnostics.rmsd_change.toFixed(2)}
              </div>
              <span className="muted" style={{ fontSize: "0.78rem" }}>
                СКО коррекции профиля
              </span>
            </div>

            {/* Correction Factor Card */}
            <div className="metric-box">
              <span className="metric-label">Множитель отклика PDS</span>
              <div className="metric-value">
                {transferResult.diagnostics.mean_correction_factor.toFixed(3)}×
              </div>
              <span className="muted" style={{ fontSize: "0.78rem" }}>
                Медианный масштаб чувствительности
              </span>
            </div>

            {/* Profile Status Card */}
            <div className="metric-box">
              <span className="metric-label">Метод трансформации</span>
              <div style={{ marginTop: "0.2rem" }}>
                <span
                  className={`badge ${
                    transferResult.is_identity ? "badge-neutral" : "badge-success"
                  }`}
                >
                  {formatMethodName(transferResult.method)}
                </span>
              </div>
              <span className="muted" style={{ fontSize: "0.78rem", marginTop: "0.25rem", display: "block" }}>
                {currentProfile?.target_instrument
                  ? `${currentProfile.source_instrument} → ${currentProfile.target_instrument}`
                  : "Без приборного переноса"}
              </span>
            </div>
          </div>

          {/* Interactive Comparison Plot */}
          {chartTraces.length > 0 && (
            <div
              style={{
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-md)",
                padding: "0.75rem",
                background: "var(--surface-sunken)",
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
                <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>
                  Визуальный контроль переноса
                </span>
                <button
                  type="button"
                  data-testid="toggle-diff-btn"
                  className="button button-small button-ghost"
                  onClick={() => setShowDiff(!showDiff)}
                >
                  <Icon name="activity" size={14} />
                  {showDiff ? "Скрыть разность (Δ)" : "Показать разность (Δ)"}
                </button>
              </div>

              <div style={{ minHeight: "280px" }}>
                <Chart
                  data={chartTraces}
                  layout={chartLayout}
                  label="График калибровочного переноса"
                />
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

