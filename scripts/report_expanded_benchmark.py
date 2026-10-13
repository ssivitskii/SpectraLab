"""Render the completed robustness benchmark without fitting or selecting models."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

MODELS = ("baseline-nnls", "logistic-ovr", "random-forest")
LABELS = ("NNLS", "Logistic", "RF")


def render(run_dir: Path, destination: Path) -> None:
    result = json.loads((run_dir / "result.json").read_text())
    prereg = json.loads((run_dir / "preregistration.json").read_text())
    if result["status"] != "completed" or result["failed_runs"]:
        raise ValueError("A complete, matched benchmark is required")
    if (
        hashlib.sha256((run_dir / "preregistration.json").read_bytes()).hexdigest()
        != result["preregistration_sha256"]
    ):
        raise ValueError("Preregistration integrity mismatch")
    config = prereg["configuration"]
    if prereg["configuration_hash"] != (
        "6c342174b4bd2281cc5d53e0e675ba7328bd30d03168f14d6c22660cf6f66aa5"
    ):
        raise ValueError("This report template describes only the locked expanded-v1 configuration")
    suites = config["suites"]
    aggregates = result["aggregates"]

    def cell(suite: str, step: float, condition: str, model: str) -> dict:
        return next(
            row
            for row in aggregates
            if (row["suite_id"], row["grid_step_nm"], row["condition_id"], row["model_id"])
            == (suite, step, condition, model)
        )

    def metric(suite: str, step: float, condition: str, model: str, name: str) -> str:
        m = cell(suite, step, condition, model)["metrics"][name]
        return f"{m['pooled_estimate']:.3f} [{m['ci_low']:.3f}, {m['ci_high']:.3f}]"

    def table(header: list[str], rows: list[list[str]]) -> str:
        return "\n".join(
            ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
            + ["| " + " | ".join(row) + " |" for row in rows]
        )

    profile_rows, trace_rows, control_rows, limit_rows, seed_rows, support_rows = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for suite in suites:
        name = suite["suite_id"]
        for step in suite["grid_steps_nm"]:
            for condition in suite["conditions"]:
                cond = condition["condition_id"]
                profile_rows.append(
                    [name, f"{step:g}", cond]
                    + [metric(name, step, cond, model, "micro_f1") for model in MODELS]
                )
            for ratio in config["trace_ratios"]:
                if ratio:
                    trace_rows.append(
                        [name, f"{step:g}", f"{ratio:g}"]
                        + [
                            metric(name, step, f"trace-{ratio:g}", model, "trace_recall")
                            for model in MODELS
                        ]
                    )
            control_rows.append(
                [name, f"{step:g}"]
                + [
                    metric(name, step, "trace-0", model, "trace_false_positive_rate")
                    for model in MODELS
                ]
            )
            limits = [
                next(
                    row["smallest_tested_ratio_meeting_preregistered_point_criteria"]
                    for row in result["tested_trace_ratio_limits"]
                    if (row["suite_id"], row["grid_step_nm"], row["model_id"])
                    == (name, step, model)
                )
                for model in MODELS
            ]
            limit_rows.append(
                [name, f"{step:g}"] + [str(v) if v is not None else "нет" for v in limits]
            )
            seed_rows.append(
                [name, f"{step:g}"]
                + [
                    f"{m['mean']:.4f} ± {m['std']:.4f}"
                    for m in [
                        cell(name, step, "reference", model)["metrics"]["micro_f1"]
                        for model in MODELS
                    ]
                ]
            )
        first_step = suite["grid_steps_nm"][0]
        base_support = cell(name, first_step, "reference", MODELS[0])[
            "per_element_positive_base_support"
        ]
        target_support = np.zeros(len(suite["elements"]), dtype=int)
        for run in result["runs"]:
            if run["suite_id"] == name and run["grid_step_nm"] == first_step:
                condition = next(c for c in run["conditions"] if c["condition_id"] == "trace-0")
                with np.load(
                    run_dir / "runs" / run["path"] / "predictions.npz", allow_pickle=False
                ) as raw:
                    targets = raw[f"{condition['condition_index']}_trace_indices"][
                        :: config["variations"]
                    ]
                target_support += np.bincount(targets, minlength=len(target_support))
        support_rows.extend(
            [
                [name, element, str(base_support[element]), str(target_support[i])]
                for i, element in enumerate(suite["elements"])
            ]
        )

    grid_rows = []
    for row in result["paired_grid_differences"]:
        if row["condition_id"] in {"reference", "broad", "voigt-tails"}:
            grid_rows.append(
                [
                    row["suite_id"],
                    row["condition_id"],
                    row["model_id"],
                    f"{row['micro_f1_difference_fine_minus_coarse']:+.3f} "
                    f"[{row['ci_low']:+.3f}, {row['ci_high']:+.3f}]",
                ]
            )

    destination.parent.mkdir(parents=True, exist_ok=True)
    figure = destination.parent / "assets" / "expanded_trace_recall.png"
    figure.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(
        2, 2, figsize=(12, 8), sharex=True, sharey=True, constrained_layout=True
    )
    for suite, axis_row in zip(suites, axes, strict=True):
        for step, ax in zip(suite["grid_steps_nm"], axis_row, strict=True):
            for model, label, color in zip(
                MODELS, LABELS, ("#0072B2", "#D55E00", "#009E73"), strict=True
            ):
                ratios = sorted(r for r in config["trace_ratios"] if r)
                metrics = [
                    cell(suite["suite_id"], step, f"trace-{r:g}", model)["metrics"]["trace_recall"]
                    for r in ratios
                ]
                mean = [m["pooled_estimate"] for m in metrics]
                ax.plot(ratios, mean, marker="o", label=label, color=color)
                ax.fill_between(
                    ratios,
                    [m["ci_low"] for m in metrics],
                    [m["ci_high"] for m in metrics],
                    alpha=0.12,
                    color=color,
                )
            ax.axhline(
                config["trace_recall_target"], color="#777777", linewidth=0.8, linestyle="--"
            )
            ax.set(
                xscale="log", ylim=(-0.03, 1.03), title=f"{suite['suite_id']} | grid {step:g} nm"
            )
            ax.grid(alpha=0.2)
    axes[0, 0].legend(loc="upper left")
    for ax in axes[1]:
        ax.set_xlabel("Nominal integrated contribution ratio (not concentration)")
    for ax in axes[:, 0]:
        ax.set_ylabel("Targeted minor-component recall")
    fig.suptitle(
        "5 training seeds × 48 independent trace groups × 2 readouts\n"
        "95% seed/base-group bootstrap; thresholds fixed on development",
        fontsize=12,
    )
    fig.savefig(figure, dpi=160)
    plt.close(fig)
    profile_table = table(
        ["Сценарий", "Источник линий", "Сетка, nm", "Training FWHM, nm", "Test профили, nm"],
        [
            [
                "nist-window",
                "247 линий H/He/Na/Mg/Ca/Fe, 390–405 nm",
                "0.01; 0.05",
                "0.05",
                "Gaussian 0.02, 0.05, 0.20; Voigt G/L 0.05/0.05; response tilt",
            ],
            [
                "close-na-fe",
                "Na 392.67000 / Fe 392.67551 nm, окно 392.4–392.9",
                "0.001; 0.01",
                "0.01",
                "Gaussian 0.002, 0.01, 0.05; Voigt G/L 0.01/0.01",
            ],
        ],
    )
    duration = (
        datetime.fromisoformat(result["completed_at"])
        - datetime.fromisoformat(result["created_at"])
    ).total_seconds()
    verification = (
        "Для этого запуска дополнительная сверка сохранена в `verification.json`."
        if (run_dir / "verification.json").is_file()
        else "Дополнительный verification.json для этого запуска ещё не сохранён."
    )
    summary = {
        "schema_version": 1,
        "run_id": result["run_id"],
        "status": result["status"],
        "created_at": result["created_at"],
        "completed_at": result["completed_at"],
        "successful_seeds": result["successful_seeds"],
        "failed_runs": result["failed_runs"],
        "configuration": config,
        "configuration_sha256": prereg["configuration_hash"],
        "preregistration_sha256": result["preregistration_sha256"],
        "implementation_sha256": prereg["implementation_sha256"],
        "source_content_sha256": prereg["source_content_sha256"],
        "source_file_sha256": prereg["source_sha256"],
        "dependency_versions": result["dependency_versions"],
        "limitations": result["limitations"],
        "interval_method": prereg["interval_method"],
        "paired_comparisons": prereg["paired_comparisons"],
        "metric_estimands": {
            "mean_std": "mean and sample SD over the five seed metrics",
            "estimate_ci": "pooled metric; seed/base-group hierarchical percentile interval",
        },
        "aggregates": [
            {
                **{k: v for k, v in row.items() if k not in {"metrics", "successful_seeds"}},
                "metrics": {
                    metric: {
                        k: values[k]
                        for k in ("mean", "std", "count", "pooled_estimate", "ci_low", "ci_high")
                    }
                    for metric, values in row["metrics"].items()
                },
            }
            for row in aggregates
        ],
        "paired_model_differences": result["paired_model_differences"],
        "paired_grid_differences": result["paired_grid_differences"],
        "tested_trace_ratio_limits": result["tested_trace_ratio_limits"],
        "runs": [
            {
                k: run[k]
                for k in (
                    "seed",
                    "suite_id",
                    "grid_step_nm",
                    "split_hash",
                    "dataset_hash",
                    "source_hash",
                    "raw_predictions_sha256",
                    "path",
                )
            }
            for run in result["runs"]
        ],
        "verification": (
            json.loads((run_dir / "verification.json").read_text())
            if (run_dir / "verification.json").is_file()
            else None
        ),
    }
    summary_path = destination.with_name(destination.stem + "_summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )

    body = f"""# Расширенный benchmark — 2026-10-13

