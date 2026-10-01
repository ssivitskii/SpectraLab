import { useEffect, useRef, useState } from "react";
import type { Data, Layout } from "plotly.js";
import type { Spectrum, Prediction } from "../api/client";
export function Chart({
  data,
  layout = {},
  label = "График",
}: {
  data: Data[];
  layout?: Partial<Layout>;
  label?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const node = ref.current;
    let cancelled = false;
    let dispose: () => void = () => {};
    if (!node) return;
    import("plotly.js-basic-dist-min")
      .then(async (Plotly) => {
        if (cancelled) return;
        await Plotly.react(
          node,
          data,
          {
            autosize: true,
            height: 430,
            margin: { l: 65, r: 24, t: 24, b: 60 },
            paper_bgcolor: "#fff",
            plot_bgcolor: "#fff",
            font: { family: "system-ui", color: "#334155", size: 12 },
            hovermode: "closest",
            legend: { orientation: "h", y: 1.12 },
            ...layout,
          },
          {
            responsive: true,
            displaylogo: false,
            scrollZoom: true,
            toImageButtonOptions: { format: "png", filename: "spectralab" },
          },
        );
        dispose = () => Plotly.purge(node);
      })
      .catch(() => setError("Не удалось загрузить график"));
    const observer = new ResizeObserver(() => {
      if (node)
        import("plotly.js-basic-dist-min").then((p) => {
          if (!cancelled) p.Plots.resize(node);
        });
    });
    observer.observe(node);
    return () => {
      cancelled = true;
      observer.disconnect();
      dispose();
    };
  }, [data, layout]);
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
}: {
  spectrum: Spectrum;
  prediction?: Prediction;
}) {
  const [references, setReferences] = useState(true);
  const data: Data[] = [
    {
      x: spectrum.wavelength_nm,
      y: spectrum.intensity,
      name: "Наблюдаемый",
      type: "scatter",
      mode: "lines",
      line: { color: "#1d4ed8", width: 1.4 },
    },
  ];
  if (spectrum.clean_signal)
    data.push({
      x: spectrum.wavelength_nm,
      y: spectrum.clean_signal,
      name: "Чистый сигнал",
      type: "scatter",
      mode: "lines",
      line: { color: "#0f766e", width: 1.4 },
    });
  const lines = prediction?.matched_reference_lines ?? [];
  const shapes: Partial<Layout>["shapes"] = references
    ? lines.map((line) => ({
        type: "line",
        x0: Number(line.wavelength_nm),
        x1: Number(line.wavelength_nm),
        y0: 0,
        y1: 1,
        yref: "paper",
        line: { color: "#b45309", width: 1, dash: "dot" },
      }))
    : [];
  return (
    <>
      <div className="chart-toolbar">
        <span>Прокрутка — zoom · панель графика — pan · легенда — серии</span>
        {prediction && (
          <label>
            <input
              type="checkbox"
              checked={references}
              onChange={(e) => setReferences(e.target.checked)}
            />{" "}
            Справочные линии ({lines.length})
          </label>
        )}
      </div>
      <Chart
        data={data}
        label="Спектр образца"
        layout={{
          xaxis: {
            title: { text: "Длина волны, nm · vacuum" },
            gridcolor: "#e2e8f0",
            zeroline: false,
          },
          yaxis: {
            title: { text: "Интенсивность, усл. ед." },
            gridcolor: "#f1f5f9",
            zerolinecolor: "#cbd5e1",
          },
          shapes,
          uirevision: spectrum.spectrum_id,
        }}
      />
    </>
  );
}
