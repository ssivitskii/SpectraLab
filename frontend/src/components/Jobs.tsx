import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type Job } from "../api/client";
import { ErrorNotice, Loading } from "./Feedback";
import { Icon } from "./Icon";
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
    <section className="jobs" aria-label="Исследовательские задания">
      <div className="section-heading jobs-heading">
        <div>
          <h2>Запуск серии</h2>
          <p className="muted">
            Задайте конфигурацию и seed. Отдельный worker выполнит серию и
            сохранит отчёт; страницу можно закрыть.
          </p>
        </div>
      </div>
      {!enabled ? (
        <p className="notice">Фоновые задания отключены на этом сервисе.</p>
      ) : (
        <div className="workspace jobs-workspace">
          <aside className="panel controls">
            <div className="control-section">
              <div className="section-heading">
                <Icon name="sliders" />
                <h3>Параметры серии</h3>
              </div>
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
                    aria-describedby="job-seeds-help"
                    onChange={(event) => {
                      setSeeds(event.target.value);
                      newKey();
                      create.reset();
                    }}
                    required
                  />
                </label>
                <p className="field-hint" id="job-seeds-help">
                  От 1 до 10 разных целых seed через запятую.
                </p>
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
                <button className="primary" disabled={create.isPending}>
                  <Icon name="play" />
                  Создать задание
                </button>
              </form>
              <ErrorNotice error={create.error} />
            </div>
            <div className="control-section">
              <div className="section-heading">
                <Icon name="clock" />
                <h3>Сохранённые задания</h3>
              </div>
              <button onClick={() => void list.refetch()}>
                <Icon name="refresh" />
                Обновить задания
              </button>
              <ErrorNotice error={list.error} />
              {list.isPending && <Loading>Загрузка заданий…</Loading>}
              {list.data?.length === 0 && <p className="muted">Заданий пока нет.</p>}
              <div className="run-list">
                {list.data?.map((job) => (
                  <button
                    className={`run-choice ${id === job.job_id ? "active" : ""}`}
                    aria-pressed={id === job.job_id}
                    key={job.job_id}
                    onClick={() =>
                      setSearch((params) => {
                        params.set("job", job.job_id);
                        return params;
                      })
                    }
                  >
                    <strong>{job.job_id.slice(0, 8)}</strong>
                    <small>{labels[job.status]}</small>
                  </button>
                ))}
              </div>
            </div>
          </aside>
          <section className="panel stage-panel" aria-label="Текущее задание">
            <div className="section-heading">
              <Icon name="activity" />
              <h2>Состояние задания</h2>
            </div>
            {id && current.isPending && <Loading />}
            <ErrorNotice error={current.error} />
            {current.data ? (
              <div className="job-progress" aria-live="polite">
                <h3>
                  {labels[current.data.status]} · {current.data.job_id}
                </h3>
                <dl className="stage-summary">
                  <div>
                    <dt>Конфигурация</dt>
                    <dd>{current.data.config_name}</dd>
                  </div>
                  <div>
                    <dt>Seed</dt>
                    <dd>{current.data.seeds.join(", ")}</dd>
                  </div>
                </dl>
                <p>
                  Завершено прогонов: {current.data.progress.completed} из{" "}
                  {current.data.progress.total}
                </p>
                <progress
                  aria-label="Завершённые прогоны"
                  max={current.data.progress.total || 1}
                  value={current.data.progress.completed}
                />
                {current.data.status === "queued" && (
                  <p className="muted">
                    Задание ожидает worker. В локальной среде он запускается
                    командой <code>make worker</code>.
                  </p>
                )}
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
                      <Icon name="close" />
                      Отменить задание
                    </button>
                  )}
                  {["failed", "cancelled"].includes(current.data.status) && (
                    <button
                      className="primary"
                      disabled={create.isPending}
                      onClick={() =>
                        create.mutate({
                          seeds: current.data?.seeds,
                          config_name: current.data?.config_name,
                          idempotency_key: newKey(),
                        })
                      }
                    >
                      <Icon name="refresh" />
                      Повторить задание
                    </button>
                  )}
                  {current.data.run_id && (
                    <Link
                      className="button"
                      to={`/experiments?run=${current.data.run_id}&job=${id}`}
                    >
                      Открыть отчёт
                      <Icon name="arrowRight" />
                    </Link>
                  )}
                </div>
                <ErrorNotice error={cancel.error} />
              </div>
            ) : !id && (
              <div className="empty tall">
                <Icon name="activity" />
                <h3>Подготовьте новую серию</h3>
                <p>
                  Создайте задание с выбранными параметрами или откройте
                  сохранённое слева. Результаты появятся после выполнения.
                </p>
              </div>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
