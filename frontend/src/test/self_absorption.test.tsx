import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type SelfAbsorptionResponse, type CFLibsResponse } from "../api/client";
import { SelfAbsorptionCard } from "../components/SelfAbsorptionCard";

const mockSelfAbsorption: SelfAbsorptionResponse = {
  doublets: [
    {
      name: "Mg II 279.64 / 280.35",
      element: "Mg",
      ion_stage: 2,
      status: "absorbed",
      observed_ratio: 1.25,
      theoretical_ratio: 2.02,
      tau_line1: 1.45,
      tau_line2: 0.72,
      sa_factor_line1: 0.528,
      sa_factor_line2: 0.713,
      line1_wl_nm: 279.635,
      line2_wl_nm: 280.353,
      line1_i_obs: 100.0,
      line2_i_obs: 80.0,
      line1_i_corrected: 189.4,
      line2_i_corrected: 112.2,
    },
    {
      name: "Ca II 393.48 / 396.96",
      element: "Ca",
      ion_stage: 2,
      status: "thin",
      observed_ratio: 2.05,
      theoretical_ratio: 2.1,
      tau_line1: 0.05,
      tau_line2: 0.024,
      sa_factor_line1: 0.975,
      sa_factor_line2: 0.988,
      line1_wl_nm: 393.477,
      line2_wl_nm: 396.959,
      line1_i_obs: 50.0,
      line2_i_obs: 24.4,
      line1_i_corrected: 51.3,
      line2_i_corrected: 24.7,
    },
    {
      name: "Na I 589.16 / 589.76",
      element: "Na",
      ion_stage: 1,
      status: "not_detected",
      observed_ratio: 0.0,
      theoretical_ratio: 2.0,
      tau_line1: 0.0,
      tau_line2: 0.0,
      sa_factor_line1: 1.0,
      sa_factor_line2: 1.0,
      line1_wl_nm: 589.158,
      line2_wl_nm: 589.756,
      line1_i_obs: 0.0,
      line2_i_obs: 0.0,
      line1_i_corrected: 0.0,
      line2_i_corrected: 0.0,
    },
  ],
  mean_self_absorption_factor: 0.7515,
  summary: "Анализ 3 реперных дублетов: 2 обнаружено, 1 с оптической толщиной tau_0 >= 0.2",
};

const mockCFLibs: CFLibsResponse = {
  temperature_k: 8500.0,
  elements: [
    {
      element: "Mg",
      atomic_fraction: 0.45,
      weight_fraction_wt: 42.1,
      intercept_q: 12.3,
      lines_used_count: 2,
      uncertainty_wt: 3.5,
    },
    {
      element: "Ca",
      atomic_fraction: 0.55,
      weight_fraction_wt: 57.9,
      intercept_q: 13.1,
      lines_used_count: 2,
      uncertainty_wt: 4.1,
    },
  ],
  normalization_factor_f: 1.25e7,
  summary: "CF-LIBS оценка по 2 элементам (сумма 100%).",
};

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("SelfAbsorptionCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders self-absorption and CF-LIBS cards with correct data", async () => {
    vi.spyOn(api, "plasmaSelfAbsorption").mockResolvedValue(mockSelfAbsorption);
    vi.spyOn(api, "plasmaCFLibs").mockResolvedValue(mockCFLibs);

    renderWithClient(
      <SelfAbsorptionCard
        spectrumId="test-id"
        wavelengthNm={[250, 300]}
        intensity={[10, 20]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Mg II 279.64 / 280.35")).toBeInTheDocument();
    });
    expect(screen.getByText("Самопоглощение (τ₀ ≥ 0.2)")).toBeInTheDocument();
    expect(screen.getByText("Ca II 393.48 / 396.96")).toBeInTheDocument();
    expect(screen.getByText("Оптически тонкий")).toBeInTheDocument();
    expect(screen.getByText("Не обнаружен")).toBeInTheDocument();

    // CF-LIBS elements
    expect(screen.getByText("Бескалибровочный элементный состав (CF-LIBS)")).toBeInTheDocument();
    expect(screen.getByText("42.1%")).toBeInTheDocument();
    expect(screen.getByText("57.9%")).toBeInTheDocument();
  });

  it("toggles details view in CF-LIBS section", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "plasmaSelfAbsorption").mockResolvedValue(mockSelfAbsorption);
    vi.spyOn(api, "plasmaCFLibs").mockResolvedValue(mockCFLibs);

    renderWithClient(<SelfAbsorptionCard spectrumId="test-id" />);

    await waitFor(() => {
      expect(screen.getByText("Подробнее")).toBeInTheDocument();
    });

    await user.click(screen.getByText("Подробнее"));
    expect(screen.getByText(/CF-LIBS оценка по 2 элементам/)).toBeInTheDocument();
    expect(screen.getByText("Скрыть детали")).toBeInTheDocument();
  });

  it("displays error message when API call fails", async () => {
    vi.spyOn(api, "plasmaSelfAbsorption").mockRejectedValue(new Error("Network Error"));
    vi.spyOn(api, "plasmaCFLibs").mockRejectedValue(new Error("Network Error"));

    renderWithClient(<SelfAbsorptionCard spectrumId="test-id" />);

    await waitFor(() => {
      expect(
        screen.getByText(/Ошибка расчёта самопоглощения плазмы или CF-LIBS/),
      ).toBeInTheDocument();
    });
  });
});
