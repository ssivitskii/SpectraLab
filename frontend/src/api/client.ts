import type { components } from "./schema";
export type Spectrum = components["schemas"]["SpectrumResponse"] & {
  units?: string;
  wavelength_medium?: string;
};
export type Prediction = components["schemas"]["PredictionResponse"];
export type GenerateInput = components["schemas"]["GenerateSpectrumRequest"];
export type ModelInfo = components["schemas"]["ModelInfo"];
export type ElementsInfo = components["schemas"]["ElementsInfo"];
export type ExperimentSummary = components["schemas"]["ExperimentSummary"];
export type Metrics = components["schemas"]["ExperimentMetrics"];
export type Estimate = components["schemas"]["AggregateStatistic"];
export type Aggregate = components["schemas"]["SeriesAggregate"];
export type Experiment = components["schemas"]["ExperimentDetail"];
export type MeasurementMetadata = components["schemas"]["MeasurementMetadata"];
export type SpectrumSummary = components["schemas"]["SpectrumSummary"];
export type Page<T> = { items: T[]; next_cursor: string | null };
export type Capabilities = components["schemas"]["ServiceCapabilities"];
export type Job = components["schemas"]["JobResponse"];
export type BatchFileDescriptor = components["schemas"]["BatchFileDescriptor"];
export type BatchFile = components["schemas"]["BatchFile"];
export type BatchResult = components["schemas"]["BatchResult"];
export type Batch = components["schemas"]["BatchResponse"];
export type BatchInput = components["schemas"]["BatchRequest"];
export type RequestOptions = { signal?: AbortSignal; timeoutMs?: number };
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: unknown;
  constructor(message: string, code: string, status = 0, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}
