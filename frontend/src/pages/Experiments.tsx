import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  useInfiniteQuery,
  useMutation,
  useQueries,
} from "@tanstack/react-query";
import type { Data } from "plotly.js";
import {
  api,
  type Aggregate,
  type Estimate,
  type Experiment,
} from "../api/client";
import { Chart } from "../components/Chart";
import { ErrorNotice, Loading } from "../components/Feedback";
import { Jobs } from "../components/Jobs";
const fmt = (n: number | null | undefined) => (n == null ? "—" : n.toFixed(3));
const metricNames = {
  micro_f1: "micro-F1",
  macro_f1: "macro-F1",
  exact_match_accuracy: "Exact match",
  hamming_loss: "Hamming loss",
  inference_ms_per_spectrum: "Время, ms/спектр",
};
type Metric = keyof typeof metricNames;
type Subset = "all" | "single" | "mixture";
function estimate(
  row: Aggregate,
  metric: Metric,
  subset: Subset,
): Estimate | undefined {
  const value =
    subset === "all"
      ? row.metrics[metric]
      : (row.metrics[subset] as Record<string, Estimate> | undefined)?.[metric];
  return value && "mean" in value ? (value as Estimate) : undefined;
}
function comparable(left: Experiment, right: Experiment) {
  return (
    left.protocol === right.protocol &&
    left.reference_source === right.reference_source &&
    left.source_hash === right.source_hash &&
    left.artifact_type === right.artifact_type
  );
}
export function Experiments() {
  const [search, setSearch] = useSearchParams();
  const id = search.get("run") ?? "";
  const [status, setStatus] = useState(""),
    [protocol, setProtocol] = useState(""),
    [source, setSource] = useState("");
  const [additional, setAdditional] = useState<string[]>([]),
    [metric, setMetric] = useState<Metric>("micro_f1"),
    [subset, setSubset] = useState<Subset>("all");
  const filters = { status, protocol, reference_source: source };
  const list = useInfiniteQuery({
    queryKey: ["experiments", filters],
    queryFn: ({ pageParam, signal }) =>
      api.experimentCatalog({ ...filters, cursor: pageParam }, { signal }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const ids = [...new Set([id, ...additional].filter(Boolean))];
  const details = useQueries({
    queries: ids.map((run) => ({
      queryKey: ["experiment", run],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        api.experiment(run, { signal }),
    })),
  });
  const selected = details[0]?.data;
  const compatible = details.flatMap((query) =>
    query.data && selected && comparable(selected, query.data)
      ? [query.data]
      : [],
  );
  const plots: Data[] = compatible.flatMap((run) => {
    const aggregates = run.aggregates ?? [];
    if (aggregates.length)
      return [...new Set(aggregates.map((row) => row.model_id))].map(
        (model) => {
          const rows = aggregates.filter((row) => row.model_id === model);
          return {
            type: "scatter" as const,
            mode: "lines+markers" as const,
            name: `${model} · ${run.run_id}`,
            connectgaps: false,
            x: rows.map((row) => String(row.value ?? "без шума")),
            y: rows.map((row) => estimate(row, metric, subset)?.mean ?? null),
            error_y: {
              type: "data" as const,
              array: rows.map(
                (row) => estimate(row, metric, subset)?.std ?? null,
              ) as number[],
              visible: true,
            },
            customdata: rows.map(
              (row) => estimate(row, metric, subset)?.count ?? 0,
            ),
            hovertemplate:
              "%{y:.3f} · n=%{customdata}<extra>%{fullData.name}</extra>",
          };
        },
      );
    return [...new Set((run.runs ?? []).map((row) => row.model_id))].map(
      (model) => {
        const rows = (run.runs ?? []).filter((row) => row.model_id === model);
        return {
          type: "scatter" as const,
          mode: "lines+markers" as const,
          name: `${model} · ${run.run_id}`,
          connectgaps: false,
          x: rows.map((row) => String(row.value ?? "без шума")),
          y: rows.map((row) =>
            subset === "all"
              ? (row.metrics[metric] ?? null)
              : metric === "micro_f1" || metric === "exact_match_accuracy"
                ? (row.metrics[subset]?.[metric] ?? null)
                : null,
          ),
        };
      },
    );
  });
  return (
    <>
      <header className="page-title">
        <div>
          <h1>Эксперименты</h1>
          <p>Фактические результаты сохранённых запусков и серий.</p>
        </div>
        <button
          onClick={() => {
            void list.refetch();
            details.forEach((query) => void query.refetch());
          }}
        >
          Обновить
        </button>
      </header>
      <Jobs />
      <form className="filters" onSubmit={(event) => event.preventDefault()}>
        <label>
          Статус
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Все статусы</option>
            {["completed", "running", "failed", "incomplete"].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          Протокол
          <input
            value={protocol}
            onChange={(e) => setProtocol(e.target.value)}
            placeholder="Все протоколы"
          />
        </label>
        <label>
          Источник
          <input
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="Все источники"
          />
        </label>
      </form>
      <ErrorNotice error={list.error} />
      {list.isPending ? (
        <Loading />
      ) : (
        <div className="workspace">
          <aside className="panel controls">
            <h2>Сохранённые запуски</h2>
            {list.data?.pages[0].items.length === 0 && (
              <div className="empty">
                <h3>Экспериментов пока нет</h3>
                <p>
                  {status || protocol || source
                    ? "Измените фильтры."
                    : "Запустите демонстрационный прогон:"}
                </p>
                {!(status || protocol || source) && <code>make demo</code>}
              </div>
            )}
            {list.data?.pages
              .flatMap((page) => page.items)
              .map((item) => (
                <div key={item.run_id}>
                  <button
                    className={`run-choice ${id === item.run_id ? "active" : ""}`}
                    onClick={() => {
                      setSearch((params) => {
                        params.set("run", item.run_id);
                        return params;
                      });
                      setAdditional([]);
                    }}
                  >
                    <strong>{item.run_id}</strong>
                    <small>
                      {new Date(item.created_at).toLocaleString("ru")} ·{" "}
                      {item.status}
                    </small>
                    <small>
                      {item.reference_source} · {item.protocol}
                    </small>
                  </button>
                  {id && id !== item.run_id && (
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={additional.includes(item.run_id)}
                        disabled={
                          additional.length >= 3 &&
                          !additional.includes(item.run_id)
                        }
                        onChange={(event) =>
                          setAdditional((current) =>
                            event.target.checked
                              ? [...current, item.run_id]
                              : current.filter(
                                  (value) => value !== item.run_id,
                                ),
                          )
                        }
                      />
                      Сравнить
                    </label>
                  )}
                </div>
              ))}
            {list.hasNextPage && (
              <button
                disabled={list.isFetchingNextPage}
                onClick={() => void list.fetchNextPage()}
              >
                Ещё запуски
              </button>
            )}
          </aside>
          <section className="panel">
            <div className="metric-controls">
              <label>
                Метрика
                <select
                  value={metric}
                  onChange={(e) => setMetric(e.target.value as Metric)}
                >
                  {Object.entries(metricNames).map(([key, name]) => (
                    <option
                      value={key}
                      key={key}
                      disabled={
                        subset !== "all" &&
                        key !== "micro_f1" &&
                        key !== "exact_match_accuracy"
                      }
                    >
                      {name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Состав
                <select
                  value={subset}
                  onChange={(e) => {
                    setSubset(e.target.value as Subset);
                    if (
                      metric !== "micro_f1" &&
                      metric !== "exact_match_accuracy"
                    )
                      setMetric("micro_f1");
                  }}
                >
                  <option value="all">Все образцы</option>
                  <option value="single">Один элемент</option>
                  <option value="mixture">Смеси</option>
                </select>
              </label>
            </div>
            {details.map((query, i) => (
              <ErrorNotice key={ids[i]} error={query.error} />
            ))}
            {details.some((query) => query.isPending) && <Loading />}
            {details
              .filter(
                (query) =>
                  query.data && selected && !comparable(selected, query.data),
              )
              .map((query) => (
                <p className="warning" key={query.data!.run_id}>
                  Запуск {query.data!.run_id} исключён из сравнения: отличаются
                  протокол, источник, его checksum или тип отчёта.
                </p>
              ))}
            {plots.length > 0 && (
              <Chart
                label="Устойчивость моделей"
                data={plots}
                layout={{
                  xaxis: {
                    title: {
                      text:
                        selected?.aggregates?.[0]?.factor ??
                        selected?.runs?.[0]?.factor ??
                        "Условие",
                    },
                    type: "category",
                  },
                  yaxis: { title: { text: metricNames[metric] } },
                }}
              />
            )}
            {compatible.length > 0
              ? compatible.map((run) => (
                  <RunDetail
                    key={run.run_id}
                    run={run}
                    metric={metric}
                    subset={subset}
                  />
                ))
              : !id && (
                  <div className="empty tall">
                    Выберите сохранённый запуск слева.
                  </div>
                )}
          </section>
        </div>
      )}
    </>
  );
}
function RunDetail({
  run,
  metric,
  subset,
}: {
  run: Experiment;
  metric: Metric;
  subset: Subset;
}) {
  const exported = useMutation({
    mutationFn: () => api.export("experiments", run.run_id),
  });
  return (
    <>
      <div className="panel-heading">
        <h2>{run.run_id}</h2>
        <span className="tag">{run.status}</span>
      </div>
      {run.limitations && <p className="warning">{run.limitations}</p>}
      <p>
        Протокол: <code>{run.protocol}</code> · справочник:{" "}
        {run.reference_source}
      </p>
      {run.error && (
        <p role="alert">
          Причина:{" "}
          {typeof run.error === "string"
            ? run.error
            : JSON.stringify(run.error)}
        </p>
      )}
      {(run.expected_seeds?.length ?? 0) > 0 && (
        <>
          <p>
            Полных прогонов: {run.successful_seeds?.length ?? 0} из{" "}
            {run.expected_seeds!.length}. Ожидаемые seed:{" "}
            {run.expected_seeds!.join(", ")}.
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Seed</th>
                  <th>Run ID</th>
                  <th>Статус</th>
                  <th>Причина / хеши</th>
                </tr>
              </thead>
              <tbody>
                {run.seed_runs?.map((item) => (
                  <tr key={item.seed}>
                    <th>{item.seed}</th>
                    <td>
                      {item.run_id ? (
                        <Link to={`/experiments?run=${item.run_id}`}>
                          {item.run_id}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{item.status}</td>
                    <td>
                      {item.error
                        ? typeof item.error === "string"
                          ? item.error
                          : JSON.stringify(item.error)
                        : "—"}
                      <small>
                        config: {item.config_hash ?? "—"}
                        <br />
                        source: {item.source_hash ?? "—"}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {!!run.aggregates?.length && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Модель / условие</th>
                <th>Среднее {metricNames[metric]}</th>
                <th>SD (ddof=1)</th>
                <th>n</th>
              </tr>
            </thead>
            <tbody>
              {run.aggregates.map((row, i) => {
                const value = estimate(row, metric, subset);
                return (
                  <tr key={i}>
                    <th>
                      {row.model_id}
                      <small>
                        {row.factor}: {String(row.value ?? "без шума")}
                      </small>
                    </th>
                    <td>{fmt(value?.mean)}</td>
                    <td>{fmt(value?.std)}</td>
                    <td>{value?.count ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!!run.runs?.length && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Модель / условие</th>
                <th>micro-F1</th>
                <th>macro-F1</th>
                <th>Exact</th>
                <th>Hamming</th>
                <th>Один</th>
                <th>Смеси</th>
              </tr>
            </thead>
            <tbody>
              {(run.runs ?? []).map((row, i) => (
                <tr key={i}>
                  <th>
                    {row.model_id}
                    <small>
                      {row.factor}: {row.value ?? "без шума"}
                    </small>
                  </th>
                  <td>{fmt(row.metrics.micro_f1)}</td>
                  <td>{fmt(row.metrics.macro_f1)}</td>
                  <td>{fmt(row.metrics.exact_match_accuracy)}</td>
                  <td>{fmt(row.metrics.hamming_loss)}</td>
                  <td>{fmt(row.metrics.single?.micro_f1)}</td>
                  <td>{fmt(row.metrics.mixture?.micro_f1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted">
        «—» означает отсутствующую метрику. Среднее и SD поступают из отчёта;
        неполные seed не входят в сравнительные агрегаты.
      </p>
      <details>
        <summary>Параметры и метрики по элементам</summary>
        <pre>
          {JSON.stringify(
            {
              configuration: run.configuration,
              config_hash: run.config_hash,
              source_hash: run.source_hash,
              per_element: (run.runs ?? []).map((row) => ({
                model_id: row.model_id,
                value: row.value,
                metrics: row.metrics.per_element,
              })),
            },
            null,
            2,
          )}
        </pre>
      </details>
      <button disabled={exported.isPending} onClick={() => exported.mutate()}>
        Экспорт эксперимента ZIP
      </button>
      <ErrorNotice error={exported.error} />
    </>
  );
}
