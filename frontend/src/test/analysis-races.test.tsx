import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { api, type Prediction } from "../api/client";
import { Analysis } from "../pages/Analysis";
import { capability, model, prediction, spectrum } from "./fixtures";
vi.mock("../components/Chart", () => ({ SpectrumChart: () => <div /> }));
beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "capabilities").mockResolvedValue(capability);
  vi.spyOn(api, "models").mockResolvedValue([
    model,
    { ...model, model_id: "second", version: "2", score_kind: "raw_vote" },
  ]);
  vi.spyOn(api, "spectra").mockResolvedValue({ items: [], next_cursor: null });
  vi.spyOn(api, "spectrum").mockImplementation(async (id) => ({
    ...spectrum,
    spectrum_id: id,
  }));
});
function Harness() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(`/analysis?spectrum=${"b".repeat(32)}`)}>
        Другой спектр
      </button>
      <Analysis />
    </>
  );
}
function mount() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter
        initialEntries={[`/analysis?spectrum=${spectrum.spectrum_id}`]}
      >
        <Harness />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
it("cancels stale prediction on spectrum change and does not show its late answer", async () => {
  let resolve!: (value: Prediction) => void;
  const call = vi.spyOn(api, "predict").mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  mount();
  const button = screen.getByRole("button", { name: "Распознать элементы" });
  await waitFor(() => expect(button).toBeEnabled());
  await userEvent.click(button);
  await waitFor(() => expect(call).toHaveBeenCalledOnce());
  const signal = call.mock.calls[0][2]!.signal!;
  await userEvent.click(screen.getByRole("button", { name: "Другой спектр" }));
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(prediction));
  expect(
    screen.queryByLabelText("Результат распознавания"),
  ).not.toBeInTheDocument();
});
it("changing model aborts its request and hides a late result", async () => {
  let resolve!: (value: Prediction) => void;
  const call = vi.spyOn(api, "predict").mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  mount();
  const button = screen.getByRole("button", { name: "Распознать элементы" });
  await waitFor(() => expect(button).toBeEnabled());
  await userEvent.click(button);
  await userEvent.selectOptions(screen.getByLabelText("Метод"), "second");
  expect(call.mock.calls[0][2]!.signal!.aborted).toBe(true);
  await act(async () => resolve(prediction));
  expect(screen.queryByText("0.8000")).not.toBeInTheDocument();
});
it("keeps successful model result if another model fails, with independent retry", async () => {
  const predict = vi
    .spyOn(api, "predict")
    .mockImplementation(async (_id, name) => {
      if (name === "second") throw new Error("Second model failed");
      return prediction;
    });
  mount();
  await userEvent.click(screen.getByText("Сравнить несколько моделей"));
  await userEvent.click(screen.getByRole("checkbox", { name: "second · v2" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Распознать элементы" }),
  );
  expect(await screen.findByText("0.8000")).toBeInTheDocument();
  expect(await screen.findByText("Second model failed")).toBeInTheDocument();
  predict.mockResolvedValue({ ...prediction, model_id: "second" });
  await userEvent.click(
    screen.getByRole("button", { name: "Повторить second" }),
  );
  await waitFor(() =>
    expect(screen.queryByText("Second model failed")).not.toBeInTheDocument(),
  );
});
it("invalid upload never sends POST and reports the error beside the file", async () => {
  const upload = vi.spyOn(api, "upload");
  mount();
  const input = screen.getByLabelText("Загрузить CSV");
  await waitFor(() => expect(input).toBeEnabled());
  await userEvent.upload(
    input,
    new File(["wave,value\n350,NaN\n800,1"], "bad.csv", { type: "text/csv" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "CSV должен начинаться",
  );
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(upload).not.toHaveBeenCalled();
});
