/// <reference types="node" />
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { createHash, randomUUID, webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  api,
  ApiError,
  type Batch,
  type BatchFile,
  type BatchInput,
} from "../api/client";
import { BatchAnalysis } from "../pages/BatchAnalysis";
import { prepareBatchFile, validateSelection } from "../lib/batchFiles";
import { capability, model, prediction } from "./fixtures";

const csv = "wavelength_nm,intensity\n350,0.5\n800,1\n";
const file = (name = "valid.csv", content = csv) =>
  new File([content], name, { type: "text/csv" });
const descriptor = (name: string, content = csv) => ({
  relative_path: name,
  sha256: createHash("sha256").update(content).digest("hex"),
  size_bytes: Buffer.byteLength(content),
});
function draft(names = ["valid.csv"]): Batch {
  return {
    schema_version: 1,
    idempotency_key: "test-batch-key",
    batch_id: "b".repeat(32),
    name: "Проверка очереди",
    status: "draft",
    model_ids: ["builtin-nnls"],
    model_identities: { "builtin-nnls": "pinned-hash" },
    model_snapshots: {},
    files: names.map((name, index) => ({
      ...descriptor(name),
      file_id: String(index),
      status: "pending_upload",
      measurement_metadata: {},
    })),
    results: [],
    progress: { completed: 0, failed: 0, total: names.length },
    created_at: "2026-10-13T12:00:00Z",
    updated_at: "2026-10-13T12:00:00Z",
    cancellation_requested: false,
  };
}
function mount(id = "") {
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
      <MemoryRouter initialEntries={[id ? `/batch?batch=${id}` : "/batch"]}>
        <BatchAnalysis />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
  vi.stubGlobal("crypto", {
    randomUUID,
    subtle: {
      digest: (_algorithm: string, bytes: ArrayBuffer) =>
        webcrypto.subtle.digest("SHA-256", Buffer.from(new Uint8Array(bytes))),
    },
  });
  vi.spyOn(api, "capabilities").mockResolvedValue({
    ...capability,
    batches: { enabled: true, max_files: 200, max_models: 5 },
  });
  vi.spyOn(api, "models").mockResolvedValue([model]);
  vi.spyOn(api, "batches").mockResolvedValue([]);
});
afterEach(() => {
  vi.unstubAllGlobals();
});
async function select(files: File[]) {
  const input = screen.getByLabelText("Выбрать CSV-файлы");
  await waitFor(() => expect(input).toBeEnabled());
  await userEvent.upload(input, files);
  await screen.findByText(
    `${files.length} файлов, локальных ошибок: ${files.filter((item) => item.name === "bad.csv").length}.`,
  );
  await waitFor(() => expect(input).toBeEnabled());
}
it("keeps invalid CSV in the manifest and continues valid files without sending rejected bytes", async () => {
  let current = draft();
  vi.spyOn(api, "createBatch").mockImplementation(async (input: BatchInput) => {
    current = {
      ...draft(),
      name: input.name!,
      files: input.expected_files.map((entry, index) => ({
        ...entry,
        file_id: String(index),
        status: "pending_upload",
        measurement_metadata: {},
      })),
      progress: { completed: 0, failed: 0, total: 2 },
    };
    return current;
  });
  vi.spyOn(api, "batch").mockImplementation(async () => current);
  const accept = (path: string, fields: Partial<BatchFile>) => {
    const value = {
      ...current.files.find((item) => item.relative_path === path)!,
      ...fields,
    };
    current = {
      ...current,
      files: current.files.map((item) =>
        item.relative_path === path ? value : item,
      ),
    };
    return value;
  };
  const upload = vi
    .spyOn(api, "uploadBatchFile")
    .mockImplementation(async (_id, _file, path) =>
      accept(path, { status: "ready", point_count: 2 }),
    );
  const reject = vi
    .spyOn(api, "rejectBatchFile")
    .mockImplementation(async (_id, input) =>
      accept(input.relative_path, {
        status: "invalid",
        error_message: input.error_message,
        error_code: input.error_code,
        validation_origin: "client",
      }),
    );
  vi.spyOn(api, "startBatch").mockImplementation(
    async () =>
      (current = {
        ...current,
        status: "partial",
        progress: { completed: 2, failed: 1, total: 2 },
      }),
  );
  mount();
  await select([file(), file("bad.csv", "wave,value\n350,1\n800,2")]);
  await userEvent.click(
    screen.getByRole("button", { name: "Загрузить и запустить очередь" }),
  );
  await screen.findByText("Завершено с ошибками");
  expect(api.createBatch).toHaveBeenCalledWith(
    expect.objectContaining({
      expected_files: expect.arrayContaining([
        expect.objectContaining(descriptor("valid.csv")),
        expect.objectContaining({ relative_path: "bad.csv" }),
      ]),
    }),
  );
  expect(upload).toHaveBeenCalledOnce();
  expect(upload.mock.calls[0][1].name).toBe("valid.csv");
  expect(reject).toHaveBeenCalledWith(
    current.batch_id,
    expect.objectContaining({
      relative_path: "bad.csv",
      error_code: "invalid_csv_header",
    }),
  );
  expect(
    within(screen.getByRole("region", { name: "Текущая очередь" })).getByText(
      /Проверка браузера/,
    ),
  ).toBeInTheDocument();
});
it("resumes an interrupted intake after reselecting files and skips already accepted bytes", async () => {
  let current = draft(["first.csv", "second.csv"]);
  current.files[0].status = "ready";
  vi.spyOn(api, "batch").mockImplementation(async () => current);
  const upload = vi
    .spyOn(api, "uploadBatchFile")
    .mockImplementation(async (_id, _file, path) => {
      current = {
        ...current,
        files: current.files.map((entry) =>
          entry.relative_path === path ? { ...entry, status: "ready" } : entry,
        ),
      };
      return current.files[1];
    });
  vi.spyOn(api, "startBatch").mockImplementation(
    async () =>
      (current = {
        ...current,
        status: "completed",
        progress: { completed: 2, failed: 0, total: 2 },
      }),
  );
  mount(current.batch_id);
  await screen.findByText(/Ожидают загрузки: 1/);
  await select([file("first.csv"), file("second.csv")]);
  await userEvent.click(
    screen.getByRole("button", { name: "Продолжить загрузку" }),
  );
  await waitFor(() => expect(api.startBatch).toHaveBeenCalledOnce());
  expect(upload).toHaveBeenCalledOnce();
  expect(upload.mock.calls[0][2]).toBe("second.csv");
});
it("rejects changed content for an unfinished file and keeps the original manifest", async () => {
  const current = draft();
  vi.spyOn(api, "batch").mockResolvedValue(current);
  const upload = vi.spyOn(api, "uploadBatchFile");
  const start = vi.spyOn(api, "startBatch");
  mount(current.batch_id);
  await screen.findByText(/Ожидают загрузки/);
  await select([file("valid.csv", csv.replace("0.5", "0.7"))]);
  await userEvent.click(
    screen.getByRole("button", { name: "Продолжить загрузку" }),
  );
  expect(
    (await screen.findAllByText(/Содержимое изменилось/)).length,
  ).toBeGreaterThan(0);
  expect(upload).not.toHaveBeenCalled();
  expect(start).not.toHaveBeenCalled();
});
it("retries an uncertain create with the original idempotency key across remount", async () => {
  const create = vi
    .spyOn(api, "createBatch")
    .mockRejectedValueOnce(new ApiError("Ответ потерян", "network_error"));
  const mounted = mount();
  await select([file()]);
  await userEvent.click(
    screen.getByRole("button", { name: "Загрузить и запустить очередь" }),
  );
  await screen.findByText("Ответ потерян");
  const first = create.mock.calls[0][0];
  mounted.unmount();
  const current = draft();
  create.mockResolvedValue(current);
  vi.spyOn(api, "batch").mockResolvedValue(current);
  mount();
  await userEvent.click(
    screen.getByRole("button", { name: "Восстановить создание очереди" }),
  );
  await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
  expect(create.mock.calls[1][0]).toEqual(first);
  await screen.findByText(/Ожидают загрузки: 1/);
});
it("shows independent model failure and provenance, exports and resumes the same queue", async () => {
  const current: Batch = {
    ...draft(),
    status: "partial",
    progress: { completed: 2, failed: 1, total: 2 },
    results: [
      {
        file_id: "0",
        model_id: "builtin-nnls",
        model_identity: "pinned",
        cache_hit: false,
        status: "completed",
        prediction_id: "existing",
        prediction,
      },
      {
        file_id: "0",
        model_id: "second",
        model_identity: "pinned-2",
        cache_hit: false,
        status: "failed",
        error_code: "model_changed",
        error_message: "Артефакт изменился",
      },
    ],
  };
  vi.spyOn(api, "batch").mockResolvedValue(current);
  const resume = vi
    .spyOn(api, "resumeBatch")
    .mockResolvedValue({ ...current, status: "completed" });
  const exportFile = vi.spyOn(api, "export").mockResolvedValue(undefined);
  mount(current.batch_id);
  expect(await screen.findByText("Артефакт изменился")).toBeInTheDocument();
  expect(screen.getByText("v1 · demo_fixture")).toBeInTheDocument();
  await userEvent.click(screen.getByText("Оценки и пороги"));
  expect(
    screen.getByText("Не является вероятностью", { exact: false }),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Сводный отчёт ZIP" }),
  );
  expect(exportFile).toHaveBeenCalledWith("batches", current.batch_id, "zip");
  await userEvent.click(
    screen.getByRole("button", { name: "Повторить незавершённые" }),
  );
  expect(resume).toHaveBeenCalledWith(current.batch_id);
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Повторить незавершённые" }),
    ).not.toBeInTheDocument(),
  );
  expect(screen.getByText("v1 · demo_fixture")).toBeInTheDocument();
});
it("does not allocate bytes for an oversized file but retains its rejection descriptor", async () => {
  const oversized = file();
  const read = vi.fn();
  Object.defineProperty(oversized, "arrayBuffer", { value: read });
  const result = await prepareBatchFile(oversized, {
    ...capability,
    spectrum: { ...capability.spectrum, max_upload_bytes: 1 },
  });
  expect(result.sha256).toBeNull();
  expect(result.error?.code).toBe("file_too_large");
  expect(read).not.toHaveBeenCalled();
});
it("refuses ambiguous duplicate names and file-count overflow before creation", () => {
  expect(() => validateSelection([file(), file()], 200)).toThrow(
    "одинаковым именем",
  );
  expect(() => validateSelection([file("a.csv"), file("b.csv")], 1)).toThrow(
    "до 1 файлов",
  );
});

