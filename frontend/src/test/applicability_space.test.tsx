import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type ApplicabilitySpaceResponse } from "../api/client";
import { ApplicabilityDomainChart } from "../components/ApplicabilityDomainChart";

// Mock Plotly Chart to avoid canvas/WebGL issues in jsdom
vi.mock("../components/Chart", () => ({
  Chart: ({ data }: { data: unknown }) => (
    <div data-testid="mock-chart">Plotly Chart (Traces: {Array.isArray(data) ? data.length : 0})</div>
  ),
}));

const mockApplicabilityResponse: ApplicabilitySpaceResponse = {
  sample_pc1: 1.25,
  sample_pc2: -0.45,
  t_squared: 2.35,
  t_squared_limit_95: 6.3,
  t_squared_limit_99: 9.9,
  q_residual: 1.15,
  q_residual_limit_95: 3.5,
  status: "in_domain",
  variance_explained: [0.65, 0.22],
  calibration_scores: [
    [1.0, 0.5, "Базальт"],
    [-1.2, 0.3, "Андезит"],
    [2.1, -1.0, "Гранит"],
  ],
  ellipse_95: [
    [2.0, 0.0],
    [0.0, 1.5],
    [-2.0, 0.0],
    [0.0, -1.5],
  ],
  ellipse_99: [
    [3.0, 0.0],
    [0.0, 2.2],
    [-3.0, 0.0],
    [0.0, -2.2],
  ],
  top_contributions: [
    {
      species: "FeOT",
      line_wl_nm: 373.59,
      contribution_percent: 42.5,
      deviation_direction: "high",
    },
    {
      species: "MgO",
      line_wl_nm: 285.3,
      contribution_percent: 28.1,
      deviation_direction: "low",
    },
  ],
  summary: "Образец находится внутри хемометрической области применимости",
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

describe("ApplicabilityDomainChart", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders PCA applicability domain chart with Hotelling T2 and Q-residuals", async () => {
    vi.spyOn(api, "quantitativeApplicabilitySpace").mockResolvedValue(mockApplicabilityResponse);

    renderWithClient(
      <ApplicabilityDomainChart
        spectrumId="spec-123"
        wavelengthNm={[350, 400]}
        intensity={[10, 20]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Внутри области (AD)")).toBeInTheDocument();
    });

    expect(screen.getByText("2.35")).toBeInTheDocument();
    expect(screen.getByText("1.15")).toBeInTheDocument();
    expect(screen.getByText(/PC1: 1.25, PC2: -0.45/)).toBeInTheDocument();
    expect(screen.getByTestId("mock-chart")).toBeInTheDocument();

    // Top contributions
    expect(screen.getByText("FeOT")).toBeInTheDocument();
    expect(screen.getByText("42.5%")).toBeInTheDocument();
    expect(screen.getByText("Превышение нормы")).toBeInTheDocument();
  });

  it("toggles feature contributions visibility", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "quantitativeApplicabilitySpace").mockResolvedValue(mockApplicabilityResponse);

    renderWithClient(<ApplicabilityDomainChart spectrumId="spec-123" />);

    await waitFor(() => {
      expect(screen.getByText("Скрыть")).toBeInTheDocument();
    });

    await user.click(screen.getByText("Скрыть"));
    expect(screen.getByText("Показать")).toBeInTheDocument();
    expect(screen.queryByText("Превышение нормы")).not.toBeInTheDocument();
  });

  it("handles API failure gracefully", async () => {
    vi.spyOn(api, "quantitativeApplicabilitySpace").mockRejectedValue(new Error("API Error"));

    renderWithClient(<ApplicabilityDomainChart spectrumId="spec-123" />);

    await waitFor(() => {
      expect(
        screen.getByText("Не удалось построить пространство применимости."),
      ).toBeInTheDocument();
    });
  });
});

