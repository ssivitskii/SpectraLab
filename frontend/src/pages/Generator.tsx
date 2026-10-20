import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  api,
  ApiError,
  validationMessages,
  type Capabilities,
  type GenerateInput,
} from "../api/client";
import { SpectrumChart } from "../components/Chart";
import { ErrorNotice, Loading } from "../components/Feedback";
import { Icon } from "../components/Icon";
const elementNames: Record<string, string> = {
  H: "Водород",
  He: "Гелий",
  Na: "Натрий",
  Mg: "Магний",
  Ca: "Кальций",
  Fe: "Железо",
};
const controls = [
  ["instrumental_fwhm_nm", "Аппаратная FWHM, nm", 0.001, undefined, 0.001],
  ["sampling_step_nm", "Шаг дискретизации, nm", undefined, undefined, 0.001],
  ["snr_db", "SNR, dB", undefined, undefined, 1],
  ["background_level", "Уровень фона", undefined, undefined, 0.01],
  ["background_slope", "Наклон фона / nm", undefined, undefined, 0.001],
  ["calibration_shift_nm", "Сдвиг калибровки, nm", undefined, undefined, 0.01],
  ["amplitude_variation", "Вариация амплитуд", 0, undefined, 0.01],
  ["seed", "Random seed", undefined, undefined, 1],
] as const;
type Params = Record<(typeof controls)[number][0], number>;
type Physics = Pick<
  GenerateInput,
  | "profile"
  | "lorentzian_fwhm_nm"
  | "noise_model"
  | "photon_scale"
  | "read_noise_std"
  | "response_slope"
  | "saturation_level"
