# Аналитика

Воспроизводимые сценарии находятся в `scripts/01_reference_data.py`,
`02_synthetic_spectra.py`, `03_model_comparison.py`. Запуск: `make analytics`
после `make demo`. CSV, JSON и PNG сохраняются в `reports/analytics/`.
Они импортируют общий ML-пакет и не реализуют отдельный генератор.

При необходимости интерактивной работы установите опциональную зависимость:

```sh
uv sync --frozen --all-packages --extra notebooks
uv run --package spectralab-ml --extra notebooks jupyter lab
```

Jupyter не требуется для API, тестов или аналитических сценариев.