Выполнены все **20 из 20** комбинаций seed × сценарий × сетка: **60 моделей**,
**126 агрегированных ячеек**, пять полных seed, без failed/incomplete.
Этот эксперимент выявляет слабые примеси и неоднозначность близких линий,
которые не были видны в прежних сериях на 36–48 base samples. Данные синтетические;
реальные измерения рассматриваются отдельно в [ML08](external_validation.md).

## Заранее зафиксированные условия

[Конфиг](../ml/configs/expanded_benchmark.yaml) и локальный `preregistration.json`
зафиксированы **до первого прогноза**, в {result["created_at"]}. Это локальная фиксация
протокола, не публичная регистрация исследования. Выполнение закончено в
{result["completed_at"]}. После просмотра test ни сетка, ни пороги, ни модели
не перенастраивались; все запланированные условия приведены ниже.

Источник — сохранённые официальные выгрузки [NIST ASD](https://physics.nist.gov/PhysRefData/ASD/lines_form.html),
нейтральные линии в vacuum nm. Внутри каждого элемента сумма line priors
приведена к единице, поэтому веса означают **относительный интегральный вклад
сигнала**, а не концентрацию. Неуказанный prior принимается равным единице.
Исходные поля NIST и SHA родительского каталога сохранены в производных каталогах.

{profile_table}

В close-na-fe расстояние **0.00551 nm** взято из каталога. Другие переходы
намеренно исключены: это отдельная задача идентифицируемости двух линий,
а не утверждение о полном Na/Fe спектре. Response tilt линейно меняется
от 0.5 до 1.5 по окну; это контролируемая модель, не измеренная характеристика прибора.

Seeds: **7, 17, 29, 43, 71**. На каждый seed/сценарий генерируются **240 независимых
base compositions**: 144 train, 48 validation, 48 test; составы содержат 1–3 элемента
(1–2 для close-na-fe), непрерывные случайные веса 0.1–1.0. К каждой base composition
относятся два шумовых измерения. Отдельно создаются **48 независимых trace compositions**
с выбранными major/minor элементами и отношениями **1, 0.1, 0.03, 0.01, 0.003, 0**.
Нулевое отношение — отрицательный контроль именно выбранного minor элемента.
Всего 2 880 физических base/trace групп в двух задачах; повторение профилей и сеток
не увеличивает это число. В каждой итоговой ячейке **240 групп / 480 измерений**.

Line amplitudes имеют независимую логнормальную вариацию sigma=0.08; shift каждой
base composition непрерывен, sigma=0.003 nm и 0.0005 nm соответственно.
Шум Gaussian, RMS SNR=35 dB. Состав, line perturbations и shift совпадают между
моделями, профилями и сетками; шумовые реализации разных сеток независимы.
Профили интегрируются по пикселям теми же kernels, что основной генератор.
Номинальный trace ratio немного отличается от фактического в конечном окне;
его mean/min/max также сохранены в исходных отчётах.

NNLS использует шаблоны обучающего профиля; Logistic и RF обучаются на том же train.
Все пороги выбираются только по validation base IDs. После этого модель и пороги
сохраняются и неизменно применяются ко всем test профилям и trace ratios.
Для каждой сетки обучается отдельная модель с совпадающими split IDs.
CNN в этот фиксированный протокол не включена.

## Неопределённость и support

В таблицах `estimate [low, high]` — pooled metric и **95% percentile CI**, 1 000
иерархических bootstrap повторов: сначала resampling пяти training seeds,
затем физических групп внутри seed. Два readout одной группы всегда остаются вместе.
Для разностей моделей и сеток используются одинаковые bootstrap draws.
Модели в bootstrap не переобучаются. Отдельно сохранены mean по seeds и SD с `ddof=1`;
это другая оценка, её не следует смешивать с CI для pooled metric.

Интервалы exploratory, без поправки на множество сравнений. Пять training seeds
ограничивают точность покрытия. Bootstrap при наблюдаемом нуле ошибок может давать
[0, 0]: это **не доказательство нулевой истинной ошибки** и не верхняя граница риска.

Число физических групп с положительным элементом в ordinary test и число trace
групп, где именно этот элемент назначен minor (последний столбец в каждом сценарии
суммируется до 240):

{table(["Сценарий", "Элемент", "Positive test groups", "Targeted trace groups"], support_rows)}

Reference micro-F1, mean ± SD по пяти seeds:

{table(["Сценарий", "Шаг nm", *LABELS], seed_rows)}

## Изменение профиля

Micro-F1 с pooled CI; `reference` — профиль обучения. Новые профили проверяются
без адаптации шаблонов или порогов.

{table(["Сценарий", "Шаг nm", "Профиль", *LABELS], profile_rows)}

Для оценки разрешения близких линий одного F1 недостаточно: при предсказании обоих
классов для каждого спектра он может оставаться высоким. Дополнительные exact-match,
Hamming loss и разделение single/mixture сохранены в исходных per-seed metrics.

## Слабый компонент и отрицательный контроль

Здесь оценивается recall **назначенного minor элемента**, а не общий F1,
который может скрыть пропуски примеси за правильным major элементом.

![Trace recall и групповые доверительные интервалы](assets/expanded_trace_recall.png)

{table(["Сценарий", "Шаг nm", "Trace ratio", *LABELS], trace_rows)}

FPR назначенного minor при ratio=0 (240 отрицательных групп / 480 readouts):

{table(["Сценарий", "Шаг nm", *LABELS], control_rows)}

Заранее выбранный критерий: pooled trace recall ≥0.90 и absent-control FPR ≤0.05.
Ниже минимальное **из проверенных отношений**, удовлетворяющее обоим точечным
условиям. Это не непрерывная оценка предела, не концентрация и не instrument LoD;
CI не использовались для изменения порога или принятия решения.

{table(["Сценарий", "Шаг nm", *LABELS], limit_rows)}

Отсутствие подходящего отношения означает, что в этой проверке условие не выполнено;
оно не задаёт численный предел за пределами проверенной сетки отношений.

## Разрешение сетки

Ниже paired difference micro-F1 **fine − coarse** и CI. Положительная разность
означает больший F1 на fine grid. Все остальные paired model/grid differences
также сохранены в `result.json`.

{table(["Сценарий", "Профиль", "Модель", "ΔF1 [95% CI]"], grid_rows)}

Эти сравнения удерживают RMS SNR, а не время экспозиции или общий photon budget.
Поэтому более мелкая сетка не означает экспериментально измеренное улучшение прибора.
Результаты относятся к двум заданным окнам и распределениям составов; они не
распространяются автоматически на 350–800 nm, ионизованные спектры, другие примеси
или измеренные instrumental profiles.

## Воспроизведение и проверка

```bash
MPLCONFIGDIR=/tmp/spectralab-mpl OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \\
  VECLIB_MAXIMUM_THREADS=1 make expanded-benchmark
scripts/uv.sh run --frozen --all-packages --no-sync python \\
  scripts/report_expanded_benchmark.py <run-directory>
scripts/uv.sh run --frozen --all-packages --no-sync pytest \\
  ml/tests/test_expanded_benchmark.py -q
```

Время этого запуска — {duration:.1f} s. Inference timing хранит два прогрева,
семь повторов, median/p95 на спектр; это локальные wall-clock наблюдения.
В каталоге benchmark сохранены составы, splits, development/reference test arrays,
всего 60 model manifests/artifacts, исходные predictions и групповые sufficient statistics.
Reference test arrays в `development_data.npz` только хранятся рядом с train/validation;
в fit и выборе порогов они не участвуют. SHA каждого raw prediction NPZ проверяется
перед агрегацией. Большие артефакты намеренно остаются вне Git; все агрегаты,
CI, paired differences и исходные hashes сохранены в компактном
[summary JSON](expanded_benchmark_summary.json).

Запуск: `{result["run_id"]}` в `reports/verification/expanded/`.

- Protocol SHA-256: `{result["preregistration_sha256"]}`.
- Implementation SHA-256: `{prereg["implementation_sha256"]}`.
- Configuration SHA-256: `{prereg["configuration_hash"]}`.
- NIST content SHA-256: `{prereg["source_content_sha256"]}`.

{verification}
Unit tests проверяют совпадение vector renderer с production Gaussian/Voigt,
контроль отсутствия minor, grouped metrics и отсутствие искусственного сужения CI
при дублировании шумовых наблюдений.

Следующий отдельный протокол может исследовать физически согласованный photon budget
при смене сетки, measured instrumental line shape, более разнообразные training профили
и больше training seeds. Эти результаты можно использовать для выбора следующего
эксперимента; перенастройка по ним потребует нового независимого test.
"""
    destination.write_text(body, encoding="utf-8")
    print(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path, default=Path("docs/expanded_benchmark.md"))
    args = parser.parse_args()
    render(args.run_directory, args.output)


if __name__ == "__main__":
    main()