it("continues after one network failure and retries only the missing upload", async () => {
  let current = draft(["first.csv", "second.csv"]);
  let firstAttempt = true;
  vi.spyOn(api, "batch").mockImplementation(async () => current);
  const upload = vi
    .spyOn(api, "uploadBatchFile")
    .mockImplementation(async (_id, _file, path) => {
      if (path === "first.csv" && firstAttempt) {
        firstAttempt = false;
        throw new ApiError("Соединение прервано", "network_error");
      }
      current = {
        ...current,
        files: current.files.map((entry) =>
          entry.relative_path === path ? { ...entry, status: "ready" } : entry,
        ),
      };
      return current.files.find((entry) => entry.relative_path === path)!;
    });
  const start = vi.spyOn(api, "startBatch").mockImplementation(
    async () =>
      (current = {
        ...current,
        status: "completed",
        progress: { completed: 2, failed: 0, total: 2 },
      }),
  );
  mount(current.batch_id);
  await screen.findByText(/Ожидают загрузки/);
  await select([file("first.csv"), file("second.csv")]);
  await userEvent.click(
    screen.getByRole("button", { name: "Продолжить загрузку" }),
  );
  await screen.findByText(/Ожидают загрузки: 1/);
  expect(await screen.findAllByText("Соединение прервано")).toHaveLength(2);
  expect(start).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Продолжить загрузку" }),
    ).toBeEnabled(),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Продолжить загрузку" }),
  );
  await waitFor(() => expect(start).toHaveBeenCalledOnce());
  expect(upload.mock.calls.map((call) => call[2])).toEqual([
    "first.csv",
    "second.csv",
    "first.csv",
  ]);
});

