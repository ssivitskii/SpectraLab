import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Chart } from "../components/Chart";
import { ErrorNotice, Loading } from "../components/Feedback";
const fmt = (n: number | null | undefined) => (n == null ? "—" : n.toFixed(3));
export function Experiments() {
  const [search, setSearch] = useSearchParams(),
    id = search.get("run") ?? "";
  const list = useQuery({
    queryKey: ["experiments"],
    queryFn: api.experiments,
  });
  const detail = useQuery({
    queryKey: ["experiment", id],
    queryFn: () => api.experiment(id),
    enabled: !!id,
  });
  const run = detail.data,
    rows = run?.runs ?? [],
    names = [...new Set(rows.map((r) => r.model_id))];
  return (
    <>
      <header className="page-title">
        <div>
          <p className="eyebrow">ВОСПРОИЗВОДИМЫЕ ИССЛЕДОВАНИЯ</p>
          <h1>Эксперименты</h1>
          <p>Фактические результаты сохранённых запусков.</p>
        </div>
        <button
          onClick={() => {
            void list.refetch();
            if (id) void detail.refetch();
          }}
        >
          Обновить
        </button>
      </header>
      <ErrorNotice error={list.error} />
      {list.isPending ? (
        <Loading />
      ) : list.data?.length === 0 ? (
        <section className="panel empty tall">
          <h2>Экспериментов пока нет</h2>
          <p>Запустите небольшой демонстрационный прогон:</p>
          <code>make demo</code>
          <p>
            Или исследование:{" "}
            <code>
              make experiment
              CONFIG=ml/configs/experiments/noise_robustness.yaml
            </code>
          </p>
        </section>
      ) : (
        <div className="workspace">
          <aside className="panel controls">
            <h2>Сохранённые запуски</h2>
            {list.data?.map((item) => (
              <button
                className={`run-choice ${id === item.run_id ? "active" : ""}`}
                key={item.run_id}
                onClick={() => setSearch({ run: item.run_id })}
              >
                <strong>{item.run_id}</strong>
                <small>
                  {new Date(item.created_at).toLocaleString("ru")} ·{" "}
                  {item.status}
                </small>
                <small>
                  {item.reference_source} {item.demo ? "· демонстрация" : ""}
                </small>
              </button>
            ))}
          </aside>
          <section className="panel">
            <ErrorNotice error={detail.error} />
            {id && detail.isPending ? (
              <Loading />
            ) : run ? (
              <>
                <div className="panel-heading">
                  <h2>Сравнение моделей</h2>
                  <span className="tag">seed {run.seed}</span>
                </div>
                <p className="warning">{run.limitations}</p>
                <p>
                  Протокол: <code>{run.protocol}</code> · статус: {run.status}
                </p>
                <details>
                  <summary>Параметры эксперимента</summary>
                  <pre>{JSON.stringify(run.configuration, null, 2)}</pre>
                </details>
                {rows.length > 0 && (
                  <>
                    <Chart
                      label="Устойчивость моделей"
                      data={names.map((name) => ({
                        type: "scatter",
                        mode: "lines+markers",
                        name,
                        x: rows
                          .filter((r) => r.model_id === name)
                          .map((r) =>
                            r.value == null ? "без шума" : String(r.value),
                          ),
                        y: rows
                          .filter((r) => r.model_id === name)
                          .map((r) => r.metrics.micro_f1),
                      }))}
                      layout={{
                        xaxis: {
                          title: { text: rows[0]?.factor ?? "Фактор" },
                          type: "category",
                        },
                        yaxis: {
                          title: { text: "micro-F1" },
                          range: [0, 1.05],
                        },
                      }}
                    />
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
                          {rows.map((row, i) => (
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
                    {rows.map((row, i) => (
                      <details key={i}>
                        <summary>
                          {row.model_id} · {row.value ?? "без шума"} — метрики
                          по элементам
                        </summary>
                        <table>
                          <thead>
                            <tr>
                              <th>Элемент</th>
                              <th>Support</th>
                              <th>Precision</th>
                              <th>Recall</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(row.metrics.per_element).map(
                              ([e, m]) => (
                                <tr key={e}>
                                  <th>{e}</th>
                                  <td>{m.support}</td>
                                  <td>{fmt(m.precision)}</td>
                                  <td>{fmt(m.recall)}</td>
                                </tr>
                              ),
                            )}
                          </tbody>
                        </table>
                        <p className="muted">
                          «—» означает, что метрику невозможно вычислить для
                          этой выборки.
                        </p>
                      </details>
                    ))}
                  </>
                )}
              </>
            ) : (
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
