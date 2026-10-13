import { useEffect, useMemo, useRef, useState } from "react";
import type { Data, Layout, PlotHoverEvent } from "plotly.js";
import { displaySeries } from "./plotData";
import type { Spectrum, Prediction } from "../api/client";
const emptyLayout: Partial<Layout> = {};
export function Chart({
  data,
  layout = emptyLayout,
  label = "График",
  onHover,
}: {
  data: Data[];
  layout?: Partial<Layout>;
  label?: string;
  onHover?: (event: PlotHoverEvent) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [engine, setEngine] = useState<typeof import("plotly.js") | null>(null);
  const hover = useRef(onHover);
  hover.current = onHover;
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    let active = true;
    let loaded: typeof import("plotly.js") | undefined;
    void import("plotly.js-basic-dist-min")
      .then(({ default: Plotly }) => {
        if (active) {
          loaded = Plotly;
          setEngine(Plotly);
        }
      })
      .catch(() => {
        if (active)
          setError(
            "Не удалось загрузить график. Данные доступны в таблице и экспорте.",
          );
      });
    const observer = new ResizeObserver(() => {
      if (loaded && node.classList.contains("js-plotly-plot"))
        void Promise.resolve(loaded.Plots.resize(node)).catch(() => {});
    });
    observer.observe(node);
    return () => {
      active = false;
      observer.disconnect();
      loaded?.purge(node);
    };
  }, []);
  useEffect(() => {
    const node = ref.current;
    if (!node || !engine) return;
    let cancelled = false;
    void engine
      .react(
        node,
        data,
        {
          autosize: true,
          height: 430,
          margin: { l: 60, r: 20, t: 35, b: 60 },
          paper_bgcolor: "#fff",
          plot_bgcolor: "#fff",
          font: { family: "system-ui", color: "#334155", size: 12 },
          hovermode: "closest",
          legend: { orientation: "h", y: 1.15 },
          ...layout,
        },
        {
          responsive: true,
          displaylogo: false,
          scrollZoom: true,
          toImageButtonOptions: { format: "png", filename: "spectralab" },
        },
      )
      .then(() => {
        if (!cancelled) {
          const plot = node as unknown as {
            removeAllListeners?: (name: string) => void;
            on?: (
              name: string,
              callback: (event: PlotHoverEvent) => void,
            ) => void;
          };
          plot.removeAllListeners?.("plotly_hover");
          plot.on?.("plotly_hover", (event) => hover.current?.(event));
        }
      })
      .catch(() => {
        if (!cancelled)
          setError(
            "Не удалось загрузить график. Данные доступны в таблице и экспорте.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [data, layout, engine]);
  return (
    <div className="chart" aria-label={label}>
      {error && <p role="alert">{error}</p>}
      <div ref={ref} />
    </div>
  );
}
export function SpectrumChart({
  spectrum,
  prediction,
  units = "nm",
  medium,
}: {
  spectrum: Spectrum;
  prediction?: Prediction;
  units?: string;
  medium?: string;
}) {
  const [references, setReferences] = useState(true),
    [clean, setClean] = useState(true),
    [element, setElement] = useState(""),
    [page, setPage] = useState(0),
    [active, setActive] = useState<number | null>(null);
  const [range, setRange] = useState<[number, number] | undefined>(),
    [reset, setReset] = useState(0);
  const [from, setFrom] = useState(String(spectrum.wavelength_nm[0])),
    [to, setTo] = useState(String(spectrum.wavelength_nm.at(-1)));
  const lines = useMemo(
    () =>
      (prediction?.matched_reference_lines ?? []).filter(
        (line) => !element || line.element === element,
      ),
    [prediction, element],
  );
  const shown = lines.slice(page * 50, (page + 1) * 50);
  const data = useMemo<Data[]>(() => {
    const observed = displaySeries(spectrum.wavelength_nm, spectrum.intensity);
    const result: Data[] = [
      {
        ...observed,
        uid: "observed",
        name: "Наблюдаемый",
        type: "scatter",
        mode: "lines",
        line: { color: "#1d4ed8", width: 1.4 },
      },
    ];
    if (clean && spectrum.clean_signal)
      result.push({
        ...displaySeries(spectrum.wavelength_nm, spectrum.clean_signal),
        uid: "clean",
        name: "Чистый сигнал",
        type: "scatter",
        mode: "lines",
        line: { color: "#0f766e", width: 1.4 },
      });
    if (references && lines.length) {
      const top = spectrum.intensity.reduce((a, b) => Math.max(a, b), 0);
      result.push({
        uid: "reference",
        x: lines.flatMap((l) => [l.wavelength_nm, l.wavelength_nm, null]),
        y: lines.flatMap(() => [0, top, null]),
        customdata: lines.flatMap((_, i) => [i, i, i]),
        text: lines.flatMap((l) => [l.element, l.element, l.element]),
        name: "Справочные линии",
        type: "scatter",
        mode: "lines",
        line: { color: "#b45309", width: 1, dash: "dot" },
        hovertemplate: "%{text} · %{x:.4f}<extra>Справочник</extra>",
      });
    }
    return result;
  }, [spectrum, clean, references, lines]);
  const layout = useMemo<Partial<Layout>>(
    () => ({
      xaxis: {
        title: {
          text: `Длина волны, ${spectrum.units ?? units}${(spectrum.wavelength_medium ?? medium) ? ` · ${spectrum.wavelength_medium ?? medium}` : ""}`,
        },
        gridcolor: "#e2e8f0",
        zeroline: false,
        ...(range ? { range, autorange: false } : {}),
      },
      yaxis: {
        title: { text: "Интенсивность, усл. ед." },
        gridcolor: "#f1f5f9",
        zerolinecolor: "#cbd5e1",
      },
      shapes:
        active !== null && lines[active]
          ? [
              {
                type: "line",
                x0: lines[active].wavelength_nm,
                x1: lines[active].wavelength_nm,
                y0: 0,
                y1: 1,
                yref: "paper",
                line: { color: "#991b1b", width: 3 },
              },
            ]
          : [],
      uirevision: `${spectrum.spectrum_id}-${reset}`,
    }),
    [spectrum, units, medium, range, active, lines, reset],
  );
  return (
    <>
      <div className="chart-toolbar">
        <span>Zoom: прокрутка или диапазон ниже · pan: панель графика</span>
        {spectrum.clean_signal && (
          <label>
            <input
              type="checkbox"
              checked={clean}
              onChange={(e) => setClean(e.target.checked)}
            />
            Чистый сигнал
          </label>
        )}
        {prediction && (
          <label>
            <input
              type="checkbox"
              checked={references}
              onChange={(e) => setReferences(e.target.checked)}
            />
            Справочные линии ({lines.length})
          </label>
        )}
      </div>
      <Chart
        data={data}
        layout={layout}
        label="Спектр образца"
        onHover={(event) => {
          const point = event.points?.find(
            (p) => typeof p.customdata === "number",
          );
          if (point && typeof point.customdata === "number") {
            setActive(point.customdata);
            setPage(Math.floor(point.customdata / 50));
          }
        }}
      />
      <form
        className="range-controls"
        onSubmit={(event) => {
          event.preventDefault();
          if (Number(from) < Number(to)) setRange([Number(from), Number(to)]);
        }}
      >
        <label>
          От, {units}
          <input
            aria-label="Начало диапазона"
            type="number"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            step="any"
            required
          />
        </label>
        <label>
          До, {units}
          <input
            aria-label="Конец диапазона"
            type="number"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            min={Number(from)}
            step="any"
            required
          />
        </label>
        <button type="submit" disabled={Number(from) >= Number(to)}>
          Показать диапазон
        </button>
        <button
          type="button"
          onClick={() => {
            setRange(undefined);
            setReset((v) => v + 1);
          }}
        >
          Сбросить zoom
        </button>
      </form>
      {spectrum.wavelength_nm.length > 4000 && (
        <p className="muted">
          График показывает огибающую максимумов и минимумов (до 4 000 точек).
          Распознавание и экспорт используют все{" "}
          {spectrum.wavelength_nm.length.toLocaleString("ru")} точек.
        </p>
      )}
      {prediction && (
        <details>
          <summary>Таблица справочных линий ({lines.length})</summary>
          <label>
            Фильтр элемента
            <select
              aria-label="Фильтр справочных линий"
              value={element}
              onChange={(e) => {
                setElement(e.target.value);
                setPage(0);
                setActive(null);
              }}
            >
              <option value="">Все элементы</option>
              {[
                ...new Set(
                  prediction.matched_reference_lines.map((l) => l.element),
                ),
              ].map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
          </label>
          <p className="muted">
            Фокус на строке выделяет линию на графике; наведение на справочную
            линию выбирает строку. Диагностика не объясняет решение модели.
          </p>
          <div
            className="table-scroll"
            tabIndex={0}
            aria-label="Справочные и наблюдаемые линии"
          >
            <table>
              <thead>
                <tr>
                  <th>Элемент</th>
                  <th>Справочная, {units}</th>
                  <th>Наблюдаемая, {units}</th>
                  <th>Δ, {units}</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((line, index) => (
                  <tr
                    key={`${line.element}-${line.wavelength_nm}-${index}`}
                    className={
                      active === page * 50 + index ? "line-active" : ""
                    }
                  >
                    <th>
                      <button
                        onFocus={() => setActive(page * 50 + index)}
                        onClick={() => setActive(page * 50 + index)}
                        aria-label={`Выделить ${line.element} ${line.wavelength_nm}`}
                      >
                        {line.element}
                      </button>
                    </th>
                    <td>{line.wavelength_nm.toFixed(4)}</td>
                    <td>{line.observed_peak_nm.toFixed(4)}</td>
                    <td>{line.delta_nm.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!lines.length && <p>Сопоставленных линий нет.</p>}
          <div className="actions">
            <button disabled={!page} onClick={() => setPage((p) => p - 1)}>
              Предыдущие линии
            </button>
            <span>
              Страница {page + 1} из {Math.max(1, Math.ceil(lines.length / 50))}
            </span>
            <button
              disabled={(page + 1) * 50 >= lines.length}
              onClick={() => setPage((p) => p + 1)}
            >
              Следующие линии
            </button>
          </div>
        </details>
      )}
    </>
  );
}
