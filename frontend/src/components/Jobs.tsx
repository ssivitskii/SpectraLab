import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type Job } from "../api/client";
import { ErrorNotice, Loading } from "./Feedback";
const labels: Record<Job["status"], string> = {
  queued: "В очереди",
  running: "Выполняется",
  completed: "Завершено",
  failed: "Ошибка",
  cancelled: "Отменено",
};
function storedDraft(): { seeds: string; config: string } {
  try {
    return (
      JSON.parse(sessionStorage.getItem("spectralab.job-draft") ?? "null") ?? {
        seeds: "11, 23, 37, 53, 71",
        config: "demo",
      }
    );
  } catch {
    return { seeds: "11, 23, 37, 53, 71", config: "demo" };
  }
}
function storedKey() {
  try {
    return sessionStorage.getItem("spectralab.job-key") ?? crypto.randomUUID();
  } catch {
    return crypto.randomUUID();
  }
}
export function Jobs() {
  const [search, setSearch] = useSearchParams();
  const id = search.get("job") ?? "";
  const [seeds, setSeeds] = useState(() => storedDraft().seeds),
    [config, setConfig] = useState(() => storedDraft().config),
    [key, setKey] = useState(storedKey);
  const client = useQueryClient();
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: ({ signal }) => api.capabilities({ signal }),
  });
  const enabled =
    (capabilities.data?.jobs as { enabled?: boolean } | undefined)?.enabled !==
    false;
  const list = useQuery({
    queryKey: ["jobs"],
    queryFn: ({ signal }) => api.jobs({ signal }),
    enabled,
    refetchInterval: (query) =>
      query.state.data?.some((j) => ["queued", "running"].includes(j.status))
        ? 3000
        : false,
  });
  const current = useQuery({
    queryKey: ["job", id],
    queryFn: ({ signal }) => api.job(id, { signal }),
    enabled: !!id && enabled,
    refetchInterval: (query) =>
      query.state.data &&
      ["completed", "failed", "cancelled"].includes(query.state.data.status)
        ? false
        : 2000,
  });
  const newKey = () => {
    const value = crypto.randomUUID();
    setKey(value);
    try {
      sessionStorage.setItem("spectralab.job-key", value);
    } catch {
      /* a key is also retained in memory */
    }
    return value;
  };
  const create = useMutation({
    mutationFn: (override?: {
      seeds?: number[];
      config_name?: string;
      idempotency_key: string;
    }) => {
      const values =
        override?.seeds ??
        seeds.split(",").map((value) => Number(value.trim()));
      if (
        !values.length ||
        values.length > 10 ||
        new Set(values).size !== values.length ||
        values.some((n) => !Number.isInteger(n) || n < 0 || n > 2147483647) ||
        seeds.split(",").some((s) => !s.trim())
      )
        throw new ApiError(
          "Укажите от 1 до 10 разных целых seed через запятую.",
          "invalid_seeds",
        );
      const requestKey = override?.idempotency_key ?? key;
      try {
        sessionStorage.setItem("spectralab.job-key", requestKey);
        sessionStorage.setItem(
          "spectralab.job-draft",
          JSON.stringify({
            seeds: values.join(", "),
            config: override?.config_name ?? config,
          }),
        );
      } catch {
        /* retries in this page still reuse the key */
      }
      return api.createJob({
        seeds: values,
        config_name: override?.config_name ?? config,
        idempotency_key: requestKey,
      });
    },
    onSuccess: (job) => {
      setSearch((params) => {
        params.set("job", job.job_id);
        return params;
      });
      void client.invalidateQueries({ queryKey: ["jobs"] });
      client.setQueryData(["job", job.job_id], job);
    },
  });
  const cancel = useMutation({
    mutationFn: () => api.cancelJob(id),
    onSuccess: (job) => {
      client.setQueryData(["job", id], job);
      void client.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  return (
    <details className="panel jobs">
      <summary>Фоновые исследовательские задания</summary>
      {!enabled ? (
        <p>Фоновые задания отключены на этом сервисе.</p>
      ) : (
        <>
          <form
            className="job-form"
            onSubmit={(event) => {
              event.preventDefault();
              create.mutate(undefined);
            }}
          >
            <label>
              Seed серии
              <input
                value={seeds}
                onChange={(event) => {
                  setSeeds(event.target.value);
                  newKey();
                  create.reset();
                }}
                required
              />
            </label>
            <label>
              Конфигурация
              <select
                value={config}
                onChange={(e) => {
                  setConfig(e.target.value);
                  newKey();
                  create.reset();
                }}
              >
                <option value="demo">Демонстрационная</option>
                <option value="default">Стандартная</option>
              </select>
            </label>
            <button disabled={create.isPending}>Создать задание</button>
            <button type="button" onClick={() => void list.refetch()}>
              Обновить задания
            </button>
          </form>
          <ErrorNotice error={create.error} />
          <ErrorNotice error={list.error} />
          {list.data?.length === 0 && <p>Заданий пока нет.</p>}
          <div className="actions">
            {list.data?.map((job) => (
              <button
                key={job.job_id}
                onClick={() =>
                  setSearch((params) => {
                    params.set("job", job.job_id);
                    return params;
                  })
                }
              >
                {job.job_id.slice(0, 8)} · {labels[job.status]}
              </button>
            ))}
          </div>
          {id && current.isPending && <Loading />}
          <ErrorNotice error={current.error} />
          {current.data && (
            <div className="job-progress" aria-live="polite">
              <h3>
                {labels[current.data.status]} · {current.data.job_id}
              </h3>
              <p>
                Завершено прогонов: {current.data.progress.completed} из{" "}
                {current.data.progress.total}
              </p>
              <progress
                aria-label="Завершённые прогоны"
                max={current.data.progress.total || 1}
                value={current.data.progress.completed}
              />
              {current.data.error_code && (
                <p role="alert">
                  Причина:{" "}
                  {current.data.error_message ?? current.data.error_code} (
                  {current.data.error_code})
                </p>
              )}
              <div className="actions">
                {["queued", "running"].includes(current.data.status) && (
                  <button
                    disabled={cancel.isPending}
                    onClick={() => cancel.mutate()}
                  >
                    Отменить задание
                  </button>
                )}
                {["failed", "cancelled"].includes(current.data.status) && (
                  <button
                    disabled={create.isPending}
                    onClick={() =>
                      create.mutate({
                        seeds: current.data?.seeds,
                        config_name: current.data?.config_name,
                        idempotency_key: newKey(),
                      })
                    }
                  >
                    Повторить задание
                  </button>
                )}
                {current.data.run_id && (
                  <Link
                    className="button"
                    to={`/experiments?run=${current.data.run_id}&job=${id}`}
                  >
                    Открыть отчёт
                  </Link>
                )}
              </div>
              <ErrorNotice error={cancel.error} />
            </div>
          )}
        </>
      )}
    </details>
  );
}
