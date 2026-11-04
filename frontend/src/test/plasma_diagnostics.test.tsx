import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type PlasmaDiagnosticsResponse } from "../api/client";
import { PlasmaDiagnosticsCard } from "../components/PlasmaDiagnosticsCard";

vi.mock("../components/Chart", () => ({
  Chart: () => <div aria-label="График Больцмана" />,
}));

const mockPlasmaResponse: PlasmaDiagnosticsResponse = {
  boltzmann: {
    temperature_k: 9850.0,
    temperature_uncertainty_k: 420.0,
    r_squared: 0.942,
    slope: -1.178,
    intercept: 14.5,
    slope_stderr: 0.05,
    element: "Fe",
    ion_stage: 1,
    points: [
      {
        element: "Fe",
        ion_stage: 1,
        wavelength_nm: 373.59,
        ek_ev: 4.18,
        ei_ev: 0.86,
        gk: 11.0,
        aki: 9.01e7,
        net_intensity: 1.25e5,
        y_value: 9.58,
        residual: 0.02,
      },
      {
        element: "Fe",
        ion_stage: 1,
        wavelength_nm: 381.69,
        ek_ev: 4.73,
        ei_ev: 1.48,
        gk: 7.0,
        aki: 1.12e8,
        net_intensity: 8.4e4,
        y_value: 8.95,
        residual: -0.01,
      },
      {
        element: "Fe",
        ion_stage: 1,
        wavelength_nm: 426.17,
        ek_ev: 5.31,
        ei_ev: 2.40,
        gk: 11.0,
        aki: 3.99e7,
        net_intensity: 4.1e4,
        y_value: 8.24,
        residual: -0.03,
      },
      {
        element: "Fe",
        ion_stage: 1,
        wavelength_nm: 376.66,
        ek_ev: 6.53,
        ei_ev: 3.24,
        gk: 15.0,
        aki: 9.51e7,
        net_intensity: 1.5e4,
        y_value: 6.81,
        residual: 0.01,
      },
    ],
    confidence_score: 0.92,
    status: "valid",
    status_message: "Recovered Te = 9850 ± 420 K (N=4, R²=0.942)",
  },
  stark: {
    electron_density_cm3: 8.2e16,
    line_name: "H-alpha",
    center_wavelength_nm: 656.46,
    observed_fwhm_nm: 0.92,
    instrumental_fwhm_nm: 0.10,
    stark_fwhm_nm: 0.90,
    status: "detected",
    status_message: "Stark FWHM = 0.90 nm -> ne = 8.20e+16 cm^-3",
  },
  mcwhirter: {
    criterion_satisfied: true,
    min_electron_density_cm3: 2.48e15,
    temperature_k: 9850.0,
    energy_gap_ev: 2.5,
    status: "satisfied",
    notes: "LTE verified: measured ne >= threshold",
  },
  summary: {
    temperature_k: 9850.0,
    temperature_uncertainty_k: 420.0,
    electron_density_cm3: 8.2e16,
    element: "Fe",
    boltzmann_r_squared: 0.942,
    boltzmann_points_count: 4,
    lte_valid: true,
    diagnostics_ready: true,
  },
};

describe("PlasmaDiagnosticsCard component", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    vi.clearAllMocks();
  });

  const renderComponent = (props: {
    spectrumId?: string;
    wavelengthNm?: number[];
    intensity?: number[];
  }) => {
    return render(
      <QueryClientProvider client={queryClient}>
        <PlasmaDiagnosticsCard {...props} />
      </QueryClientProvider>,
    );
  };

  it("renders diagnostics metrics and Boltzmann plot on success", async () => {
    vi.spyOn(api, "plasmaDiagnostics").mockResolvedValueOnce(mockPlasmaResponse);

    renderComponent({ spectrumId: "spec-test-1234" });

    // Header and title
    expect(screen.getByText("Параметры плазмы и LTE")).toBeInTheDocument();

    // Temperature metric
    await waitFor(() => {
      expect(screen.getByText(/9\s*850/)).toBeInTheDocument();
      expect(screen.getByText(/± 420 K/)).toBeInTheDocument();
    });

    // Badges
    expect(screen.getByText("LTE оценка")).toBeInTheDocument();
    expect(screen.getByText("R² = 0.942")).toBeInTheDocument();
    expect(screen.getByText(/4 линий \(Fe\)/)).toBeInTheDocument();

    // Electron density
    expect(screen.getByText(/8.20e\+16/)).toBeInTheDocument();
    expect(screen.getByText(/Штарк H/)).toBeInTheDocument();

    // McWhirter LTE criterion
    expect(screen.getByText("Выполнен")).toBeInTheDocument();

    // Plot placeholder
    expect(screen.getByLabelText("График Больцмана")).toBeInTheDocument();
  });

  it("toggles table of fitted lines on button click", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "plasmaDiagnostics").mockResolvedValueOnce(mockPlasmaResponse);

    renderComponent({ spectrumId: "spec-test-1234" });

    await waitFor(() => {
      expect(screen.getByText(/Показать линии \(4\)/)).toBeInTheDocument();
    });

    // Click to show table
    const toggleButton = screen.getByText(/Показать линии \(4\)/);
    await user.click(toggleButton);

    // Verify table headers and values
    expect(screen.getByText("Скрыть таблицу линий")).toBeInTheDocument();
    expect(screen.getByText("373.59")).toBeInTheDocument();
    expect(screen.getByText("381.69")).toBeInTheDocument();
    expect(screen.getByText("426.17")).toBeInTheDocument();
  });

  it("switches element when user clicks an element button", async () => {
    const user = userEvent.setup();
    const mockCall = vi.spyOn(api, "plasmaDiagnostics").mockResolvedValue(mockPlasmaResponse);

    renderComponent({ spectrumId: "spec-test-1234" });

    await waitFor(() => {
      expect(screen.getByText(/9\s*850/)).toBeInTheDocument();
    });

    // Click "Ti I" button
    const tiButton = screen.getByText("Ti I");
    await user.click(tiButton);

    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ element: "Ti" }),
        expect.anything(),
      );
    });
  });

  it("handles API error state gracefully", async () => {
    vi.spyOn(api, "plasmaDiagnostics").mockRejectedValueOnce(new Error("API connection error"));

    renderComponent({ spectrumId: "spec-test-1234" });

    await waitFor(() => {
      expect(screen.getByTestId("plasma-error")).toHaveTextContent(
        "Не удалось выполнить расчёт физических параметров плазмы.",
      );
    });
  });
});
