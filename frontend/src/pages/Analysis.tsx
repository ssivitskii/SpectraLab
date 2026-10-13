import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  useInfiniteQuery,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  api,
  download,
  validateUpload,
  type MeasurementMetadata,
  type Prediction,
  type Spectrum,
} from "../api/client";
import { SpectrumChart } from "../components/Chart";
import { ErrorNotice, Loading } from "../components/Feedback";
export function Analysis() {
  const [search, setSearch] = useSearchParams();
  const id = search.get("spectrum") ?? "";
  return (
    <>
      <header className="page-title">
        <div>
          <h1>Анализ спектра</h1>
          <p>От измерения к оценкам присутствия элементов.</p>
        </div>
        <Link className="button" to="/generator">
          Создать образец →
        </Link>
      </header>
      <AnalysisWorkspace
        key={id}
        id={id}
        onSelect={(value) => setSearch({ spectrum: value })}
      />
    </>
  );
}
function AnalysisWorkspace({
  id,
  onSelect,
}: {
  id: string;
  onSelect: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState(["builtin-nnls"]),
    [attempt, setAttempt] = useState(0),
    [mediumOverride, setMedium] = useState<string>();
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: ({ signal }) => api.capabilities({ signal }),
  });
  const models = useQuery({
    queryKey: ["models"],
    queryFn: ({ signal }) => api.models({ signal }),
  });
  const spectrum = useQuery({
    queryKey: ["spectrum", id],
    queryFn: ({ signal }) => api.spectrum(id, { signal }),
    enabled: !!id,
  });
  const history = useInfiniteQuery({
    queryKey: ["spectra"],
    queryFn: ({ pageParam, signal }) => api.spectra(pageParam, { signal }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const ready = selected.filter((model) =>
    models.data?.some((item) => item.model_id === model && item.ready),
  );
  const predictions = useQueries({
    queries: ready.map((model) => ({
      queryKey: ["prediction", id, model, attempt],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        api.predict(id, model, { signal }),
      enabled: !!spectrum.data && attempt > 0,
      retry: false,
      staleTime: Infinity,
    })),
  });
  const result = predictions[0]?.data;
  const medium =
    mediumOverride ?? capabilities.data?.spectrum.wavelength_medium ?? "";
  const upload = useMutation({
    mutationFn: async (file: File) => {
      if (!capabilities.data)
        throw new Error("Сначала загрузите возможности сервиса.");
      await validateUpload(file, medium, capabilities.data);
      return api.upload(file, medium, capabilities.data.spectrum.units);
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ["spectra"] });
      onSelect(data.spectrum_id);
    },
  });
  return (
    <div className="workspace">
      <aside className="panel controls">
        <h2>Входной спектр</h2>
        <ErrorNotice error={capabilities.error} />
        {capabilities.data && (
          <p className="muted">
            CSV · {capabilities.data.spectrum.csv_columns.join(",")}
            <br />
            Полное покрытие {capabilities.data.spectrum.wavelength_start_nm}–
            {capabilities.data.spectrum.wavelength_end_nm}{" "}
            {capabilities.data.spectrum.units}, до{" "}
            {(
              capabilities.data.spectrum.max_upload_bytes / 1_000_000
            ).toLocaleString("ru")}{" "}
            MB.
          </p>
        )}
        <label>
          Система длин волн
          <select
            value={medium}
            disabled={!capabilities.data}
            onChange={(e) => setMedium(e.target.value)}
          >
            <option value={capabilities.data?.spectrum.wavelength_medium ?? ""}>
              {capabilities.data?.spectrum.wavelength_medium ?? "Загрузка…"}
            </option>
          </select>
        </label>
        {capabilities.data && (
          <p className="muted">
            Поддерживается среда {capabilities.data.spectrum.wavelength_medium}.
            Преобразуйте другие среды явно до загрузки.
          </p>
        )}
        <label className="file-input">
          Загрузить CSV
          <input
            aria-label="Загрузить CSV"
            aria-invalid={!!upload.error}
            aria-describedby="upload-error"
            type="file"
            accept=".csv,text/csv"
            disabled={upload.isPending || !capabilities.data}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) upload.mutate(file);
              event.target.value = "";
            }}
          />
        </label>
        {upload.isPending && <Loading>Проверка и загрузка спектра…</Loading>}
        <ErrorNotice id="upload-error" error={upload.error} />
        <details>
          <summary>История спектров</summary>
          <ErrorNotice error={history.error} />
          {history.isPending && <Loading />}
          {history.data?.pages
            .flatMap((p) => p.items)
            .map((item) => (
              <button
                key={item.spectrum_id}
                className={`run-choice ${id === item.spectrum_id ? "active" : ""}`}
                onClick={() => onSelect(item.spectrum_id)}
              >
                <strong>
                  {item.measurement_metadata?.sample_id ||
                    item.spectrum_id.slice(0, 12)}
                </strong>
                <small>
                  {item.origin} · {item.reference_source}
                </small>
                <small>{new Date(item.created_at).toLocaleString("ru")}</small>
              </button>
            ))}
          {history.data?.pages[0].items.length === 0 && (
            <p>История пока пуста.</p>
          )}
          {history.hasNextPage && (
            <button
              disabled={history.isFetchingNextPage}
              onClick={() => void history.fetchNextPage()}
            >
              Ещё спектры
            </button>
          )}
        </details>
        <hr />
        <h2>Модель распознавания</h2>
        {models.isPending ? (
          <Loading />
        ) : (
          <label>
            Метод
            <select
              value={selected[0] ?? ""}
              onChange={(event) => {
                setSelected([event.target.value]);
                setAttempt(0);
              }}
            >
              {models.data?.map((item) => (
                <option
                  key={item.model_id}
                  value={item.model_id}
                  disabled={!item.ready}
                >
                  {item.model_id} · v{item.version ?? "—"}
                  {!item.ready ? " · недоступна" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        <ErrorNotice error={models.error} />
        {models.data
          ?.filter((item) => !item.ready)
          .map((item) => (
            <p className="muted" key={item.model_id}>
              {item.model_id}: {item.unavailable_reason ?? "Модель не обучена"}
              {item.diagnostic_code && <> ({item.diagnostic_code})</>}
            </p>
          ))}
        <details>
          <summary>Сравнить несколько моделей</summary>
          {models.data
            ?.filter((item) => item.ready)
            .map((item) => (
              <label className="check" key={item.model_id}>
                <input
                  type="checkbox"
                  checked={selected.includes(item.model_id)}
                  onChange={(event) => {
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, item.model_id]
                        : current.filter((v) => v !== item.model_id),
                    );
                    setAttempt(0);
                  }}
                />
                {item.model_id} · v{item.version}
              </label>
            ))}
        </details>
        {selected.map((model) => {
          const item = models.data?.find((entry) => entry.model_id === model);
          return item ? (
            <p key={model} className="muted">
              {item.model_id}: {item.data_source} ·{" "}
              {item.score_kind ?? "Оценка недоступна"}
              <br />
              Область: {item.applicability}
            </p>
          ) : null;
        })}
        <button
          className="primary full"
          disabled={
            !spectrum.data ||
            !ready.length ||
            predictions.some((p) => p.isFetching)
          }
          onClick={() => setAttempt((v) => v + 1)}
        >
          {predictions.some((p) => p.isFetching)
            ? "Распознавание…"
            : "Распознать элементы"}
        </button>
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
                : spectrum.data.parameters.noise_model === "poisson_read"
                  ? "Не задан; Poisson/read noise"
                  : String(spectrum.data.parameters.snr_db ?? "Без шума")}
            </dd>
            <dt>ID образца</dt>
            <dd className="mono">{id}</dd>
            <dt>Справочник</dt>
            <dd>{spectrum.data.reference_source}</dd>
          </dl>
        )}
      </aside>
      <div className="main-column">
        <section className="panel spectrum-panel">
          <div className="panel-heading">
            <h2>Эмиссионный спектр</h2>
            {capabilities.data && (
              <span className="tag">
                {medium} · {capabilities.data.spectrum.units}
              </span>
            )}
          </div>
          <ErrorNotice error={spectrum.error} />
          {spectrum.error && (
            <button onClick={() => void spectrum.refetch()}>
              Повторить загрузку
            </button>
          )}
          {id && spectrum.isPending ? (
            <Loading>Загрузка спектра…</Loading>
          ) : spectrum.data ? (
            <>
              <SpectrumChart
                key={id}
                spectrum={spectrum.data}
                prediction={result}
                medium={medium}
                units={capabilities.data?.spectrum.units}
              />
              <SpectrumMetadata key={id} spectrum={spectrum.data} />
            </>
          ) : (
            <div className="empty tall">
              <h3>Спектр ещё не выбран</h3>
              <p>Загрузите измерение или создайте синтетический образец.</p>
              <Link to="/generator">Открыть генератор →</Link>
            </div>
          )}
        </section>
        {attempt > 0 && ready.length > 1 && (
          <p className="notice">
            Сравнивайте обнаруженный состав. Оценки разных типов имеют
            собственные шкалы; их нельзя усреднять или ранжировать как общую
            уверенность.
          </p>
        )}
        {attempt > 0 &&
          predictions.map((query, index) => (
            <section
              className="panel result"
              aria-label={
                index === 0
                  ? "Результат распознавания"
                  : `Результат ${ready[index]}`
              }
              key={ready[index]}
            >
              <h2>{ready[index]}</h2>
              {query.isFetching && (
                <Loading>Модель обрабатывает спектр…</Loading>
              )}
              <ErrorNotice error={query.error} />
              {query.error && (
                <button onClick={() => void query.refetch()}>
                  Повторить {ready[index]}
                </button>
              )}
              {query.data && <PredictionResult result={query.data} />}
            </section>
          ))}
      </div>
    </div>
  );
}
function SpectrumMetadata({ spectrum }: { spectrum: Spectrum }) {
  const client = useQueryClient();
  const [metadata, setMetadata] = useState<MeasurementMetadata>(
    spectrum.measurement_metadata ?? {},
  );
  const save = useMutation({
    mutationFn: () => api.metadata(spectrum.spectrum_id, metadata),
    onSuccess: (data) => {
      client.setQueryData(["spectrum", spectrum.spectrum_id], data);
      void client.invalidateQueries({ queryKey: ["spectra"] });
    },
  });
  const exportSpectrum = useMutation({
    mutationFn: (format: string) =>
      api.export("spectra", spectrum.spectrum_id, format),
  });
  return (
    <>
      <details>
        <summary>Метаданные измерения</summary>
        <form
          className="metadata-form"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          {(
            [
              ["sample_id", "Образец"],
              ["instrument_id", "Прибор"],
              ["session_id", "Сессия"],
              ["calibration", "Калибровка"],
              ["notes", "Примечания"],
            ] as const
          ).map(([field, label]) => (
            <label key={field}>
              {label}
              <input
                type="text"
                maxLength={
                  field === "notes"
                    ? 4000
                    : field === "calibration"
                      ? 2000
                      : 200
                }
                value={metadata[field] ?? ""}
                onChange={(e) => {
                  setMetadata({ ...metadata, [field]: e.target.value || null });
                  save.reset();
                }}
              />
            </label>
          ))}
          <button disabled={save.isPending}>Сохранить метаданные</button>
          {save.isSuccess && <p role="status">Метаданные сохранены.</p>}
          <ErrorNotice error={save.error} />
        </form>
        <p className="muted">
          Это сведения пользователя. Вычисленные характеристики хранятся
          отдельно; SNR загруженного CSV остаётся неизвестным.
        </p>
      </details>
      <div className="actions">
        <button
          disabled={exportSpectrum.isPending}
          onClick={() => exportSpectrum.mutate("zip")}
        >
          Экспорт исследования ZIP
        </button>
        <button
          disabled={exportSpectrum.isPending}
          onClick={() => exportSpectrum.mutate("csv")}
        >
          Экспорт спектра CSV
        </button>
      </div>
      <ErrorNotice error={exportSpectrum.error} />
    </>
  );
}
function PredictionResult({ result }: { result: Prediction }) {
  const exported = useMutation({
    mutationFn: async () => {
      if (result.prediction_id)
        await api.export("predictions", result.prediction_id, "json");
      else
        download(
          "spectralab-result.json",
          JSON.stringify(result, null, 2),
          "application/json",
        );
    },
  });
  return (
    <>
      <div className="detections">
        {result.detected_elements.length ? (
          result.detected_elements.map((name) => (
            <span className="detected" key={name}>
              {name}
            </span>
          ))
        ) : (
          <p>Поддерживаемые элементы не обнаружены.</p>
        )}
      </div>
      <p className="muted">
        {result.model_id} · v{result.model_version} · {result.reference_source}{" "}
        · {result.processing_time_ms.toFixed(1)} ms
      </p>
      <p>
        Тип оценки: <code>{result.score_kind}</code>.{" "}
        {result.calibrated
          ? "Применена калибровка; область проверки указана в модели."
          : "Не калибрована; не является вероятностью."}
      </p>
      <div className="table-scroll">
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
              <tr key={score.element}>
                <th>{score.element}</th>
                <td className="mono">{score.value.toFixed(4)}</td>
                <td className="mono">
                  {result.thresholds[score.element]?.toFixed(4) ?? "—"}
                </td>
                <td>
                  {result.detected_elements.includes(score.element)
                    ? "Обнаружен"
                    : "Не обнаружен"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {result.warnings.map((warning, index) => (
        <p className="warning" key={index}>
          {warning}
        </p>
      ))}
      <button disabled={exported.isPending} onClick={() => exported.mutate()}>
        Экспорт результата JSON
      </button>
      <ErrorNotice error={exported.error} />
    </>
  );
}
