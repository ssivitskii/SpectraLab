import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, parseSeriesCsvPreview, type SeriesAnalysisResponse } from "../api/client";
import { ShotSeries } from "../pages/ShotSeries";

vi.mock("../components/Chart", () => ({
  Chart: () => <div aria-label="Спектральный график серии выстрелов" />,
}));

const mockSeriesResponse: SeriesAnalysisResponse = {
  schema_version: 2,
  series_id: "test-series-uuid-1234",
  created_at: "2026-11-04T18:00:00Z",
  status: "completed",
  workflow: "chemcam-shot-series-v1",
  source_sha256: "187125973eaf59d6a4f1182ffb3360168d0e05397ea78ace6862ece0f13723ea",
  metadata_sha256: "test-meta-sha-5678",
  shot_count: 4,
  accepted_shot_count: 3,
  accepted_shot_indices: [0, 1, 3],
  discard_first_shots: 5,
  discarded_shot_numbers: [1, 2, 3, 4, 5],
  accepted_shot_numbers: [6, 7, 9],
  policy: {
    min_snr: 3.0,
    min_accepted_shots: 2,
    saturation_ceiling: null,
    outlier_mad_z: 6.0,
  },
  shots: [
    {
      shot_index: 0,
      shot_number: 6,
      status: "accepted",
      snr: 12.5,
      snr_status: "finite",
      normalised_shape_distance: 0.05,
      reasons: [],
    },
    {
      shot_index: 1,
      shot_number: 7,
      status: "accepted",
      snr: 14.2,
      snr_status: "finite",
      normalised_shape_distance: 0.04,
      reasons: [],
    },
    {
      shot_index: 2,
      shot_number: 8,
      status: "rejected_quality",
      snr: 1.2,
      snr_status: "finite",
      normalised_shape_distance: 0.45,
      reasons: ["low_snr", "shape_outlier"],
    },
    {
      shot_index: 3,
      shot_number: 9,
      status: "accepted",
      snr: 11.8,
      snr_status: "finite",
      normalised_shape_distance: 0.06,
      reasons: [],
    },
  ],
  measurement: {
    measurement_id: "synthetic-shot-series-demo",
    physical_sample_id: "synthetic-material-demo",
    family_id: "synthetic-family-demo",
    session_id: "synthetic-session-demo",
    instrument_id: "synthetic-fixture-only",
  },
  coverage: [
    { band_nm: [500.0, 501.0], points: 11 },
  ],
  outputs: {
    "aggregate.csv": { sha256: "agg-sha", bytes: 1200 },
    "dispersion.csv": { sha256: "disp-sha", bytes: 1200 },
    "metadata.json": { sha256: "meta-sha", bytes: 400 },
    "receipt.json": { sha256: "rec-sha", bytes: 2000 },
  },
  aggregate: [
    { wavelength_nm: 500.0, intensity: 1.0, intensity_mad: 0.1 },
    { wavelength_nm: 500.5, intensity: 8.0, intensity_mad: 0.5 },
    { wavelength_nm: 501.0, intensity: 1.0, intensity_mad: 0.1 },
  ],
};

function wrap(element: React.ReactNode, route = "/series") {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <MemoryRouter initialEntries={[route]}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "seriesList").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(api, "downloadSeriesFile").mockResolvedValue();
  vi.spyOn(api, "deleteSeries").mockResolvedValue({ success: true, series_id: "test-series-uuid-1234" });
});

describe("ShotSeries Page", () => {
  it("renders empty state initially", () => {
    wrap(<ShotSeries />);
    expect(screen.getByText("Серия выстрелов не загружена")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Анализировать серию" })).toBeDisabled();
  });

  it("loads demo series and enables submit button", async () => {
    wrap(<ShotSeries />);
    const loadDemoBtn = screen.getByRole("button", { name: /Демо-серия/ });
    await userEvent.click(loadDemoBtn);

    expect(screen.getByText(/Используется демо-набор/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Анализировать серию" })).toBeEnabled();
  });

  it("submits series analysis and displays results with diagnostics and exports", async () => {
    const analyzeSpy = vi.spyOn(api, "analyzeSeries").mockResolvedValue(mockSeriesResponse);
    wrap(<ShotSeries />);

    // Click demo series
    await userEvent.click(screen.getByRole("button", { name: /Демо-серия/ }));

    // Click submit
    const submitBtn = screen.getByRole("button", { name: "Анализировать серию" });
    await userEvent.click(submitBtn);

    await waitFor(() => expect(analyzeSpy).toHaveBeenCalledTimes(1));

    // Verify summary status
    expect(await screen.findByText("Успешно агрегирована")).toBeInTheDocument();
    expect(screen.getByText("Принято QC")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();

    // Verify shot diagnostics table
    expect(screen.getByText("#6")).toBeInTheDocument();
    expect(screen.getByText("#8")).toBeInTheDocument();
    expect(screen.getByText("Низкий SNR")).toBeInTheDocument();
    expect(screen.getByText("Аномалия формы (RMS outlier)")).toBeInTheDocument();

    // Verify export buttons
    expect(screen.getByRole("button", { name: /Скачать receipt.json/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Скачать aggregate.csv/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Скачать dispersion.csv/ })).toBeInTheDocument();
  });

  it("displays server error cleanly", async () => {
    vi.spyOn(api, "analyzeSeries").mockRejectedValue(new Error("Некорректный формат CSV серии"));
    wrap(<ShotSeries />);

    await userEvent.click(screen.getByRole("button", { name: /Демо-серия/ }));
    await userEvent.click(screen.getByRole("button", { name: "Анализировать серию" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Некорректный формат CSV серии");
  });

  it("parses series CSV preview correctly", () => {
    const csv = `# NSHOTS=3\n# wave,shot1,shot2,shot3,median,mean\n400.0,1,2,3,2,2\n400.1,4,5,6,5,5\n`;
    const preview = parseSeriesCsvPreview(csv);
    expect(preview.shotCount).toBe(3);
    expect(preview.pointCount).toBe(2);
    expect(preview.minWavelength).toBe(400.0);
    expect(preview.previewShots).toHaveLength(3);
    expect(preview.previewShots[0].values).toEqual([1, 4]);
  });
});
