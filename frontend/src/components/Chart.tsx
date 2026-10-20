import { useEffect, useMemo, useRef, useState } from "react";
import type { Data, Layout, PlotHoverEvent, PlotRelayoutEvent } from "plotly.js";
import { displaySeries } from "./plotData";
import { chartColors, photonChartLayout } from "./chartTheme";
import type { Spectrum, Prediction } from "../api/client";
const emptyLayout: Partial<Layout> = {};
export function Chart({
  data,
  layout = emptyLayout,
  label = "График",
  onHover,
  onRelayout,
}: {
  data: Data[];
  layout?: Partial<Layout>;
  label?: string;
  onHover?: (event: PlotHoverEvent) => void;
  onRelayout?: (event: PlotRelayoutEvent) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [engine, setEngine] = useState<typeof import("plotly.js") | null>(null);
  const hover = useRef(onHover);
  const relayout = useRef(onRelayout);
  hover.current = onHover;
  relayout.current = onRelayout;
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
      .react(node, data, photonChartLayout(layout), {
        responsive: true,
        displaylogo: false,
        scrollZoom: true,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
        toImageButtonOptions: { format: "png", filename: "spectralab" },
      })
      .then(() => {
        if (!cancelled) {
          const plot = node as unknown as {
            removeAllListeners?: (name: string) => void;
            on?: {
              (
                name: "plotly_hover",
                callback: (event: PlotHoverEvent) => void,
              ): void;
              (
                name: "plotly_relayout",
                callback: (event: PlotRelayoutEvent) => void,
              ): void;
            };
          };
          plot.removeAllListeners?.("plotly_hover");
          plot.removeAllListeners?.("plotly_relayout");
          plot.on?.("plotly_hover", (event) => hover.current?.(event));
          plot.on?.("plotly_relayout", (event) => relayout.current?.(event));
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
    <div className="chart" role="region" aria-label={label}>
      {error && <p role="alert">{error}</p>}
      <div ref={ref} className="chart-plot" />
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
    [reset, setReset] = useState(0),
    [dragmode, setDragmode] = useState<"zoom" | "pan">("zoom");
  const [from, setFrom] = useState(String(spectrum.wavelength_nm[0])),
    [to, setTo] = useState(String(spectrum.wavelength_nm.at(-1)));
  const displayUnits = spectrum.units ?? units;
  const wavelengthMedium = spectrum.wavelength_medium ?? medium;
  const displayMedium =
    wavelengthMedium === "vacuum"
      ? "вакуум"
      : wavelengthMedium === "air"
        ? "воздух"
        : wavelengthMedium;
  const validRange =
    from.trim() !== "" &&
    to.trim() !== "" &&
    Number.isFinite(Number(from)) &&
    Number.isFinite(Number(to)) &&
    Number(from) < Number(to);
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
        line: { color: chartColors.observed, width: 1.6 },
      },
    ];
    if (clean && spectrum.clean_signal)
      result.push({
        ...displaySeries(spectrum.wavelength_nm, spectrum.clean_signal),
        uid: "clean",
        name: "Чистый сигнал",
        type: "scatter",
        mode: "lines",
        line: { color: chartColors.clean, width: 1.4 },
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
        line: { color: chartColors.reference, width: 1, dash: "dot" },
        hovertemplate: "%{text} · %{x:.4f}<extra>Справочник</extra>",
      });
    }
    return result;
  }, [spectrum, clean, references, lines]);
  const layout = useMemo<Partial<Layout>>(
    () => ({
      dragmode,
      xaxis: {
        title: {
          text: `Длина волны, ${displayUnits}${displayMedium ? ` · ${displayMedium}` : ""}`,
        },
        zeroline: false,
        ...(range ? { range, autorange: false } : {}),
      },
      yaxis: {
        title: { text: "Интенсивность, усл. ед." },
      },
      shapes:
        references && active !== null && lines[active]
          ? [
              {
                type: "line",
                x0: lines[active].wavelength_nm,
                x1: lines[active].wavelength_nm,
                y0: 0,
                y1: 1,
                yref: "paper",
                line: { color: chartColors.active, width: 2 },
              },
            ]
          : [],
      uirevision: `${spectrum.spectrum_id}-${reset}`,
    }),
    [
      spectrum,
      displayUnits,
      displayMedium,
      range,
      active,
      lines,
      reset,
      references,
      dragmode,
    ],
  );
  return (
    <>
      <div className="chart-toolbar">
        <div className="chart-modes" role="group" aria-label="Режим графика">
          <button
            type="button"
            className="chart-mode"
            aria-pressed={dragmode === "zoom"}
            onClick={() => setDragmode("zoom")}
            title="Выделите область мышью или задайте диапазон ниже"
          >
            <svg
              viewBox="0 0 20 20"
              width="18"
              height="18"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              aria-hidden="true"
            >
              <circle cx="8.5" cy="8.5" r="5.5" />
              <path d="m13 13 4 4M8.5 6v5M6 8.5h5" />
            </svg>
            Масштаб
          </button>
          <button
            type="button"
            className="chart-mode"
            aria-pressed={dragmode === "pan"}
            onClick={() => setDragmode("pan")}
            title="Перемещайте видимый участок спектра"
          >
            <svg
              viewBox="0 0 20 20"
              width="18"
              height="18"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              aria-hidden="true"
            >
              <path d="M10 2v16M2 10h16M7 5l3-3 3 3M7 15l3 3 3-3M5 7l-3 3 3 3M15 7l3 3-3 3" />
            </svg>
            Перемещение
          </button>
        </div>
        <div className="chart-layers" role="group" aria-label="Слои спектра">
          {spectrum.clean_signal && (
            <label className="chart-switch">
              <input
                type="checkbox"
                checked={clean}
                onChange={(e) => setClean(e.target.checked)}
              />
              Чистый сигнал
            </label>
          )}
          {prediction && (
            <label className="chart-switch">
              <input
                type="checkbox"
                checked={references}
                onChange={(e) => setReferences(e.target.checked)}
              />
              Справочные линии ({lines.length})
            </label>
          )}
        </div>
      </div>
      <Chart
        data={data}
        layout={layout}
        label="Спектр образца"
        onRelayout={(event) => {
          if (event.dragmode === "zoom" || event.dragmode === "pan")
            setDragmode(event.dragmode);
        }}
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
          if (validRange) setRange([Number(from), Number(to)]);
        }}
      >
        <div className="range-fields" role="group" aria-label="Диапазон спектра">
          <label>
            От, {displayUnits}
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
            До, {displayUnits}
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
        </div>
        <div className="range-actions">
          <button type="submit" disabled={!validRange}>
            Показать диапазон
          </button>
          <button
            type="button"
            onClick={() => {
              setRange(undefined);
              setFrom(String(spectrum.wavelength_nm[0]));
              setTo(String(spectrum.wavelength_nm.at(-1)));
              setReset((v) => v + 1);
            }}
          >
            Сбросить масштаб
          </button>
        </div>
      </form>
      {spectrum.wavelength_nm.length > 4000 && (
        <p className="muted chart-note">
          График показывает огибающую максимумов и минимумов (до 4 000 точек).
          Распознавание и экспорт используют все{" "}
          {spectrum.wavelength_nm.length.toLocaleString("ru")} точек.
        </p>
      )}
      {prediction && (
        <details className="reference-lines">
          <summary>Таблица справочных линий ({lines.length})</summary>
          <label className="reference-filter">
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
                  <th>Справочная, {displayUnits}</th>
                  <th>Наблюдаемая, {displayUnits}</th>
                  <th>Δ, {displayUnits}</th>
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
                        type="button"
                        onFocus={() => setActive(page * 50 + index)}
                        onClick={() => setActive(page * 50 + index)}
                        aria-pressed={active === page * 50 + index}
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