> & { ion_stage: number; };
type Preset = {
  physics: Physics;
  schema_version: number;
  profile: string;
  parameters: Params;
  selected: string[];
  weights: Record<string, number>;
  clean: boolean;
};
const presetKey = "spectralab.generator.preset.v1";
export function Generator() {
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: ({ signal }) => api.capabilities({ signal }),
  });
  return (
    <>
      <header className="page-title">
        <div>
          <h1>Генератор спектра</h1>
          <p>Разделите влияние шума, оптики и дискретизации.</p>
        </div>
        {capabilities.data && (
          <span className="tag">
            {capabilities.data.spectrum.wavelength_start_nm}–
            {capabilities.data.spectrum.wavelength_end_nm}{" "}
            {capabilities.data.spectrum.units} ·{" "}
            {capabilities.data.spectrum.wavelength_medium}
          </span>
        )}
      </header>
      <ErrorNotice error={capabilities.error} />
      {capabilities.error && (
        <button onClick={() => void capabilities.refetch()}>
          Повторить загрузку настроек
        </button>
      )}
      {capabilities.isPending && <Loading />}
      {capabilities.data && (
        <GeneratorForm
          key={JSON.stringify(capabilities.data)}
          capability={capabilities.data}
        />
      )}
    </>
  );
}
function GeneratorForm({ capability }: { capability: Capabilities; }) {
  const [search, setSearch] = useSearchParams();
  const id = search.get("spectrum") ?? "";
  const elements = useQuery({
    queryKey: ["elements"],
    queryFn: ({ signal }) => api.elements({ signal }),
  });
  const [selected, setSelected] = useState<string[]>([]),
    [weights, setWeights] = useState<Record<string, number>>({}),
    [clean, setClean] = useState(false);
  const [params, setParams] = useState<Params>(
    () =>
      Object.fromEntries(
        controls.map(([key]) => [
          key,
          Number(capability.generation.defaults[key] ?? 0),
        ]),
      ) as Params,
  );
  const [physics, setPhysics] = useState<Physics>(
    () =>
      Object.fromEntries(
        [
          "profile",
          "lorentzian_fwhm_nm",
          "noise_model",
          "photon_scale",
          "read_noise_std",
          "response_slope",
          "saturation_level",
          "ion_stage",
        ].map((key) => [key, capability.generation.defaults[key]]),
      ) as Physics,
  );
  const [presetError, setPresetError] = useState<Error | null>(null),
    [presetMessage, setPresetMessage] = useState("");
  const generate = useMutation({
    mutationFn: api.generate,
    onSuccess: (data) => setSearch({ spectrum: data.spectrum_id }),
  });
  const saved = useQuery({
    queryKey: ["spectrum", id],
    queryFn: ({ signal }) => api.spectrum(id, { signal }),
    enabled: !!id && generate.data?.spectrum_id !== id,
  });
  const spectrum =
    generate.data?.spectrum_id === id ? generate.data : saved.data;
  const exportSpectrum = useMutation({
    mutationFn: () => api.export("spectra", spectrum!.spectrum_id, "csv"),
  });
  const profileKey = JSON.stringify({
    range: capability.spectrum,
    reference: capability.reference,
    schema: capability.schema_versions,
  });
  const loadPreset = () => {
    try {
      const raw = localStorage.getItem(presetKey);
      if (!raw) throw new Error("Сохранённого пресета пока нет.");
      const value = JSON.parse(raw) as Preset;
      if (
        value.schema_version !== 1 ||
        value.profile !== profileKey ||
        !value.physics ||
        !capability.generation.profiles.includes(value.physics.profile) ||
        !capability.generation.noise_models.includes(
          value.physics.noise_model,
        ) ||
        !capability.generation.ion_stages.includes(value.physics.ion_stage)
      )
        throw new Error(
          "Пресет относится к другой версии схемы или профилю сервиса. Сохраните новый пресет.",
        );
      if (
        !Array.isArray(value.selected) ||
        !value.selected.length ||
        value.selected.length > capability.generation.max_components ||
        !value.selected.every((item) =>
          elements.data?.elements.includes(item),
        ) ||
        !controls.every(([key]) => Number.isFinite(value.parameters?.[key])) ||
        !value.selected.every(
          (item) =>
            Number.isFinite(value.weights?.[item] ?? 1) &&
            (value.weights[item] ?? 1) > 0,
        ) ||
        typeof value.clean !== "boolean"
      )
        throw new Error("Пресет повреждён или содержит недоступные параметры.");
      setPhysics(value.physics);
      setParams(value.parameters);
      setSelected(value.selected);
      setWeights(value.weights);
      setClean(value.clean);
      setPresetError(null);
      setPresetMessage(
        "Параметры восстановлены. Сгенерируйте спектр с тем же seed.",
      );
    } catch (error) {
      setPresetError(
        error instanceof Error
          ? error
          : new Error("Не удалось открыть пресет."),
      );
    }
  };
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const input = {
      ...capability.generation.defaults,
      ...params,
      ...physics,
      lorentzian_fwhm_nm:
        physics.profile === "gaussian" ? 0 : physics.lorentzian_fwhm_nm,
      elements: selected,
      component_weights: selected.map((name) => weights[name] ?? 1),
      snr_db:
        clean || physics.noise_model === "poisson_read" ? null : params.snr_db,
    } as GenerateInput;
    generate.mutate(input);
  };
  return (
    <div className="workspace generator-workspace">
      <form className="panel controls control-form" onSubmit={submit}>
        <section className="control-section">
          <div className="section-heading">
            <Icon name="flask" />
            <h2>Параметры образца</h2>
          </div>
          <p className="muted">
            Выберите от 1 до {capability.generation.max_components} элементов.
          </p>
          <ErrorNotice error={elements.error} />
          {elements.isPending ? (
            <Loading />
          ) : (
            <div className="element-grid" role="group" aria-label="Элементы образца">
              {elements.data?.elements.map((element) => (
                <button
                  type="button"
                  key={element}
                  aria-label={`${element} · стадия ${elements.data.ion_stage}`}
                  aria-pressed={selected.includes(element)}
                  className={
                    selected.includes(element) ? "element selected" : "element"
                  }
                  disabled={
                    !selected.includes(element) &&
                    selected.length >= capability.generation.max_components
                  }
                  onClick={() =>
                    setSelected((current) =>
                      current.includes(element)
                        ? current.filter((e) => e !== element)
                        : [...current, element],
                    )
                  }
                >
                  <span className="element-symbol">{element}</span>
                  <span className="element-name">{elementNames[element] ?? element}</span>
                </button>
              ))}
            </div>
          )}
          <p className="selection-summary" aria-live="polite">
            {selected.length ? `Выбрано: ${selected.join(" + ")}` : "Элементы пока не выбраны"}
          </p>
          <div className="weights-grid">
            {selected.map((element) => (
              <label key={element}>
                Вклад {element}
                <input
                  type="number"
                  min="0.000001"
                  step="any"
                  value={weights[element] ?? 1}
                  onChange={(e) =>
                    setWeights({ ...weights, [element]: Number(e.target.value) })
                  }
                  required
                />
              </label>
            ))}
          </div>
          {selected.length > 0 && <p className="muted">Вклады задают относительную амплитуду компонентов, а не концентрации.</p>}
          <button
            className="primary full"
            disabled={selected.length === 0 || generate.isPending}
          >
            <Icon name="play" />
            {generate.isPending ? "Генерация…" : "Сгенерировать спектр"}
          </button>
          <ErrorNotice error={generate.error} />
          <details className="control-details">
            <summary>Справочник элементов</summary>
            <p className="muted">
              Источник: {String(capability.reference.source ?? "—")} · стадия
              ионизации {elements.data?.ion_stage ?? "—"}.
            </p>
          </details>
        </section>
        <section className="control-section">
          <div className="section-heading">
            <Icon name="sliders" />
            <h2>Условия измерения</h2>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={clean}
              onChange={(e) => setClean(e.target.checked)}
            />
            Чистый сигнал, без шума
          </label>
          <div className="form-grid">
            {controls.map(([key, label, min, max, step]) => {
              const error = validationMessages(generate.error).find(
                (entry) => entry.field === key,
              );
              return (
                <label key={key}>
                  {label}
                  <input
                    id={`generator-${key}`}
                    type="number"
                    disabled={
                      key === "snr_db" &&
                      (clean || physics.noise_model === "poisson_read")
                    }
                    min={
                      key === "seed"
                        ? capability.generation.seed_min
                        : key === "sampling_step_nm"
                          ? capability.generation.min_sampling_step_nm
                          : (capability.generation.limits?.[key]?.minimum ?? min)
                    }
                    max={
                      key === "seed"
                        ? capability.generation.seed_max
                        : (capability.generation.limits?.[key]?.maximum ?? max)
                    }
                    step={key === "sampling_step_nm" ? "any" : step}
                    value={params[key]}
                    aria-invalid={!!error}
                    aria-describedby={error ? `error-${key}` : undefined}
                    onChange={(e) =>
                      setParams({ ...params, [key]: Number(e.target.value) })
                    }
                    required
                  />
                  {error && (
                    <span id={`error-${key}`} className="field-error">
                      {error.message}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
          <details className="control-details">
            <summary>Профиль линии и детектор</summary>
            <label>
              Профиль линии
              <select
                value={physics.profile}
                onChange={(e) =>
                  setPhysics({
                    ...physics,
                    profile: e.target.value as Physics["profile"],
                  })
                }
              >
                {capability.generation.profiles.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            {physics.profile === "voigt" && (
              <label>
                Лоренцева FWHM, nm
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={physics.lorentzian_fwhm_nm}
                  onChange={(e) =>
                    setPhysics({
                      ...physics,
                      lorentzian_fwhm_nm: Number(e.target.value),
                    })
                  }
                  required
                />
              </label>
            )}
            <label>
              Модель шума
              <select
                value={physics.noise_model}
                onChange={(e) =>
                  setPhysics({
                    ...physics,
                    noise_model: e.target.value as Physics["noise_model"],
                  })
                }
              >
                {capability.generation.noise_models.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            {physics.noise_model === "poisson_read" && (
              <>
                <label>
                  Масштаб фотонов
                  <input
                    type="number"
                    min="0.000001"
                    step="any"
                    value={physics.photon_scale}
                    onChange={(e) =>
                      setPhysics({
                        ...physics,
                        photon_scale: Number(e.target.value),
                      })
                    }
                    required
                  />
                </label>
                <label>
                  Шум считывания, std
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={physics.read_noise_std}
                    onChange={(e) =>
                      setPhysics({
                        ...physics,
                        read_noise_std: Number(e.target.value),
                      })
                    }
                    required
                  />
                </label>
                <p className="muted">
                  Poisson/read noise задаётся масштабом фотонов и шумом
                  считывания.
                </p>
              </>
            )}
            <label>
              Наклон отклика детектора
              <input
                type="number"
                min="-1"
                max="1"
                step="any"
                value={physics.response_slope}
                onChange={(e) =>
                  setPhysics({
                    ...physics,
                    response_slope: Number(e.target.value),
                  })
                }
                required
              />
            </label>
            <label>
              Уровень насыщения (пусто — отключено)
              <input
                type="number"
                min="0.000001"
                step="any"
                value={physics.saturation_level ?? ""}
                onChange={(e) =>
                  setPhysics({
                    ...physics,
                    saturation_level:
                      e.target.value === "" ? null : Number(e.target.value),
                  })
                }
              />
            </label>
            <label>
              Стадия ионизации
              <select
                value={physics.ion_stage}
                onChange={(e) =>
                  setPhysics({ ...physics, ion_stage: Number(e.target.value) })
                }
              >
                {capability.generation.ion_stages.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            <p className="muted">Доступны только стадии текущего справочника.</p>
          </details>
        </section>
        <section className="control-section">
          <details className="control-details">
            <summary>Пресет генератора</summary>
            <p className="muted">
              Сохраняется в этом браузере вместе с версией схемы и профилем
              сервиса.
            </p>
            <div className="actions">
              <button
                type="button"
                disabled={!selected.length}
                onClick={() => {
                  try {
                    const value: Preset = {
                      physics,
                      schema_version: 1,
                      profile: profileKey,
                      parameters: params,
                      selected,
                      weights,
                      clean,
                    };
                    localStorage.setItem(presetKey, JSON.stringify(value));
                    setPresetError(null);
                    setPresetMessage("Пресет сохранён.");
                  } catch {
                    setPresetError(
                      new ApiError(
                        "Браузер запретил сохранение пресета.",
                        "storage_unavailable",
                      ),
                    );
                  }
                }}
              >
                Сохранить пресет
              </button>
              <button
                type="button"
                disabled={!elements.data}
                onClick={loadPreset}
              >
                Восстановить пресет
              </button>
            </div>
            <p role="status">{presetMessage}</p>
            <ErrorNotice error={presetError} />
          </details>
        </section>
      </form>
      <section className="panel spectrum-panel generator-canvas">
        <div className="panel-heading">
          <div>
            <h2>Сигнал образца</h2>
            <p className="muted">
              {spectrum ? <>Сохранённый спектр · <span className="mono" title={spectrum.spectrum_id}>{spectrum.spectrum_id.slice(0, 12)}</span></> : "Синтетическое измерение с известным составом"}
            </p>
          </div>
          <span className="tag">
            {spectrum
              ? `${spectrum.wavelength_nm.length.toLocaleString("ru")} точек`
              : "Нет данных"}
          </span>
        </div>
        <ErrorNotice error={saved.error} />
        {generate.isPending || (id && saved.isPending && !spectrum) ? (
          <Loading>Генерируем сигнал…</Loading>
        ) : spectrum ? (
          <>
            <p className="draft-notice">
              На графике сохранённый спектр. Параметры формы применятся только при новой генерации.
            </p>
            <SpectrumChart
              key={spectrum.spectrum_id}
              spectrum={spectrum}
              units={capability.spectrum.units}
              medium={capability.spectrum.wavelength_medium}
            />
            <div className="truth">
              <div>
                <strong>Истинный состав (генератор)</strong>
                <p className="muted">Эти метки не передаются распознаванию.</p>
              </div>
              <span className="truth-elements">
                {Object.entries(spectrum.true_labels ?? {})
                  .filter(([, v]) => v)
                  .map(([name]) => name)
                  .join(" + ")}
              </span>
            </div>
            <details className="source-details">
              <summary>Параметры сохранённого спектра</summary>
              <dl className="metadata">
                <dt>ID образца</dt><dd className="mono">{spectrum.spectrum_id}</dd>
                <dt>Источник сигнала</dt><dd>{spectrum.origin === "uploaded" ? "Загруженный CSV" : "Генератор"}</dd>
                <dt>Справочник</dt><dd>{spectrum.reference_source}</dd>
              </dl>
              <pre>{JSON.stringify(spectrum.parameters, null, 2)}</pre>
            </details>
            <div className="actions">
              <Link
                className="button primary"
                to={`/analysis?spectrum=${spectrum.spectrum_id}`}
                aria-label="Передать на анализ →"
              >
                Передать на анализ
                <Icon name="arrowRight" />
              </Link>
              <button
                disabled={exportSpectrum.isPending}
                onClick={() => exportSpectrum.mutate()}
              >
                <Icon name="download" />
                Скачать CSV
              </button>
            </div>
            <ErrorNotice error={exportSpectrum.error} />
          </>
        ) : (
          <div className="empty tall spectrum-empty">
            <div className="empty-symbol" aria-hidden="true"><Icon name="flask" /></div>
            <h3>Начните с виртуального образца</h3>
            <p>
              Выберите элементы и условия измерения.
              После генерации здесь появятся чистый и наблюдаемый сигналы.
            </p>
            <ol className="empty-steps">
              <li className="empty-step"><Icon name="plus" /><span>Задайте состав</span></li>
              <li className="empty-step"><Icon name="sliders" /><span>Настройте измерение</span></li>
              <li className="empty-step"><Icon name="spectrum" /><span>Сравните сигналы</span></li>
            </ol>
            <p className="empty-hint">Меняйте один параметр за раз, чтобы увидеть его влияние на спектр.</p>
          </div>
        )}
      </section>
    </div>
  );
}
