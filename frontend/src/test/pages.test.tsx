import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type Spectrum, type Prediction } from "../api/client";
import { Analysis } from "../pages/Analysis";
import { Generator } from "../pages/Generator";
import { Experiments } from "../pages/Experiments";
vi.mock("../components/Chart", () => ({
  SpectrumChart: () => <div aria-label="Спектр образца" />,
  Chart: () => <div aria-label="График эксперимента" />,
}));
const spectrum: Spectrum = {
  spectrum_id: "a".repeat(32),
  wavelength_nm: [350, 800],
  intensity: [1, 2],
  clean_signal: [1, 2],
  true_labels: { H: 1 },
  parameters: { snr_db: 20 },
  reference_source: "demo_fixture",
  origin: "generated",
};
const prediction: Prediction = {
  model_id: "builtin-nnls",
  model_version: "1",
  model_origin: "reference",
  reference_source: "demo_fixture",
  scores: [{ element: "H", value: 0.8 }],
  score_kind: "nnls_coefficient_times_fit_quality",
  calibrated: false,
  thresholds: { H: 0.2 },
  detected_elements: ["H"],
  warnings: ["Демонстрационные данные"],
  processing_time_ms: 1,
  matched_reference_lines: [],
};
function wrap(element: React.ReactNode, route = "/analysis") {
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
  vi.spyOn(api, "models").mockResolvedValue([
    {
      model_id: "builtin-nnls",
      version: "1",
      model_type: "BaselineNNLS",
      ready: true,
      score_kind: "nnls",
      calibrated: false,
      data_source: "demo_fixture",
      applicability: "synthetic",
    },
  ]);
  vi.spyOn(api, "elements").mockResolvedValue({
    elements: ["H", "Na", "He", "Fe"],
    ion_stage: 1,
    reference: { source: "demo_fixture" },
    line_counts: { H: 3 },
  });
});
describe("Analysis states", () => {
  it("shows empty spectrum and loading models", () => {
    vi.spyOn(api, "models").mockImplementation(() => new Promise(() => {}));
    wrap(<Analysis />);
    expect(screen.getByText("Спектр ещё не выбран")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Загрузка");
  });
  it("shows API errors", async () => {
    vi.spyOn(api, "spectrum").mockRejectedValue(new Error("Спектр не найден"));
    wrap(<Analysis />, `/analysis?spectrum=${spectrum.spectrum_id}`);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Спектр не найден",
    );
  });
  it("renders real response contract and score, without percent probability", async () => {
    vi.spyOn(api, "spectrum").mockResolvedValue(spectrum);
    const predict = vi.spyOn(api, "predict").mockResolvedValue(prediction);
    wrap(<Analysis />, `/analysis?spectrum=${spectrum.spectrum_id}`);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Распознать элементы" }),
      ).toBeEnabled(),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Распознать элементы" }),
    );
    expect(
      await screen.findByLabelText("Результат распознавания"),
    ).toHaveTextContent("0.8000");
    expect(predict).toHaveBeenCalledWith(spectrum.spectrum_id, "builtin-nnls");
    expect(screen.getByText("Демонстрационные данные")).toBeInTheDocument();
  });
  it("permits no detections", async () => {
    vi.spyOn(api, "spectrum").mockResolvedValue(spectrum);
    vi.spyOn(api, "predict").mockResolvedValue({
      ...prediction,
      detected_elements: [],
    });
    wrap(<Analysis />, `/analysis?spectrum=${spectrum.spectrum_id}`);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Распознать элементы" }),
      ).toBeEnabled(),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Распознать элементы" }),
    );
    expect(
      await screen.findByText("Поддерживаемые элементы не обнаружены."),
    ).toBeInTheDocument();
  });
});
it("generator preserves truth separately and exposes analysis link", async () => {
  const call = vi.spyOn(api, "generate").mockResolvedValue(spectrum);
  wrap(<Generator />, "/generator");
  await userEvent.click(await screen.findByRole("button", { name: "H I" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Сгенерировать спектр" }),
  );
  expect(
    await screen.findByText("Истинный состав (генератор)"),
  ).toBeInTheDocument();
  expect(call.mock.calls[0][0]).toEqual(
    expect.objectContaining({
      elements: ["H"],
      component_weights: [1],
      seed: 42,
    }),
  );
  expect(
    screen.getByRole("link", { name: /Передать на анализ/ }),
  ).toHaveAttribute("href", `/analysis?spectrum=${spectrum.spectrum_id}`);
});
it("experiments empty state shows command, no invented charts", async () => {
  vi.spyOn(api, "experiments").mockResolvedValue([]);
  wrap(<Experiments />, "/experiments");
  expect(await screen.findByText("Экспериментов пока нет")).toBeInTheDocument();
  expect(screen.getByText("make demo")).toBeInTheDocument();
  expect(
    screen.queryByLabelText("График эксперимента"),
  ).not.toBeInTheDocument();
});
