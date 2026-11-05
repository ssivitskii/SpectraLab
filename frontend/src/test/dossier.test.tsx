import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type MeasurementDossierResponse } from "../api/client";
import { DossierModal } from "../components/DossierModal";

const mockDossier: MeasurementDossierResponse = {
  dossier_id: "dos_test123456",
  spectrum_id: "spec_test_01",
  created_at_utc: "2026-11-05T12:00:00Z",
  sha256: "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
  point_count: 5000,
  wavelength_range_nm: [250.0, 650.0],
  transfer: null,
  plasma: {
    temperature_k: 9500.0,
    temperature_uncertainty_k: 450.0,
    electron_density_cm3: 2.5e16,
    element: "Fe",
    boltzmann_r_squared: 0.985,
    boltzmann_points_count: 8,
    lte_valid: true,
    diagnostics_ready: true,
  },
  quantitative: {
    predictions: [
      {
        species: "FeOT",
        element: "Fe",
        value_wt_percent: 18.5,
        lower_wt_percent: 16.2,
        upper_wt_percent: 20.8,
        confidence_level: 0.95,
        unit: "wt_percent",
        status: "in_domain",
        lod_wt_percent: 0.05,
        leverage: 0.35,
        warning: null,
      },
      {
        species: "MgO",
        element: "Mg",
        value_wt_percent: 8.2,
        lower_wt_percent: 7.1,
        upper_wt_percent: 9.3,
        confidence_level: 0.95,
        unit: "wt_percent",
        status: "in_domain",
        lod_wt_percent: 0.02,
        leverage: 0.42,
        warning: null,
      },
    ],
    model_id: "ridge-conformal-v1",
    model_version: "1.0",
    confidence_level: 0.95,
    total_detected_mass_wt_percent: 26.7,
    applicability_domain_summary: "Все оксиды внутри области",
  },
  explainability: {
    lines: [
      {
        element: "Fe",
        ion_stage: 1,
        line_name: "Fe I 373.59",
        target_oxide: "FeOT",
        wavelength_vacuum_nm: 373.593,
        measured_wavelength_nm: 373.595,
        peak_intensity: 55.0,
        background_intensity: 5.0,
        net_intensity: 50.0,
        signal_to_background_ratio: 11.0,
        detected: true,
        is_blend: false,
        interfering_elements: [],
        relative_contribution_score: 120.0,
      },
      {
        element: "Mg",
        ion_stage: 1,
        line_name: "Mg I 285.30",
        target_oxide: "MgO",
        wavelength_vacuum_nm: 285.296,
        measured_wavelength_nm: 285.298,
        peak_intensity: 42.0,
        background_intensity: 4.0,
        net_intensity: 38.0,
        signal_to_background_ratio: 10.5,
        detected: true,
        is_blend: true,
        interfering_elements: ["Si 1"],
        relative_contribution_score: 95.0,
      },
    ],
    detected_elements: ["Fe", "Mg"],
    spectral_purity_score: 0.5,
    blend_count: 1,
    mean_signal_to_background: 10.75,
    summary: "Обнаружено 2 элемента по 2 аналитическим линиям",
  },
  executive_summary: "Комплексное досье спектрального измерения: обнаружено 2 элементов.",
  markdown_report: "# Измерительное досье спектра dos_test123456\n\nПротокол измерения",
};

describe("DossierModal", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "dossierGenerate").mockResolvedValue(mockDossier);
  });

  it("does not render when closed", () => {
    const { container } = render(
      <DossierModal isOpen={false} onClose={vi.fn()} spectrumId="test" />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders dossier details, executive summary and badges when opened", async () => {
    render(<DossierModal isOpen={true} onClose={vi.fn()} spectrumId="test" />);

    await waitFor(() => {
      expect(screen.getByText("Измерительное досье спектра")).toBeInTheDocument();
      expect(screen.getByText("dos_test123456")).toBeInTheDocument();
      expect(screen.getByText("Fe I 373.59")).toBeInTheDocument();
    });

    expect(screen.getByText("50.0%")).toBeInTheDocument(); // purity
    expect(screen.getByText("9500 K")).toBeInTheDocument(); // Te
    expect(screen.getByText("26.70 wt%")).toBeInTheDocument(); // mass
  });

  it("switches tabs between lines, assay and markdown protocol", async () => {
    const user = userEvent.setup();
    render(<DossierModal isOpen={true} onClose={vi.fn()} spectrumId="test" />);

    await waitFor(() => expect(screen.getByText("Fe I 373.59")).toBeInTheDocument());

    // Switch to Assay tab
    const assayTab = screen.getByRole("button", { name: /Оксидный состав/i });
    await user.click(assayTab);
    expect(screen.getByText("FeOT")).toBeInTheDocument();
    expect(screen.getByText("18.500")).toBeInTheDocument();

    // Switch to Protocol tab
    const protocolTab = screen.getByRole("button", { name: /Протокол/i });
    await user.click(protocolTab);
    expect(screen.getByText(/# Измерительное досье спектра/)).toBeInTheDocument();
  });

  it("filters lines by input search", async () => {
    const user = userEvent.setup();
    render(<DossierModal isOpen={true} onClose={vi.fn()} spectrumId="test" />);

    await waitFor(() => expect(screen.getByText("Fe I 373.59")).toBeInTheDocument());

    const input = screen.getByPlaceholderText(/Фильтр по элементу/i);
    await user.type(input, "Mg");

    expect(screen.getByText("Mg I 285.30")).toBeInTheDocument();
    expect(screen.queryByText("Fe I 373.59")).not.toBeInTheDocument();
  });

  it("handles close button click", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<DossierModal isOpen={true} onClose={onClose} spectrumId="test" />);

    await waitFor(() => expect(screen.getByText("Измерительное досье спектра")).toBeInTheDocument());

    const closeBtn = screen.getByRole("button", { name: "Закрыть" });
    await user.click(closeBtn);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
