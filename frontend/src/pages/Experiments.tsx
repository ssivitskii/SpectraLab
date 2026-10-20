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
import { Icon } from "../components/Icon";
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
const protocolNames: Record<string, string> = {
  iid_grouped: "Групповое разбиение",
  controlled_sweep: "Устойчивость к условиям",
  unseen_combinations: "Новые сочетания элементов",
};
const runTitle = (run: Pick<Experiment, "artifact_type" | "protocol">) =>
  `${run.artifact_type === "experiment_series" ? "Серия" : "Прогон"} · ${
    protocolNames[run.protocol] ?? "Исследование"
  }`;
const runDate = (createdAt: string, short = false) =>
  new Date(createdAt).toLocaleString("ru", {
    day: "2-digit",
    month: "short",
    ...(short ? {} : { year: "numeric" as const }),
    hour: "2-digit",
    minute: "2-digit",
  });
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
  const view =
    search.get("view") === "jobs" ||
    (!search.get("view") && !id && search.has("job"))
      ? "jobs"
      : "reports";
  const selectView = (next: "reports" | "jobs") =>
    setSearch((params) => {
      params.set("view", next);
      return params;
    });
  const [status, setStatus] = useState(""),
    [protocol, setProtocol] = useState(""),
    [source, setSource] = useState("");
  const [additional, setAdditional] = useState<string[]>([]),
    [metric, setMetric] = useState<Metric>("micro_f1"),
    [subset, setSubset] = useState<Subset>("all");
  const [catalogOpen, setCatalogOpen] = useState(true);
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
  const runAliases = new Map(
    ids.map((run, index) => [run, String.fromCharCode(65 + index)]),
  );
  const legendName = (model: string, run: Experiment) =>
    compatible.length === 1
      ? model
      : `${model} · ${runAliases.get(run.run_id)} · ${runDate(run.created_at, true)}`;
  const plots: Data[] = compatible.flatMap((run) => {
    const aggregates = run.aggregates ?? [];
    if (aggregates.length)
      return [...new Set(aggregates.map((row) => row.model_id))].map(
        (model) => {
          const rows = aggregates.filter((row) => row.model_id === model);
          return {
            type: "scatter" as const,
            mode: "lines+markers" as const,
            name: legendName(model, run),
            meta: { run_id: run.run_id },
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
              "%{y:.3f} · n=%{customdata}<br>Run ID: %{meta.run_id}<extra>%{fullData.name}</extra>",
          };
        },
      );
    return [...new Set((run.runs ?? []).map((row) => row.model_id))].map(
      (model) => {
        const rows = (run.runs ?? []).filter((row) => row.model_id === model);
        return {
          type: "scatter" as const,
          mode: "lines+markers" as const,
          name: legendName(model, run),
          meta: { run_id: run.run_id },
          connectgaps: false,
          x: rows.map((row) => String(row.value ?? "без шума")),
          y: rows.map((row) =>
            subset === "all"
              ? (row.metrics[metric] ?? null)
              : metric === "micro_f1" || metric === "exact_match_accuracy"
                ? (row.metrics[subset]?.[metric] ?? null)
                : null,
          ),
          hovertemplate:
            "%{x}: %{y:.3f}<br>Run ID: %{meta.run_id}<extra>%{fullData.name}</extra>",
        };
      },
    );
  });
  return (
    <>
      <header className="page-title">
        <div>
          <h1>Эксперименты</h1>
          <p>Сравнивайте сохранённые прогоны и запускайте новые серии.</p>
        </div>
        <button
          onClick={() => {
            void list.refetch();
            details.forEach((query) => void query.refetch());
          }}
        >
          <Icon name="refresh" />
          Обновить
        </button>
      </header>
      <div
        className="view-tabs"
        role="tablist"
        aria-label="Раздел экспериментов"
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
            return;
          event.preventDefault();
          const next =
            event.key === "Home"
              ? "reports"
              : event.key === "End"
                ? "jobs"
                : view === "reports"
                  ? "jobs"
                  : "reports";
          selectView(next);
          document.getElementById(`experiments-tab-${next}`)?.focus();
        }}
      >
        <button
          id="experiments-tab-reports"
          role="tab"
          aria-selected={view === "reports"}
          aria-controls="experiments-reports"
          tabIndex={view === "reports" ? 0 : -1}
          onClick={() => selectView("reports")}
        >
          <Icon name="experiments" />
          Отчёты
        </button>
        <button
          id="experiments-tab-jobs"
          role="tab"
          aria-selected={view === "jobs"}
          aria-controls="experiments-jobs"
          tabIndex={view === "jobs" ? 0 : -1}
          onClick={() => selectView("jobs")}
        >
          <Icon name="activity" />
          Фоновые исследовательские задания
        </button>
      </div>
      <div
        id="experiments-jobs"
        role="tabpanel"
        aria-labelledby="experiments-tab-jobs"
        hidden={view !== "jobs"}
      >
        <Jobs />
      </div>
      <div
        id="experiments-reports"
        role="tabpanel"
        aria-labelledby="experiments-tab-reports"
        hidden={view !== "reports"}
      >
        <form
          className="filters experiment-filters"
          onSubmit={(event) => event.preventDefault()}
        >
          <label>
            Статус
            <select value={status} onChange={(e) => {
              setStatus(e.target.value);
              setCatalogOpen(true);
            }}>
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
              onChange={(e) => {
                setProtocol(e.target.value);
                setCatalogOpen(true);
              }}
              placeholder="Все протоколы"
            />
          </label>
          <label>
            Источник
            <input
              value={source}
              onChange={(e) => {
                setSource(e.target.value);
                setCatalogOpen(true);
              }}
              placeholder="Все источники"
            />
          </label>
        </form>
        <ErrorNotice error={list.error} />
        {list.isPending ? (
          <Loading />
        ) : (
          <div className="workspace experiments-workspace">
            <aside className="panel controls">
              <div className="section-heading">
                <Icon name="layers" />
                <h2>Сохранённые запуски</h2>
              </div>
              {selected && (
                <p className="catalog-selection" title={selected.run_id}>
                  {runTitle(selected)}
                  <small>{runDate(selected.created_at)}</small>
                </p>
              )}
              <details
                className="experiment-catalog"
                open={catalogOpen}
                onToggle={(event) => setCatalogOpen(event.currentTarget.open)}
              >
                <summary>
                  {selected ? "Выбрать другой запуск" : "Выбрать запуск"}
                </summary>
                <div
                  className="experiment-catalog-scroll"
                  role="region"
                  aria-label="Сохранённые запуски"
                  tabIndex={0}
                >
                  <p className="muted">
                    Выберите отчёт. Для сравнения можно добавить ещё три совместимых
                    запуска.
                  </p>
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
                  <div className="run-list">
                    {list.data?.pages
                      .flatMap((page) => page.items)
                      .map((item) => (
                        <div className="run-entry" key={item.run_id}>
                          <button
                            className={`run-choice ${id === item.run_id ? "active" : ""}`}
                            aria-pressed={id === item.run_id}
                            title={item.run_id}
                            data-run-id={item.run_id}
                            onClick={() => {
                              setSearch((params) => {
                                params.set("run", item.run_id);
                                params.set("view", "reports");
                                return params;
                              });
                              setAdditional([]);
                              setCatalogOpen(false);
                            }}
                          >
                            <strong>{runTitle(item)}</strong>
                            <small>
                              {runDate(item.created_at)} ·{" "}
                              {item.status}
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
                  </div>
                  {list.hasNextPage && (
                    <button
                      disabled={list.isFetchingNextPage}
                      onClick={() => void list.fetchNextPage()}
                    >
                      Ещё запуски
                    </button>
                  )}
                </div>
              </details>
            </aside>
            <section className="panel stage-panel report-stage">
              <div className="section-heading">
                <div>
                  <h2>Сравнение моделей</h2>
                  <p className="muted">
                    Один протокол, источник и тип отчёта. Значения поступают из
                    сохранённых результатов.
                  </p>
                </div>
              </div>
              <div className="metric-controls control-section">
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
                      alias={
                        compatible.length > 1
                          ? runAliases.get(run.run_id)
                          : undefined
                      }
                      metric={metric}
                      subset={subset}
                    />
                  ))
                : !id && (
                    <div className="empty tall report-empty">
                      <Icon name="experiments" />
                      <h3>Выберите запуск для сравнения</h3>
                      <p>Выберите сохранённый запуск слева.</p>
                      <button onClick={() => selectView("jobs")}>
                        <Icon name="plus" />
                        Запустить новую серию
                      </button>
                    </div>
                  )}
            </section>
          </div>
        )}
      </div>
    </>
  );
}
function RunDetail({
  run,
  alias,
  metric,
  subset,
}: {
  run: Experiment;
  alias?: string;
  metric: Metric;
  subset: Subset;
}) {
  const exported = useMutation({
    mutationFn: () => api.export("experiments", run.run_id),
  });
  return (
    <article className="run-detail control-section" data-run-id={run.run_id}>
      <div className="panel-heading section-heading">
        <h2 title={run.run_id}>
          {alias && `${alias} · `}
          {runTitle(run)}
        </h2>
        <span className="tag" data-status={run.status}>
          {run.status}
        </span>
      </div>
      <p className="muted">{runDate(run.created_at)}</p>
      {run.limitations && <p className="warning">{run.limitations}</p>}
      {run.error && (
        <p role="alert">
          Причина:{" "}
          {typeof run.error === "string"
            ? run.error
            : JSON.stringify(run.error)}
        </p>
      )}
      {!!run.aggregates?.length && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Модель / условие</th>
                <th className="num">Среднее {metricNames[metric]}</th>
                <th className="num">SD (ddof=1)</th>
                <th className="num">n</th>
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
                    <td className="num">{fmt(value?.mean)}</td>
                    <td className="num">{fmt(value?.std)}</td>
                    <td className="num">{value?.count ?? "—"}</td>
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
                <th className="num">micro-F1</th>
                <th className="num">macro-F1</th>
                <th className="num">Exact</th>
                <th className="num">Hamming</th>
                <th className="num">Один</th>
                <th className="num">Смеси</th>
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
                  <td className="num">{fmt(row.metrics.micro_f1)}</td>
                  <td className="num">{fmt(row.metrics.macro_f1)}</td>
                  <td className="num">{fmt(row.metrics.exact_match_accuracy)}</td>
                  <td className="num">{fmt(row.metrics.hamming_loss)}</td>
                  <td className="num">{fmt(row.metrics.single?.micro_f1)}</td>
                  <td className="num">{fmt(row.metrics.mixture?.micro_f1)}</td>
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
      <details className="provenance-details">
        <summary>Прогоны и происхождение отчёта</summary>
        <dl className="report-provenance">
          <div>
            <dt>Run ID</dt>
            <dd>
              <Link to={`/experiments?run=${run.run_id}`}>{run.run_id}</Link>
            </dd>
          </div>
          <div>
            <dt>Дата создания</dt>
            <dd>{run.created_at}</dd>
          </div>
          <div>
            <dt>Тип отчёта</dt>
            <dd>{run.artifact_type}</dd>
          </div>
          <div>
            <dt>Протокол</dt>
            <dd>
              <code>{run.protocol}</code>
            </dd>
          </div>
          <div>
            <dt>Источник</dt>
            <dd>{run.reference_source}</dd>
          </div>
        </dl>
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
      </details>
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
        <Icon name="download" />
        Экспорт эксперимента ZIP
      </button>
      <ErrorNotice error={exported.error} />
    </article>
  );
}
