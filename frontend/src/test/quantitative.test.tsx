import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type QuantitativeReportResponse } from "../api/client";
import { QuantitativeAssayCard } from "../components/QuantitativeAssayCard";

const mockQuantitativeReport: QuantitativeReportResponse = {
  model_id: "ridge-conformal-v1",
  model_version: "1.0.0",
  confidence_level: 0.95,
  total_detected_mass_wt_percent: 74.35,
  applicability_domain_summary: "Все оцениваемые компоненты находятся внутри калибровочной области",
  predictions: [
    {
      species: "FeOT",
      element: "Fe",
      value_wt_percent: 18.42,
      lower_wt_percent: 15.2,
      upper_wt_percent: 21.64,
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
      value_wt_percent: 8.75,
      lower_wt_percent: 6.8,
      upper_wt_percent: 10.7,
      confidence_level: 0.95,
      unit: "wt_percent",
      status: "in_domain",
      lod_wt_percent: 0.03,
      leverage: 0.42,
      warning: null,
    },
    {
      species: "CaO",
      element: "Ca",
      value_wt_percent: 0.02,
      lower_wt_percent: 0.0,
      upper_wt_percent: 0.04,
      confidence_level: 0.95,
      unit: "wt_percent",
      status: "below_lod",
      lod_wt_percent: 0.04,
      leverage: 0.12,
      warning: "Концентрация ниже предела обнаружения (< 0.04 wt%)",
    },
    {
      species: "TiO2",
      element: "Ti",
      value_wt_percent: 1.25,
      lower_wt_percent: 0.8,
      upper_wt_percent: 1.7,
      confidence_level: 0.95,
      unit: "wt_percent",
      status: "near_boundary",
      lod_wt_percent: 0.02,
      leverage: 1.15,
      warning: "Образец на границе применимости калибровочной модели",
    },
    {
      species: "SiO2",
      element: "Si",
      value_wt_percent: 45.93,
      lower_wt_percent: 41.2,
      upper_wt_percent: 50.66,
      confidence_level: 0.95,
      unit: "wt_percent",
      status: "out_of_domain",
      lod_wt_percent: 0.1,
      leverage: 2.85,
      warning: "Образец вне калибровочной области (OOD, высокий leverage)",
    },
  ],
};

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("QuantitativeAssayCard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders quantitative assay predictions with oxide cards, values, and conformal intervals", async () => {
    vi.spyOn(api, "quantitativePredict").mockResolvedValue(mockQuantitativeReport);

    renderWithClient(
      <QuantitativeAssayCard
        spectrumId="demo-spec-id"
        wavelengthNm={[300, 400, 500]}
        intensity={[10, 20, 30]}
      />,
    );

    expect(
      screen.getByText("Количественный анализ (Assay & Конформные интервалы)"),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("oxide-card-FeOT")).toBeInTheDocument();
    });

    // Check FeOT details
    expect(screen.getByText("18.42 wt%")).toBeInTheDocument();
    expect(screen.getByText("(184 200 ppm)")).toBeInTheDocument();
    expect(screen.getByText("[15.20 — 21.64] wt%")).toBeInTheDocument();

    // Check total detected mass
    expect(screen.getByText("74.35 wt%")).toBeInTheDocument();

    // Check applicability summary
    expect(
      screen.getByText(
        "Все оцениваемые компоненты находятся внутри калибровочной области",
      ),
    ).toBeInTheDocument();
  });

  it("switches confidence level and triggers query refetch", async () => {
    const predictSpy = vi
      .spyOn(api, "quantitativePredict")
      .mockResolvedValue(mockQuantitativeReport);

    renderWithClient(<QuantitativeAssayCard spectrumId="demo-spec-id" />);

    await waitFor(() => {
      expect(predictSpy).toHaveBeenCalledWith(
        expect.objectContaining({ confidence_level: 0.95 }),
        expect.anything(),
      );
    });

    const select = screen.getByTestId("confidence-select");
    await userEvent.selectOptions(select, "0.99");

    await waitFor(() => {
      expect(predictSpy).toHaveBeenCalledWith(
        expect.objectContaining({ confidence_level: 0.99 }),
        expect.anything(),
      );
    });
  });

  it("toggles full collapsible table with detailed columns", async () => {
    vi.spyOn(api, "quantitativePredict").mockResolvedValue(mockQuantitativeReport);

    renderWithClient(<QuantitativeAssayCard spectrumId="demo-spec-id" />);

    await waitFor(() => {
      expect(screen.getByTestId("toggle-table-btn")).toBeInTheDocument();
    });

    // Initially table is hidden
    expect(screen.queryByText("Оценка (wt%)")).not.toBeInTheDocument();

    // Click toggle button
    await userEvent.click(screen.getByTestId("toggle-table-btn"));

    // Now table headers and rows are visible
    expect(screen.getByText("Оценка (wt%)")).toBeInTheDocument();
    expect(screen.getByText("Leverage h(x)")).toBeInTheDocument();
    expect(screen.getByText("Статус AD")).toBeInTheDocument();

    // Click again to hide
    await userEvent.click(screen.getByTestId("toggle-table-btn"));
    expect(screen.queryByText("Оценка (wt%)")).not.toBeInTheDocument();
  });

  it("displays below_lod and out_of_domain badges correctly", async () => {
    vi.spyOn(api, "quantitativePredict").mockResolvedValue(mockQuantitativeReport);

    renderWithClient(<QuantitativeAssayCard spectrumId="demo-spec-id" />);

    await waitFor(() => {
      expect(screen.getByTestId("oxide-card-CaO")).toBeInTheDocument();
    });

    // Below LOD
    expect(screen.getByText("< 0.04 wt%")).toBeInTheDocument();
    expect(screen.getByText("< LOD")).toBeInTheDocument();

    // Out of domain
    expect(screen.getByText("Экстраполяция (OOD)")).toBeInTheDocument();
    expect(
      screen.getByText("Образец вне калибровочной области (OOD, высокий leverage)"),
    ).toBeInTheDocument();

    // Near boundary
    expect(screen.getByText("Граница AD")).toBeInTheDocument();
  });

  it("handles error state gracefully", async () => {
    vi.spyOn(api, "quantitativePredict").mockRejectedValue(
      new Error("API quantitative calculation error"),
    );

    renderWithClient(<QuantitativeAssayCard spectrumId="demo-spec-id" />);

    await waitFor(() => {
      expect(screen.getByTestId("quantitative-error")).toBeInTheDocument();
    });
    expect(
      screen.getByText(/API quantitative calculation error/),
    ).toBeInTheDocument();
  });
});

