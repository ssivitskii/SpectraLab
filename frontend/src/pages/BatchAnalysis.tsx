import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  type Batch,
  type BatchFile,
  type BatchInput,
  type BatchResult,
} from "../api/client";
import { ErrorNotice, Loading } from "../components/Feedback";
import {
  batchFilePath,
  prepareBatchFile,
  validateSelection,
  type PreparedBatchFile,
} from "../lib/batchFiles";

const statusNames: Record<string, string> = {
  draft: "Подготовка файлов",
  pending_upload: "Ожидает загрузки",
  ready: "Проверен",
  invalid: "Файл отклонён",
  queued: "В очереди",
  pending: "Ожидает анализа",
  running: "Выполняется",
  completed: "Завершено",
  partial: "Завершено с ошибками",
  failed: "Ошибка",
  cancelled: "Отменено",
};
const draftStorageKey = "spectralab.batch-create.v1";
function readPendingCreate(): BatchInput | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(draftStorageKey) ?? "null");
    return value &&
      Array.isArray(value.expected_files) &&
      Array.isArray(value.model_ids) &&
      typeof value.idempotency_key === "string"
      ? value
      : null;
  } catch {
    return null;
  }
}
const describeError = (error: unknown) =>
  error instanceof Error
    ? error
    : new Error("Не удалось обработать файл. Повторите загрузку.");
