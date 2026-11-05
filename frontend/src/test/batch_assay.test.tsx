import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type BatchAssayResponse } from "../api/client";
import { BatchAssayCard } from "../components/BatchAssayCard";

const mockAssayMatrix: BatchAssayResponse = {
  batch_id: "batch_123",
  summary: {
    total_samples: 3,
    in_domain_count: 2,
    near_boundary_count: 1,
    out_of_domain_count: 0,
    mean_concentrations: { FeOT: 14.5, MgO: 6.2, CaO: 5.1 },
    mean_total_mass_wt_percent: 25.8,
    confidence_level: 0.95,
  },
  samples: [
    {
      file_id: "f1",
      relative_path: "sample_01.csv",
      sample_id: "S01",
      status: "ready",
      total_mass_wt_percent: 24.5,
      applicability_status: "in_domain",
      max_leverage: 0.25,
      predictions: {
        FeOT: {
          species: "FeOT",
          element: "Fe",
          value_wt_percent: 15.0,
          lower_wt_percent: 13.0,
          upper_wt_percent: 17.0,
          confidence_level: 0.95,
          unit: "wt_percent",
          status: "in_domain",
          lod_wt_percent: 0.05,
          leverage: 0.25,
          warning: null,
        },
      },
    },
    {
      file_id: "f2",
      relative_path: "sample_02.csv",
      sample_id: "S02",
      status: "ready",
      total_mass_wt_percent: 27.1,
      applicability_status: "near_boundary",
      max_leverage: 0.65,
      predictions: {
        FeOT: {
          species: "FeOT",
          element: "Fe",
          value_wt_percent: 14.0,
          lower_wt_percent: 12.0,
          upper_wt_percent: 16.0,
          confidence_level: 0.95,
          unit: "wt_percent",
          status: "near_boundary",
          lod_wt_percent: 0.05,
          leverage: 0.65,
          warning: null,
        },
      },
    },
  ],
};

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("BatchAssayCard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "batchAssayMatrix").mockResolvedValue(mockAssayMatrix);
  });

  it("renders KPIs and samples with AD badges", async () => {
    renderWithQuery(<BatchAssayCard batchId="batch_123" />);

    await waitFor(() => {
      expect(
        screen.getByText(/Количественный анализ оксидов и матричный аудит/i)
      ).toBeInTheDocument();
      expect(screen.getByText("sample_01.csv")).toBeInTheDocument();
      expect(screen.getByText("sample_02.csv")).toBeInTheDocument();
    });

    expect(screen.getByText("25.80 wt%")).toBeInTheDocument();
    expect(screen.getByText("14.50 wt%")).toBeInTheDocument();
    expect(screen.getByText("in_domain")).toBeInTheDocument();
    expect(screen.getByText("near_boundary")).toBeInTheDocument();
  });

  it("handles confidence level change and calls API with new confidence", async () => {
    const user = userEvent.setup();
    renderWithQuery(<BatchAssayCard batchId="batch_123" />);

    await waitFor(() => expect(screen.getByText("sample_01.csv")).toBeInTheDocument());

    const select = screen.getByLabelText(/Уровень доверия/i);
    await user.selectOptions(select, "0.99");

    await waitFor(() => {
      expect(api.batchAssayMatrix).toHaveBeenCalledWith("batch_123", 0.99, expect.any(Object));
    });
  });

  it("renders error notice if API fails", async () => {
    vi.spyOn(api, "batchAssayMatrix").mockRejectedValue(new Error("Network failure"));
    renderWithQuery(<BatchAssayCard batchId="batch_123" />);

    await waitFor(() => {
      expect(screen.getByText("Network failure")).toBeInTheDocument();
    });
  });
});

