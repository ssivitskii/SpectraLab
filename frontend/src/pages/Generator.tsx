import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, download } from "../api/client";
import { SpectrumChart } from "../components/Chart";
import { ErrorNotice, Loading } from "../components/Feedback";
const controls = [
  ["instrumental_fwhm_nm", "Аппаратная FWHM, nm", 0.5, 0.02, 5, 0.01],
  ["sampling_step_nm", "Шаг дискретизации, nm", 0.05, 0.005, 5, 0.005],
  ["snr_db", "SNR, dB", 20, -20, 100, 1],
  ["background_level", "Уровень фона", 0, -10, 10, 0.01],
  ["background_slope", "Наклон фона / nm", 0, -0.1, 0.1, 0.001],
  ["calibration_shift_nm", "Сдвиг калибровки, nm", 0, -1, 1, 0.01],
  ["amplitude_variation", "Вариация амплитуд", 0.08, 0, 1, 0.01],
  ["seed", "Random seed", 42, 0, 2147483647, 1],
] as const;
type Params = Record<(typeof controls)[number][0], number>;
export function Generator() {
  const elements = useQuery({ queryKey: ["elements"], queryFn: api.elements });
  const [selected, setSelected] = useState<string[]>([]),
    [weights, setWeights] = useState<Record<string, number>>({}),
    [clean, setClean] = useState(false);
  const [params, setParams] = useState<Params>(
    () =>
      Object.fromEntries(
        controls.map(([key, , value]) => [key, value]),
      ) as Params,
  );
  const generate = useMutation({ mutationFn: api.generate });
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    generate.mutate({
      ...params,
      elements: selected,
      component_weights: selected.map((e) => weights[e] ?? 1),
      snr_db: clean ? null : params.snr_db,
    });
  };
  const spectrum = generate.data;
  return (
    <>
      <header className="page-title">
        <div>
          <p className="eyebrow">СИНТЕТИЧЕСКИЕ ДАННЫЕ</p>
          <h1>Генератор спектра</h1>
          <p>Разделите влияние шума, оптики и дискретизации.</p>
        </div>
        <span className="tag">350–800 nm · vacuum</span>
      </header>
      <div className="workspace">
        <form className="panel controls" onSubmit={submit}>
          <h2>Параметры образца</h2>
          <p className="muted">Выберите от 1 до 3 элементов</p>
          <ErrorNotice error={elements.error} />
          {elements.isPending ? (
            <Loading />
          ) : (
            <div className="element-grid">
              {elements.data?.elements.map((element) => (
                <button
                  type="button"
                  key={element}
                  aria-label={`${element} I`}
                  aria-pressed={selected.includes(element)}
                  className={
                    selected.includes(element) ? "element selected" : "element"
                  }
                  disabled={
                    !selected.includes(element) && selected.length === 3
                  }
                  onClick={() =>
                    setSelected((current) =>
                      current.includes(element)
                        ? current.filter((e) => e !== element)
                        : [...current, element],
                    )
                  }
                >
                  {element}
                  <small>I</small>
                </button>
              ))}
            </div>
          )}
          {selected.map((element) => (
            <label key={element}>
              Вклад {element}
              <input
                type="number"
                min="0.01"
                max="100"
                step="0.01"
                value={weights[element] ?? 1}
                onChange={(e) =>
                  setWeights({ ...weights, [element]: Number(e.target.value) })
                }
                required
              />
            </label>
          ))}
          <hr />
          <h3>Условия измерения</h3>
          <label className="check">
            <input
              type="checkbox"
              checked={clean}
              onChange={(e) => setClean(e.target.checked)}
            />{" "}
            Чистый сигнал, без шума
          </label>
          {controls.map(([key, label, , min, max, step]) => (
            <label key={key}>
              {label}
              <input
                type="number"
                disabled={key === "snr_db" && clean}
                min={min}
                max={max}
                step={step}
                value={params[key]}
                onChange={(e) =>
                  setParams({ ...params, [key]: Number(e.target.value) })
                }
                required
              />
            </label>
          ))}
          <button
            className="primary full"
            disabled={selected.length === 0 || generate.isPending}
          >
            {generate.isPending ? "Генерация…" : "Сгенерировать спектр"}
          </button>
          <ErrorNotice error={generate.error} />
        </form>
        <section className="panel spectrum-panel">
          <div className="panel-heading">
            <h2>Сигнал образца</h2>
            <span className="tag">
              {spectrum
                ? `${spectrum.wavelength_nm.length.toLocaleString("ru")} точек`
                : "Нет данных"}
            </span>
          </div>
          {generate.isPending ? (
            <Loading children="Генерируем сигнал…" />
          ) : spectrum ? (
            <>
              <SpectrumChart spectrum={spectrum} />
              <div className="truth">
                <strong>Истинный состав (генератор)</strong>
                <span>
                  {Object.entries(spectrum.true_labels ?? {})
                    .filter(([, v]) => v)
                    .map(([e]) => e)
                    .join(" + ")}
                </span>
                <small>Эти метки не передаются распознаванию.</small>
              </div>
              <div className="actions">
                <Link
                  className="button primary"
                  to={`/analysis?spectrum=${spectrum.spectrum_id}`}
                >
                  Передать на анализ →
                </Link>
                <button
                  onClick={() =>
                    download(
                      "spectrum.csv",
                      "wavelength_nm,intensity\n" +
                        spectrum.wavelength_nm
                          .map((x, i) => `${x},${spectrum.intensity[i]}`)
                          .join("\n"),
                      "text/csv",
                    )
                  }
                >
                  Скачать CSV
                </button>
              </div>
            </>
          ) : (
            <div className="empty tall">
              <div className="spectrum-symbol">∿</div>
              <h3>Начните с виртуального образца</h3>
              <p>
                Выберите элементы и условия слева.
                <br />
                Здесь появятся чистый и наблюдаемый сигналы.
              </p>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