export async function request<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {},
  binary = false,
): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs ?? 30_000);
  try {
    const response = await fetch(`/api/v1${path}`, {
      ...init,
      signal: controller.signal,
    });
    if (binary && response.ok) return (await response.blob()) as T;
    const raw = await response.text();
    let data: unknown;
    try {
      data = JSON.parse(raw);
    } catch {
      throw new ApiError(
        response.ok
          ? "Сервис вернул некорректный ответ. Повторите запрос."
          : `Ошибка шлюза HTTP ${response.status}. Повторите запрос.`,
        "invalid_response",
        response.status,
      );
    }
    if (!response.ok) {
      const body = data as {
        error?: { message?: string; code?: string; details?: unknown };
      };
      throw new ApiError(
        body.error?.message ?? `Ошибка HTTP ${response.status}`,
        body.error?.code ?? "http_error",
        response.status,
        body.error?.details,
      );
    }
    return data as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted)
      throw new ApiError(
        timedOut
          ? "Сервис не ответил вовремя. Повторите запрос."
          : "Запрос отменён.",
        timedOut ? "timeout" : "cancelled",
      );
    throw new ApiError(
      "Не удалось связаться с сервисом. Проверьте соединение и повторите запрос.",
      "network_error",
    );
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", abort);
  }
}
const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const queryString = (values: Record<string, string | number | undefined>) => {
  const search = new URLSearchParams();
  Object.entries(values).forEach(([k, v]) => {
    if (v !== undefined && v !== "") search.set(k, String(v));
  });
  return search.toString();
};
export const api = {
  capabilities: (options?: RequestOptions) =>
    request<Capabilities>("/capabilities", {}, options),
  elements: (options?: RequestOptions) =>
    request<ElementsInfo>("/elements", {}, options),
  models: (options?: RequestOptions) =>
    request<ModelInfo[]>("/models", {}, options),
  spectrum: (id: string, options?: RequestOptions) =>
    request<Spectrum>(`/spectra/${encodeURIComponent(id)}`, {}, options),
  spectra: (cursor?: string, options?: RequestOptions) =>
    request<Page<SpectrumSummary>>(
      `/spectra?${queryString({ limit: 20, cursor })}`,
      {},
      options,
    ),
  metadata: (id: string, value: MeasurementMetadata) =>
    request<Spectrum>(`/spectra/${encodeURIComponent(id)}/metadata`, {
      ...json(value),
      method: "PATCH",
    }),
  generate: (input: GenerateInput) =>
    request<Spectrum>("/spectra/generate", json(input)),
  upload: (
    file: File,
    medium: string,
    units: string,
    metadata: MeasurementMetadata = {},
  ) => {
    const body = new FormData();
    body.append("file", file);
    body.append("units", units);
    body.append("wavelength_medium", medium);
    body.append("measurement_metadata", JSON.stringify(metadata));
    return request<Spectrum>("/spectra/upload", { method: "POST", body });
  },
  predict: (spectrum_id: string, model_id: string, options?: RequestOptions) =>
    request<Prediction>(
      "/predict",
      json({
        input_type: "spectrum_id",
        spectrum_id,
        model_id,
      } satisfies components["schemas"]["PredictByIdRequest"]),
      options,
    ),
  experiments: (options?: RequestOptions) =>
    request<ExperimentSummary[]>("/experiments", {}, options),
  experimentCatalog: (
    filters: Record<string, string | undefined>,
    options?: RequestOptions,
  ) =>
    request<Page<ExperimentSummary>>(
      `/experiments/catalog?${queryString({ limit: 20, ...filters })}`,
      {},
      options,
    ),
  experiment: (id: string, options?: RequestOptions) =>
    request<Experiment>(`/experiments/${encodeURIComponent(id)}`, {}, options),
  jobs: (options?: RequestOptions) => request<Job[]>("/jobs", {}, options),
  job: (id: string, options?: RequestOptions) =>
    request<Job>(`/jobs/${encodeURIComponent(id)}`, {}, options),
  createJob: (input: {
    seeds: number[];
    config_name: string;
    idempotency_key: string;
  }) => request<Job>("/jobs", json(input)),
  cancelJob: (id: string) =>
    request<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, json({})),
  batches: (options?: RequestOptions) =>
    request<Batch[]>("/batches", {}, options),
  batch: (id: string, options?: RequestOptions) =>
    request<Batch>(`/batches/${encodeURIComponent(id)}`, {}, options),
  createBatch: (input: BatchInput) => request<Batch>("/batches", json(input)),
  uploadBatchFile: (
    id: string,
    file: File,
    relativePath: string,
    capabilities: Capabilities,
    options?: RequestOptions,
  ) => {
    const body = new FormData();
    body.append("file", file);
    body.append("relative_path", relativePath);
    body.append("units", capabilities.spectrum.units);
    body.append("wavelength_medium", capabilities.spectrum.wavelength_medium);
    return request<BatchFile>(
      `/batches/${encodeURIComponent(id)}/files`,
      { method: "POST", body },
      options,
    );
  },
  rejectBatchFile: (
    id: string,
    input: BatchFileDescriptor & { error_code: string; error_message: string },
  ) =>
    request<BatchFile>(
      `/batches/${encodeURIComponent(id)}/rejections`,
      json(input),
    ),
  startBatch: (id: string) =>
    request<Batch>(`/batches/${encodeURIComponent(id)}/start`, json({})),
  cancelBatch: (id: string) =>
    request<Batch>(`/batches/${encodeURIComponent(id)}/cancel`, json({})),
  resumeBatch: (id: string) =>
    request<Batch>(`/batches/${encodeURIComponent(id)}/resume`, json({})),
  export: async (
    kind: "spectra" | "predictions" | "experiments" | "batches",
    id: string,
    format = "zip",
  ) => {
    const blob = await request<Blob>(
      `/${kind}/${encodeURIComponent(id)}/export?${queryString({ format })}`,
      {},
      {},
      true,
    );
    saveBlob(`spectralab-${id}.${format}`, blob);
  },
};
function saveBlob(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function download(name: string, content: string, type: string) {
  saveBlob(name, new Blob([content], { type }));
}
export function validationMessages(
  error: Error | null | undefined,
): { field: string; message: string }[] {
  if (!(error instanceof ApiError) || !Array.isArray(error.details)) return [];
  return error.details.map(
    (item: { loc?: (string | number)[]; msg?: string }) => ({
      field: item.loc?.filter((p) => p !== "body").join(".") ?? "file",
      message: item.msg ?? "Некорректное значение",
    }),
  );
}
export async function validateUpload(
  file: File,
  medium: string,
  capability: Capabilities,
): Promise<void> {
  const limits = capability.spectrum;
  if (medium !== limits.wavelength_medium)
    throw new ApiError(
      `Среда ${medium} не поддерживается. Выберите ${limits.wavelength_medium}.`,
      "unsupported_medium",
    );
  if (file.size > limits.max_upload_bytes)
    throw new ApiError(
      `Размер CSV превышает ${limits.max_upload_bytes.toLocaleString("ru")} байт.`,
      "file_too_large",
    );
  if (!file.name.toLowerCase().endsWith(".csv"))
    throw new ApiError("Выберите файл с расширением .csv.", "invalid_csv");
  const raw =
    typeof file.text === "function"
      ? await file.text()
      : await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result));
          reader.onerror = () =>
            reject(
              new ApiError("Не удалось прочитать файл.", "file_read_error"),
            );
          reader.readAsText(file);
        });
  const rows = raw
    .replace(/^\uFEFF/, "")
    .trim()
    .split(/\r?\n/);
  const headers = rows
    .shift()
    ?.split(",")
    .map((s) => s.trim());
  if (headers?.join(",") !== limits.csv_columns.join(","))
    throw new ApiError(
      `CSV должен начинаться с ${limits.csv_columns.join(",")}.`,
      "invalid_csv_header",
    );
  if (rows.length < 2 || rows.length > limits.max_points)
    throw new ApiError(
      `CSV должен содержать от 2 до ${limits.max_points.toLocaleString("ru")} точек.`,
      "invalid_point_count",
    );
  for (const [index, row] of rows.entries()) {
    const cells = row.split(",");
    if (
      cells.length !== headers.length ||
      cells.some((value) => !value.trim() || !Number.isFinite(Number(value)))
    )
      throw new ApiError(
        `Строка ${index + 2}: нужны конечные числовые значения.`,
        "invalid_csv_value",
      );
  }
}
