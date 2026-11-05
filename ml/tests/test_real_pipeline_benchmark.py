from __future__ import annotations

import json
from pathlib import Path

from spectralab_ml.evaluation.real_pipeline_benchmark import RealPipelineBenchmark


def test_real_pipeline_benchmark_execution(tmp_path: Path):
    benchmark = RealPipelineBenchmark()
    summary = benchmark.run_benchmark(max_standards=6, output_dir=tmp_path)

    assert summary.standards_evaluated_count == 6
    assert len(summary.pipeline_stages) == 8
    assert summary.mean_temperature_k > 4000.0
    assert 0.0 <= summary.mean_purity_score <= 1.0

    # Verify metrics exist for key oxides
    assert "FeOT" in summary.oxide_metrics
    assert "MgO" in summary.oxide_metrics
    assert "CaO" in summary.oxide_metrics

    fe_m = summary.oxide_metrics["FeOT"]
    assert fe_m.sample_count == 6
    assert fe_m.rmse >= 0.0
    assert fe_m.mae >= 0.0
    assert fe_m.mean_interval_width > 0.0
    # Conformal empirical coverage should be reasonable (> 50%)
    assert fe_m.coverage_95_percent >= 50.0

    # Verify exported files exist and are valid
    summary_file = tmp_path / "pipeline_benchmark_summary.json"
    assert summary_file.is_file()
    data = json.loads(summary_file.read_text(encoding="utf-8"))
    assert data["standards_evaluated_count"] == 6

    csv_file = tmp_path / "predicted_vs_true.csv"
    assert csv_file.is_file()
    lines = csv_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 6  # header + data lines

    md_file = tmp_path / "pipeline_benchmark_report.md"
    assert md_file.is_file()
    content = md_file.read_text(encoding="utf-8")
    assert "# Сквозной бенчмарк приборного конвейера ChemCam Replay" in content
