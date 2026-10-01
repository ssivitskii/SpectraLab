import type { components } from "./schema";
export type Spectrum = components["schemas"]["SpectrumResponse"];
export type Prediction = components["schemas"]["PredictionResponse"];
export type GenerateInput = components["schemas"]["GenerateSpectrumRequest"];
export type ModelInfo = components["schemas"]["ModelInfo"];
export type ElementsInfo = components["schemas"]["ElementsInfo"];
export type ExperimentSummary = components["schemas"]["ExperimentSummary"];
export type Metrics = components["schemas"]["ExperimentMetrics"];
export type Experiment = components["schemas"]["ExperimentDetail"];
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.error?.message ?? `Ошибка HTTP ${response.status}`);
  return data as T;
}
export const api = {
  elements: () => request<ElementsInfo>("/elements"),
  models: () => request<ModelInfo[]>("/models"),
  spectrum: (id: string) =>
    request<Spectrum>(`/spectra/${encodeURIComponent(id)}`),
  generate: (input: GenerateInput) =>
    request<Spectrum>("/spectra/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  upload: (file: File, medium: string) => {
    const body = new FormData();
    body.append("file", file);
    body.append("units", "nm");
    body.append("wavelength_medium", medium);
    return request<Spectrum>("/spectra/upload", { method: "POST", body });
  },
  predict: (spectrum_id: string, model_id: string) =>
    request<Prediction>("/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input_type: "spectrum_id",
        spectrum_id,
        model_id,
      } satisfies components["schemas"]["PredictByIdRequest"]),
    }),
  experiments: () => request<ExperimentSummary[]>("/experiments"),
  experiment: (id: string) =>
    request<Experiment>(`/experiments/${encodeURIComponent(id)}`),
};
export function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