export function BatchAnalysis() {
  const [search, setSearch] = useSearchParams();
  const id = search.get("batch") ?? "";
  const client = useQueryClient();
  const [selectedModels, setSelectedModels] = useState(["builtin-nnls"]),
    [name, setName] = useState("Папка спектров");
  const [prepared, setPrepared] = useState<PreparedBatchFile[]>([]),
    [selectionError, setSelectionError] = useState<Error | null>(null),
    [recovery, setRecovery] = useState(readPendingCreate);
  const [localProgress, setLocalProgress] = useState({
      phase: "idle",
      completed: 0,
      total: 0,
    }),
    [intakeErrors, setIntakeErrors] = useState<Record<string, string>>({});
  const [page, setPage] = useState(0);
  const [ignoredFiles, setIgnoredFiles] = useState(0);
  const preparationVersion = useRef(0),
    pause = useRef(false),
    requestBody = useRef<BatchInput | null>(null),
    active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      preparationVersion.current += 1;
      pause.current = true;
    };
  }, []);
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: ({ signal }) => api.capabilities({ signal }),
  });
  const models = useQuery({
    queryKey: ["models"],
    queryFn: ({ signal }) => api.models({ signal }),
  });
  const history = useQuery({
    queryKey: ["batches"],
    queryFn: ({ signal }) => api.batches({ signal }),
    refetchInterval: (query) =>
      query.state.data?.some((entry) =>
        ["queued", "running"].includes(entry.status),
      )
        ? 2000
        : false,
  });
  const detail = useQuery({
    queryKey: ["batch", id],
    queryFn: ({ signal }) => api.batch(id, { signal }),
    enabled: !!id,
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.status ?? "")
        ? 1000
        : false,
  });
  const batch = detail.data;
  const limits = capabilities.data?.batches as
    | {
        max_files?: number;
        max_models?: number;
        max_total_upload_bytes?: number;
        enabled?: boolean;
      }
    | undefined;
  const readyModels = selectedModels.filter((model) =>
    models.data?.some((item) => item.model_id === model && item.ready),
  );
  const busy = localProgress.phase !== "idle";
  const storeBatch = (value: Batch) => {
    client.setQueryData(["batch", value.batch_id], value);
    void client.invalidateQueries({ queryKey: ["batches"] });
  };
  const selectFiles = async (selected: File[], directory = false) => {
    if (!capabilities.data || busy) return;
    const files = directory
      ? selected.filter((file) => file.name.toLowerCase().endsWith(".csv"))
      : selected;
    setIgnoredFiles(selected.length - files.length);
    const version = ++preparationVersion.current;
    requestBody.current = null;
    setSelectionError(null);
    setPrepared([]);
    setIntakeErrors({});
    try {
      if (directory && !files.length)
        throw new ApiError(
          "В выбранной папке нет файлов .csv или .CSV. Выберите папку со спектрами.",
          "empty_csv_folder",
        );
      validateSelection(files, limits?.max_files ?? 200);
      setLocalProgress({
        phase: "checking",
        completed: 0,
        total: files.length,
      });
      const values: PreparedBatchFile[] = [];
      for (const file of [...files].sort((a, b) =>
        batchFilePath(a).localeCompare(batchFilePath(b)),
      )) {
        const value = await prepareBatchFile(file, capabilities.data);
        if (version !== preparationVersion.current || !active.current) return;
        values.push(value);
        setPrepared([...values]);
        setLocalProgress({
          phase: "checking",
          completed: values.length,
          total: files.length,
        });
      }
      const validBytes = values
        .filter((entry) => !entry.error)
        .reduce((total, entry) => total + entry.size_bytes, 0);
      if (
        !id &&
        limits?.max_total_upload_bytes &&
        validBytes > limits.max_total_upload_bytes
      )
        throw new ApiError(
          `Суммарный размер проверенных CSV превышает ${limits.max_total_upload_bytes.toLocaleString("ru")} байт. Разделите файлы на несколько очередей.`,
          "batch_size_limit",
        );
    } catch (error) {
      if (active.current) setSelectionError(describeError(error));
    } finally {
      if (version === preparationVersion.current && active.current)
        setLocalProgress((value) => ({ ...value, phase: "idle" }));
    }
  };
  const intake = useMutation({
    mutationFn: async (target: Batch) => {
      if (!capabilities.data)
        throw new Error("Возможности сервиса ещё не загружены.");
      pause.current = false;
      setIntakeErrors({});
      setSelectionError(null);
      setLocalProgress({
        phase: "uploading",
        completed: 0,
        total: prepared.length,
      });
      let completed = 0;
      try {
        for (const entry of prepared) {
          if (pause.current || !active.current) break;
          const expected = target.files.find(
            (file) => file.relative_path === entry.relative_path,
          );
          if (!expected) {
            setIntakeErrors((current) => ({
              ...current,
              [entry.relative_path]:
                "Файл отсутствует в исходном составе очереди.",
            }));
            continue;
          }
          if (
            expected.sha256 !== entry.sha256 ||
            expected.size_bytes !== entry.size_bytes
          ) {
            setIntakeErrors((current) => ({
              ...current,
              [entry.relative_path]:
                "Содержимое изменилось: checksum или размер не совпадает с исходным файлом. Создайте новую очередь.",
            }));
            continue;
          }
          if (expected.status !== "pending_upload") {
            completed += 1;
            setLocalProgress((value) => ({ ...value, completed }));
            continue;
          }
          try {
            if (entry.error)
              await api.rejectBatchFile(target.batch_id, {
                relative_path: entry.relative_path,
                sha256: entry.sha256,
                size_bytes: entry.size_bytes,
                error_code: entry.error.code,
                error_message: entry.error.message,
              });
            else
              await api.uploadBatchFile(
                target.batch_id,
                entry.file,
                entry.relative_path,
                capabilities.data,
              );
          } catch (error) {
            setIntakeErrors((current) => ({
              ...current,
              [entry.relative_path]: describeError(error).message,
            }));
          }
          completed += 1;
          if (active.current)
            setLocalProgress((value) => ({ ...value, completed }));
        }
        const current = await api.batch(target.batch_id);
        storeBatch(current);
        if (
          !pause.current &&
          active.current &&
          current.files.every((file) => file.status !== "pending_upload")
        ) {
          const queued = await api.startBatch(target.batch_id);
          storeBatch(queued);
        }
      } finally {
        if (active.current)
          setLocalProgress((value) => ({ ...value, phase: "idle" }));
      }
    },
  });
  const create = useMutation({
    mutationFn: async (restore?: BatchInput) => {
      const body = restore ??
        requestBody.current ?? {
          name: name.trim() || "Папка спектров",
          model_ids: readyModels,
          idempotency_key: crypto.randomUUID(),
          expected_files: prepared.map(
            ({ relative_path, sha256, size_bytes }) => ({
              relative_path,
              sha256,
              size_bytes,
            }),
          ),
        };
      requestBody.current = body;
      setRecovery(body);
      try {
        sessionStorage.setItem(draftStorageKey, JSON.stringify(body));
      } catch {
        /* in-memory retries retain the same idempotency key */
      }
      return api.createBatch(body);
    },
    onSuccess: (value) => {
      storeBatch(value);
      if (!active.current) return;
      setSearch({ batch: value.batch_id });
      setRecovery(null);
      try {
        sessionStorage.removeItem(draftStorageKey);
      } catch {
        /* URL and server history remain authoritative */
      }
      if (prepared.length && value.status === "draft") intake.mutate(value);
    },
  });
  const action = useMutation({
    mutationFn: ({
      action,
      batchId,
    }: {
      action: "start" | "cancel" | "resume";
      batchId: string;
    }) =>
      action === "start"
        ? api.startBatch(batchId)
        : action === "cancel"
          ? api.cancelBatch(batchId)
          : api.resumeBatch(batchId),
    onSuccess: storeBatch,
  });
  const exported = useMutation({
    mutationFn: (format: string) => api.export("batches", id, format),
  });
  const allBusy = busy || create.isPending || intake.isPending;
  const beginNew = () => {
    setSearch({});
    setPrepared([]);
    setIgnoredFiles(0);
    setPage(0);
    setIntakeErrors({});
    setSelectionError(null);
    requestBody.current = null;
    create.reset();
    intake.reset();
    action.reset();
  };
  const choose = (batchId: string) => {
    setSearch({ batch: batchId });
    setPrepared([]);
    setIgnoredFiles(0);
    setPage(0);
    setIntakeErrors({});
    setSelectionError(null);
    create.reset();
    intake.reset();
    action.reset();
  };
  const fileInputs = (
    <div className="actions batch-file-inputs">
      <label className="button">
        Выбрать папку
        <input
          aria-label="Выбрать папку CSV"
          type="file"
          multiple
          {...{ webkitdirectory: "" }}
          disabled={allBusy || !capabilities.data}
          onChange={(event) => {
            void selectFiles(Array.from(event.target.files ?? []), true);
            event.target.value = "";
          }}
        />
      </label>
      <label className="button">
        Выбрать CSV-файлы
        <input
          aria-label="Выбрать CSV-файлы"
          type="file"
          accept=".csv,text/csv"
          multiple
          disabled={allBusy || !capabilities.data}
          onChange={(event) => {
            void selectFiles(Array.from(event.target.files ?? []));
            event.target.value = "";
          }}
        />
      </label>
    </div>
  );
  return (
    <>
      <header className="page-title">
        <div>
          <h1>Пакетный анализ</h1>
          <p>
            Общая очередь для папки спектров с отдельным результатом каждого
            файла и модели.
          </p>
        </div>
        {id && (
          <button disabled={allBusy} onClick={beginNew}>
            Новая очередь
          </button>
        )}
      </header>
      <ErrorNotice error={capabilities.error} />
      <ErrorNotice error={models.error} />
      <ErrorNotice error={detail.error} />
      <div className="workspace">
        <aside className="panel controls">
          <h2>Сохранённые очереди</h2>
          <ErrorNotice error={history.error} />
          {history.isPending && <Loading />}
          {history.data?.length === 0 && (
            <p className="muted">
              Очередей пока нет. Выберите папку или несколько файлов CSV.
            </p>
          )}
          <button
            disabled={allBusy}
            onClick={() => {
              void history.refetch();
              if (id) void detail.refetch();
            }}
          >
            Обновить очереди
          </button>
          {history.data?.map((item) => (
            <button
              className={`run-choice ${item.batch_id === id ? "active" : ""}`}
              key={item.batch_id}
              disabled={allBusy}
              onClick={() => choose(item.batch_id)}
            >
              <strong>{item.name}</strong>
              <small>
                {statusNames[item.status] ?? item.status} ·{" "}
                {new Date(item.created_at).toLocaleString("ru")}
              </small>
            </button>
          ))}
        </aside>
        <div className="main-column">
          {!id && (
            <section className="panel">
              <h2>Подготовить очередь</h2>
              <p className="muted">
                Файлы выбираются в браузере; путь к папке на сервере не нужен.
                Некорректные CSV попадут в отчёт как ошибки, остальные продолжат
                обработку.
              </p>
              <div className="metadata-form">
                <label>
                  Название очереди
                  <input
                    maxLength={200}
                    value={name}
                    disabled={allBusy}
                    onChange={(e) => {
                      setName(e.target.value);
                      requestBody.current = null;
                    }}
                  />
                </label>
              </div>
              <fieldset className="batch-models">
                <legend>Модели для каждого спектра</legend>
                {models.data?.map((model) => (
                  <label className="check" key={model.model_id}>
                    <input
                      type="checkbox"
                      checked={selectedModels.includes(model.model_id)}
                      disabled={
                        !model.ready ||
                        allBusy ||
                        (!selectedModels.includes(model.model_id) &&
                          selectedModels.length >= (limits?.max_models ?? 5))
                      }
                      onChange={(event) => {
                        setSelectedModels((current) =>
                          event.target.checked
                            ? [...current, model.model_id]
                            : current.filter((id) => id !== model.model_id),
                        );
                        requestBody.current = null;
                      }}
                    />
                    {model.model_id} · v{model.version ?? "—"}
                    {!model.ready && (
                      <small>{model.unavailable_reason ?? "Недоступна"}</small>
                    )}
                  </label>
                ))}
              </fieldset>
              {fileInputs}
              {capabilities.data && (
                <p className="muted">
                  До {limits?.max_files ?? 200} файлов; каждый — до{" "}
                  {(
                    capabilities.data.spectrum.max_upload_bytes / 1_000_000
                  ).toLocaleString("ru")}{" "}
                  MB, {capabilities.data.spectrum.wavelength_medium},{" "}
                  {capabilities.data.spectrum.units}.
                  {limits?.max_total_upload_bytes
                    ? ` Общий объём принятых CSV — до ${(limits.max_total_upload_bytes / 1_000_000).toLocaleString("ru")} MB.`
                    : ""}{" "}
                  Изменённый файл требует новой очереди.
                </p>
              )}
              {recovery && !prepared.length && (
                <div className="notice">
                  <span>
                    Есть подготовленный запрос создания очереди. Его можно
                    безопасно повторить с тем же ключом.
                  </span>
                  <button
                    disabled={create.isPending}
                    onClick={() => create.mutate(recovery)}
                  >
                    Восстановить создание очереди
                  </button>
                </div>
              )}
              <button
                className="primary"
                disabled={
                  !prepared.length ||
                  !readyModels.length ||
                  allBusy ||
                  !!selectionError ||
                  limits?.enabled === false
                }
                onClick={() => create.mutate(undefined)}
              >
                Загрузить и запустить очередь
              </button>
              {limits?.enabled === false && (
                <p>Пакетная обработка отключена на этом сервисе.</p>
              )}
              <ErrorNotice error={create.error} />
            </section>
          )}
          {id && detail.isPending && <Loading>Загрузка очереди…</Loading>}
          {batch && (
            <section className="panel" aria-label="Текущая очередь">
              <div className="panel-heading">
                <h2>{batch.name}</h2>
                <span className="tag">
                  {statusNames[batch.status] ?? batch.status}
                </span>
              </div>
              <p className="muted">
                ID: <code>{batch.batch_id}</code> · {batch.model_ids.join(", ")}
              </p>
              <div className="batch-progress" aria-live="polite">
                <p>
                  Обработано: {batch.progress.completed} · из них ошибок:{" "}
                  {batch.progress.failed} · всего результатов:{" "}
                  {batch.progress.total}
                </p>
                <progress
                  aria-label="Готовые результаты очереди"
                  value={batch.progress.completed}
                  max={batch.progress.total || 1}
                />
              </div>
              {batch.status === "draft" && (
                <>
                  <p>
                    Ожидают загрузки:{" "}
                    {
                      batch.files.filter(
                        (file) => file.status === "pending_upload",
                      ).length
                    }
                    . После обновления страницы повторно выберите ту же папку:
                    браузер не сохраняет доступ к файлам. Принятые файлы не
                    загружаются заново.
                  </p>
                  {fileInputs}
                  <div className="actions">
                    <button
                      disabled={!prepared.length || allBusy}
                      onClick={() => intake.mutate(batch)}
                    >
                      Продолжить загрузку
                    </button>
                    {batch.files.every(
                      (file) => file.status !== "pending_upload",
                    ) && (
                      <button
                        disabled={allBusy || action.isPending}
                        onClick={() =>
                          action.mutate({ action: "start", batchId: id })
                        }
                      >
                        Запустить обработку
                      </button>
                    )}
                  </div>
                </>
              )}
              {["queued", "running"].includes(batch.status) && (
                <p className="muted">
                  Задание выполняет отдельный worker. Эта страница может быть
                  закрыта; очередь остаётся на сервере.
                </p>
              )}
              {batch.error_message && (
                <div role="alert" className="error">
                  <strong>{batch.error_code}</strong> {batch.error_message}
                </div>
              )}
              {batch.cancellation_requested && (
                <p role="status">
                  Отмена запрошена. Завершённые результаты сохранятся.
                </p>
              )}
              <div className="actions">
                {["queued", "running"].includes(batch.status) && (
                  <button
                    disabled={action.isPending || batch.cancellation_requested}
                    onClick={() =>
                      action.mutate({ action: "cancel", batchId: id })
                    }
                  >
                    Отменить обработку
                  </button>
                )}
                {["partial", "failed", "cancelled"].includes(batch.status) && (
                  <button
                    disabled={action.isPending || allBusy}
                    onClick={() =>
                      action.mutate({ action: "resume", batchId: id })
                    }
                  >
                    Повторить незавершённые
                  </button>
                )}
                {["json", "csv", "zip"].map((format) => (
                  <button
                    key={format}
                    disabled={exported.isPending || allBusy}
                    onClick={() => exported.mutate(format)}
                  >
                    Сводный отчёт {format.toUpperCase()}
                  </button>
                ))}
              </div>
              <ErrorNotice error={action.error} />
              <ErrorNotice error={exported.error} />
              <details>
                <summary>Зафиксированные модели и источник</summary>
                <pre>{JSON.stringify(batch.model_snapshots, null, 2)}</pre>
                <p className="muted">
                  Версии и источник фиксируются при создании очереди. Оценки
                  разных типов имеют собственные шкалы; их нельзя усреднять как
                  общую уверенность.
                </p>
              </details>
              <div
                className="table-scroll"
                tabIndex={0}
                aria-label="Файлы и результаты пакетного анализа"
              >
                <table className="batch-table">
                  <thead>
                    <tr>
                      <th>Файл / происхождение</th>
                      <th>Проверка</th>
                      <th>Результаты моделей</th>
                    </tr>
                  </thead>
                  <tbody>
                    {batch.files
                      .slice(page * 25, (page + 1) * 25)
                      .map((file) => (
                        <BatchFileRow
                          key={file.file_id}
                          file={file}
                          results={batch.results.filter(
                            (result) => result.file_id === file.file_id,
                          )}
                          localError={intakeErrors[file.relative_path]}
                        />
                      ))}
                  </tbody>
                </table>
              </div>
              <div className="actions">
                <button disabled={!page} onClick={() => setPage((p) => p - 1)}>
                  Предыдущие файлы
                </button>
                <span>
                  Страница {page + 1} из{" "}
                  {Math.max(1, Math.ceil(batch.files.length / 25))}
                </span>
                <button
                  disabled={(page + 1) * 25 >= batch.files.length}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Следующие файлы
                </button>
              </div>
            </section>
          )}
          <ErrorNotice error={selectionError} />
          <ErrorNotice error={intake.error} />
          {ignoredFiles > 0 && (
            <p className="muted" role="status">
              Пропущено файлов другого типа: {ignoredFiles}. В очередь
              включаются только CSV.
            </p>
          )}
          {busy && (
            <div className="notice" role="status">
              <span>
                {localProgress.phase === "checking"
                  ? "Проверка файлов"
                  : localProgress.phase === "pausing"
                    ? "Останавливаем загрузку после текущего файла"
                    : "Загрузка файлов"}
                : {localProgress.completed} из {localProgress.total}
              </span>
              {localProgress.phase === "uploading" && (
                <button
                  onClick={() => {
                    pause.current = true;
                    setLocalProgress((value) => ({
                      ...value,
                      phase: "pausing",
                    }));
                  }}
                >
                  Приостановить загрузку
                </button>
              )}
            </div>
          )}
          {!!prepared.length && (
            <section className="panel">
              <h2>Выбранные файлы</h2>
              <p>
                {prepared.length} файлов, локальных ошибок:{" "}
                {prepared.filter((file) => file.error).length}.
              </p>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Файл</th>
                      <th>Размер</th>
                      <th>Проверка</th>
                    </tr>
                  </thead>
                  <tbody>
                    {prepared.map((entry) => (
                      <tr key={entry.relative_path}>
                        <th>{entry.relative_path}</th>
                        <td>{entry.size_bytes.toLocaleString("ru")} байт</td>
                        <td>
                          {entry.error ? (
                            <span className="field-error">
                              {entry.error.message}
                            </span>
                          ) : (
                            "Готов к загрузке"
                          )}
                          {intakeErrors[entry.relative_path] && (
                            <p className="field-error">
                              {intakeErrors[entry.relative_path]}
                            </p>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
function BatchFileRow({
  file,
  results,
  localError,
}: {
  file: BatchFile;
  results: BatchResult[];
  localError?: string;
}) {
  return (
    <tr>
      <th>
        <strong>{file.relative_path}</strong>
        <small>
          {file.point_count == null
            ? "—"
            : `${file.point_count.toLocaleString("ru")} точек`}{" "}
          · {file.size_bytes.toLocaleString("ru")} байт
        </small>
        <details>
          <summary>SHA-256</summary>
          <code>{file.sha256 ?? "Не вычислен"}</code>
        </details>
        {file.spectrum_id && (
          <Link to={`/analysis?spectrum=${file.spectrum_id}`}>
            Открыть спектр
          </Link>
        )}
      </th>
      <td>
        {statusNames[file.status] ?? file.status}
        {file.error_message && (
          <p className="field-error">{file.error_message}</p>
        )}
        {file.error_code && (
          <small>
            {file.error_code} ·{" "}
            {file.validation_origin === "client"
              ? "Проверка браузера"
              : "Проверка сервера"}
          </small>
        )}
        {localError && <p className="field-error">{localError}</p>}
      </td>
      <td>
        {results.length ? (
          results.map((result) => (
            <div className="batch-model-result" key={result.model_id}>
              <strong>{result.model_id}</strong>
              <span>
                {statusNames[result.status] ?? result.status}
                {result.cache_hit ? " · сохранённый результат" : ""}
              </span>
              {result.error_message && (
                <p className="field-error">{result.error_message}</p>
              )}
              {result.error_code && <small>{result.error_code}</small>}
              {result.prediction && (
                <>
                  <p>
                    Обнаружены:{" "}
                    {result.prediction.detected_elements.join(", ") ||
                      "порог не превышен"}
                  </p>
                  <small>
                    v{result.prediction.model_version} ·{" "}
                    {result.prediction.reference_source}
                  </small>
                  <details>
                    <summary>Оценки и пороги</summary>
                    <p>
                      <code>{result.prediction.score_kind}</code> ·{" "}
                      {result.prediction.calibrated
                        ? "Применена калибровка"
                        : "Не является вероятностью"}
                    </p>
                    <table>
                      <thead>
                        <tr>
                          <th>Элемент</th>
                          <th>Score</th>
                          <th>Порог</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.prediction.scores.map((score) => (
                          <tr key={score.element}>
                            <th>{score.element}</th>
                            <td>{score.value.toFixed(4)}</td>
                            <td>
                              {result.prediction!.thresholds[
                                score.element
                              ]?.toFixed(4) ?? "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {result.prediction.warnings.map((message, index) => (
                      <p className="warning" key={index}>
                        {message}
                      </p>
                    ))}
                  </details>
                </>
              )}
            </div>
          ))
        ) : (
          <span className="muted">После проверки файла</span>
        )}
      </td>
    </tr>
  );
}