it("folder selection ignores README and accepts uppercase CSV without blocking valid spectra", async () => {
  let current = draft(["SAMPLE.CSV"]);
  const create = vi.spyOn(api, "createBatch").mockResolvedValue(current);
  vi.spyOn(api, "batch").mockImplementation(async () => current);
  const upload = vi
    .spyOn(api, "uploadBatchFile")
    .mockImplementation(async () => {
      current = {
        ...current,
        files: [{ ...current.files[0], status: "ready" }],
      };
      return current.files[0];
    });
  vi.spyOn(api, "startBatch").mockImplementation(
    async () =>
      (current = {
        ...current,
        status: "completed",
        progress: { completed: 1, failed: 0, total: 1 },
      }),
  );
  const reject = vi.spyOn(api, "rejectBatchFile");
  mount();
  const input = screen.getByLabelText("Выбрать папку CSV");
  await waitFor(() => expect(input).toBeEnabled());
  await userEvent.upload(input, [
    file("SAMPLE.CSV"),
    new File(["notes"], "README.txt", { type: "text/plain" }),
  ]);
  await screen.findByText("1 файлов, локальных ошибок: 0.");
  expect(
    screen.getByText(/Пропущено файлов другого типа: 1/),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Загрузить и запустить очередь" }),
  );
  await waitFor(() => expect(api.startBatch).toHaveBeenCalledOnce());
  expect(create.mock.calls[0][0].expected_files).toEqual([
    descriptor("SAMPLE.CSV"),
  ]);
  expect(upload).toHaveBeenCalledOnce();
  expect(reject).not.toHaveBeenCalled();
});

it("does not create a queue for a folder without CSV files", async () => {
  const create = vi.spyOn(api, "createBatch");
  mount();
  const input = screen.getByLabelText("Выбрать папку CSV");
  await waitFor(() => expect(input).toBeEnabled());
  await userEvent.upload(
    input,
    new File(["notes"], "README.txt", { type: "text/plain" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "нет файлов .csv или .CSV",
  );
  expect(
    screen.getByRole("button", { name: "Загрузить и запустить очередь" }),
  ).toBeDisabled();
  expect(create).not.toHaveBeenCalled();
});
