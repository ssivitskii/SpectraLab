import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { api, type Experiment } from "../api/client";
import { Experiments } from "../pages/Experiments";
import { capability } from "./fixtures";
vi.mock("../components/Chart", () => ({
  Chart: ({ data }: { data: unknown }) => (
    <pre aria-label="series-chart">{JSON.stringify(data)}</pre>
  ),
}));
const series: Experiment = {
  run_id: "series-one",
  status: "incomplete",
  created_at: "2026-10-13T09:00:00Z",
  reference_source: "demo_fixture",
  protocol: "paired-v1",
  demo: true,
  artifact_type: "experiment_series",
  configuration: {},
  schema_version: 1,
  runs: [],
  limitations: "Synthetic benchmark",
  source_hash: "sha",
  expected_seeds: [1, 2, 3],
  successful_seeds: [1],
  seed_runs: [
    { seed: 1, run_id: "run-1", status: "completed" },
    { seed: 2, status: "failed", error: "training failed" },
    { seed: 3, status: "incomplete", error: "condition missing" },
  ],
  aggregates: [
    {
      model_id: "builtin-nnls",
      factor: "snr_db",
      value: 20,
      metrics: {
        micro_f1: { mean: 0.75, std: null, count: 1 },
        single: { micro_f1: { mean: null, std: null, count: 0 } },
        mixture: { micro_f1: { mean: 0.8, std: null, count: 1 } },
      },
    },
  ],
};
beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "capabilities").mockResolvedValue(capability);
  vi.spyOn(api, "jobs").mockResolvedValue([]);
  vi.spyOn(api, "experimentCatalog").mockResolvedValue({
    items: [series],
    next_cursor: null,
  });
  vi.spyOn(api, "experiment").mockResolvedValue(series);
});
function mount() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={["/experiments?run=series-one"]}>
        <Experiments />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
it("keeps null missing, shows failed/incomplete seeds, uses server subset aggregates", async () => {
  mount();
  expect(
    await screen.findByText("training failed", { exact: false }),
  ).toBeInTheDocument();
  expect(
    screen.getByText("condition missing", { exact: false }),
  ).toBeInTheDocument();
  expect(screen.getByText("0.750")).toBeInTheDocument();
  expect(screen.getByLabelText("series-chart")).toHaveTextContent(
    '"array":[null]',
  );
  await userEvent.selectOptions(screen.getByLabelText("Состав"), "single");
  expect(screen.getByLabelText("series-chart")).toHaveTextContent('"y":[null]');
  expect(screen.queryByText("0.000")).not.toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("Состав"), "mixture");
  expect(screen.getByText("0.800")).toBeInTheDocument();
});
it("rejects incompatible protocol from comparison without inventing merged metrics", async () => {
  const other = { ...series, run_id: "series-other", protocol: "different-v2" };
  vi.spyOn(api, "experimentCatalog").mockResolvedValue({
    items: [series, other],
    next_cursor: null,
  });
  vi.spyOn(api, "experiment").mockImplementation(async (id) =>
    id === other.run_id ? other : series,
  );
  mount();
  await userEvent.click(
    await screen.findByRole("checkbox", { name: "Сравнить" }),
  );
  expect(await screen.findByText(/исключён из сравнения/)).toBeInTheDocument();
  expect(screen.getByLabelText("series-chart")).not.toHaveTextContent(
    other.run_id,
  );
});
it("loads cursor pages and resets cursor on filter changes", async () => {
  const catalog = vi
    .spyOn(api, "experimentCatalog")
    .mockImplementation(async (filters) => ({
      items: filters.cursor ? [{ ...series, run_id: "older" }] : [series],
      next_cursor: filters.cursor ? null : "cursor-one",
    }));
  mount();
  await userEvent.click(
    await screen.findByRole("button", { name: "Ещё запуски" }),
  );
  await waitFor(() =>
    expect(catalog).toHaveBeenCalledWith(
      expect.objectContaining({ cursor: "cursor-one" }),
      expect.anything(),
    ),
  );
  await userEvent.selectOptions(
    screen.getByLabelText("Статус", { exact: true }),
    "completed",
  );
  await waitFor(() =>
    expect(catalog).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: undefined, status: "completed" }),
      expect.anything(),
    ),
  );
});
