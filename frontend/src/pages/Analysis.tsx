import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, download } from "../api/client";
import { SpectrumChart } from "../components/Chart";
import { ErrorNotice, Loading } from "../components/Feedback";
export function Analysis() {
  const [search, setSearch] = useSearchParams(),
    id = search.get("spectrum") ?? "";
  const [model, setModel] = useState("builtin-nnls"),
    [medium, setMedium] = useState("vacuum");
  const models = useQuery({ queryKey: ["models"], queryFn: api.models });
  const spectrum = useQuery({
    queryKey: ["spectrum", id],
    queryFn: () => api.spectrum(id),
    enabled: !!id,
  });
  const predict = useMutation({
    mutationFn: async () => ({
      spectrumId: id,
      modelId: model,
      prediction: await api.predict(id, model),
    }),
  });
  const upload = useMutation({
    mutationFn: (file: File) => api.upload(file, medium),
    onSuccess: (data) => {
      predict.reset();
      setSearch({ spectrum: data.spectrum_id });
    },
  });
  const result =
    predict.data?.spectrumId === id && predict.data.modelId === model
      ? predict.data.prediction
      : undefined;
  return (
    <>
      <header className="page-title">
        <div>
          <p className="eyebrow">СПЕКТРАЛЬНАЯ ИДЕНТИФИКАЦИЯ</p>
          <h1>Анализ спектра</h1>
          <p>От измерения к оценкам присутствия элементов.</p>
        </div>
        <Link className="button" to="/generator">
          Создать образец →
        </Link>
      </header>
      <div className="workspace">
        <aside className="panel controls">
          <h2>Входной спектр</h2>
          <p className="muted">
            CSV · wavelength_nm,intensity
            <br />
            Полное покрытие 350–800 nm, до 5 MB.
          </p>
          <label>
            Система длин волн
            <select value={medium} onChange={(e) => setMedium(e.target.value)}>
              <option value="vacuum">Вакуум, nm</option>
              <option value="air">Воздух (не поддерживается)</option>
            </select>
          </label>
          <label className="file-input">
            Загрузить CSV
            <input
              aria-label="Загрузить CSV"
              type="file"
              accept=".csv,text/csv"
              disabled={upload.isPending}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) upload.mutate(file);
              }}
            />
          </label>
          {upload.isPending && <Loading children="Загрузка спектра…" />}
          <ErrorNotice error={upload.error} />
          <hr />
          <h2>Модель распознавания</h2>
          {models.isPending ? (
            <Loading />
          ) : (
            <label>
              Метод
              <select
                value={model}
                onChange={(e) => {
                  setModel(e.target.value);
                  predict.reset();
                }}
              >
                {models.data?.map((item) => (
                  <option
                    key={item.model_id}
                    value={item.model_id}
                    disabled={!item.ready}
                  >
                    {item.model_id}
                    {!item.ready ? " · не обучена" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <ErrorNotice error={models.error} />
          <p className="muted">
            Обучаемые модели становятся доступны после <code>make demo</code>.
          </p>
          <button
            className="primary full"
            disabled={!spectrum.data || predict.isPending || models.isPending}
            onClick={() => predict.mutate()}
          >
            {predict.isPending ? "Распознавание…" : "Распознать элементы"}
          </button>
          <ErrorNotice error={predict.error} />
          {spectrum.data && (
            <dl className="metadata">
              <dt>Источник сигнала</dt>
              <dd>
                {spectrum.data.origin === "uploaded"
                  ? "Загруженный CSV"
                  : "Генератор"}
              </dd>
              <dt>SNR</dt>
              <dd>
                {spectrum.data.origin === "uploaded"
                  ? "Неизвестен"
                  : String(spectrum.data.parameters.snr_db ?? "Без шума")}
              </dd>
              <dt>ID образца</dt>
              <dd className="mono">{id.slice(0, 12)}…</dd>
            </dl>
          )}
        </aside>
        <div className="main-column">
          <section className="panel spectrum-panel">
            <div className="panel-heading">
              <h2>Эмиссионный спектр</h2>
              <span className="tag">vacuum · nm</span>
            </div>
            <ErrorNotice error={spectrum.error} />
            {id && spectrum.isPending ? (
              <Loading children="Загрузка спектра…" />
            ) : spectrum.data ? (
              <SpectrumChart spectrum={spectrum.data} prediction={result} />
            ) : (
              <div className="empty tall">
                <div className="spectrum-symbol">∿</div>
                <h3>Спектр ещё не выбран</h3>
                <p>Загрузите измерение или создайте синтетический образец.</p>
                <Link to="/generator">Открыть генератор →</Link>
              </div>
            )}
          </section>
          {predict.isPending && (
            <Loading children="Модель обрабатывает спектр…" />
          )}
          {result && (
            <section
              className="panel result"
              aria-label="Результат распознавания"
            >
              <div className="panel-heading">
                <h2>Результат распознавания</h2>
                <span className="tag">
                  {result.processing_time_ms.toFixed(1)} ms
                </span>
              </div>
              <div className="detections">
                {result.detected_elements.length ? (
                  result.detected_elements.map((e) => (
                    <span className="detected" key={e}>
                      {e}
                    </span>
                  ))
                ) : (
                  <p>Поддерживаемые элементы не обнаружены.</p>
                )}
              </div>
              <p className="muted">
                {result.model_id} · v{result.model_version} ·{" "}
                {result.reference_source}
              </p>
              <p>
                Тип оценки: <code>{result.score_kind}</code>.{" "}
                {result.calibrated
                  ? "Калибрована"
                  : "Не калибрована; не является вероятностью."}
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Элемент</th>
                    <th>Score</th>
                    <th>Порог</th>
                    <th>Решение</th>
                  </tr>
                </thead>
                <tbody>
                  {result.scores.map((score) => (
                    <tr key={String(score.element)}>
                      <th>{String(score.element)}</th>
                      <td className="mono">{Number(score.value).toFixed(4)}</td>
                      <td className="mono">
                        {result.thresholds[String(score.element)].toFixed(4)}
                      </td>
                      <td>
                        {result.detected_elements.includes(
                          String(score.element),
                        )
                          ? "Обнаружен"
                          : "Не обнаружен"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {result.warnings.map((warning, i) => (
                <p className="warning" key={i}>
                  {warning}
                </p>
              ))}
              <button
                onClick={() =>
                  download(
                    "spectralab-result.json",
                    JSON.stringify(result, null, 2),
                    "application/json",
                  )
                }
              >
                Экспорт результата JSON
              </button>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
