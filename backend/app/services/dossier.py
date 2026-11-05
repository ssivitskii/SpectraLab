from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from spectralab_ml.models.calibration_transfer import CalibrationTransferResult
from spectralab_ml.models.explainability import (
    ExplainabilityReport,
    analyze_spectral_explainability,
)
from spectralab_ml.models.quantitative import get_default_quantitative_regressor
from spectralab_ml.simulation.diagnostics import analyze_plasma_diagnostics


class DossierStorage:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict[str, Any]] = {}

    def generate_dossier(
        self,
        wave: np.ndarray,
        flux: np.ndarray,
        *,
        spectrum_id: str | None = None,
        transfer_profile_id: str = "identity",
        plasma_element: str = "auto",
        plasma_fwhm_nm: float = 0.10,
        quantitative_targets: list[str] | None = None,
        confidence_level: float = 0.95,
        medium: str = "vacuum",
        transfer_result: CalibrationTransferResult | None = None,
    ) -> dict[str, Any]:
        """Generate comprehensive measurement dossier covering provenance, assay, and lines."""
        dossier_id = f"dos_{uuid.uuid4().hex[:12]}"
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()

        # SHA-256 of processed spectral arrays
        sha = hashlib.sha256()
        sha.update(wave.tobytes())
        sha.update(flux.tobytes())
        spectrum_sha256 = sha.hexdigest()

        # 1. Calibration transfer diagnostics
        transfer_dto = None
        if transfer_result is not None:
            diag = transfer_result.diagnostics
            transfer_dto = {
                "shift_detected_nm": diag.shift_detected_nm,
                "rmsd_change": diag.rmsd_change,
                "mean_correction_factor": diag.mean_correction_factor,
                "window_half_width": diag.window_half_width,
                "alpha": diag.alpha,
                "applied_method": diag.applied_method,
                "condition_number": diag.condition_number,
            }

        # 2. Spectral Explainability & NIST line attribution
        explain_report: ExplainabilityReport = analyze_spectral_explainability(
            wave, flux, medium=medium
        )
        explain_dto = explain_report.to_dict()

        # 3. Plasma Diagnostics (LTE & Boltzmann)
        plasma_diag = analyze_plasma_diagnostics(
            wave,
            flux,
            element=plasma_element,
            instrumental_fwhm_nm=plasma_fwhm_nm,
            medium=medium,
        )
        plasma_summary = plasma_diag.summary
        plasma_summary_dto = {
            "temperature_k": plasma_summary["temperature_k"],
            "temperature_uncertainty_k": plasma_summary["temperature_uncertainty_k"],
            "electron_density_cm3": plasma_summary["electron_density_cm3"],
            "element": plasma_summary["element"],
            "boltzmann_r_squared": plasma_summary["boltzmann_r_squared"],
            "boltzmann_points_count": plasma_summary["boltzmann_points_count"],
            "lte_valid": plasma_summary["lte_valid"],
            "diagnostics_ready": plasma_summary["diagnostics_ready"],
        }

        # 4. Quantitative Composition Assay (Ridge Conformal)
        regressor = get_default_quantitative_regressor()
        quant_report = regressor.predict(
            wave,
            flux,
            targets=quantitative_targets,
            confidence_level=confidence_level,
            medium=medium,
        )
        quant_dto = quant_report.to_dict()

        # 5. Executive summary synthesis
        elem_count = len(explain_report.detected_elements)
        t_val = plasma_summary["temperature_k"]
        t_err = plasma_summary["temperature_uncertainty_k"]
        te_str = (
            f"Te = {t_val:.0f} ± {t_err:.0f} K"
            if plasma_summary["diagnostics_ready"]
            else "Te не определена"
        )
        ne_str = (
            f", ne = {plasma_summary['electron_density_cm3']:.2e} см⁻³"
            if plasma_summary["electron_density_cm3"] is not None
            else ""
        )
        lte_str = "LTE подтверждено" if plasma_summary["lte_valid"] else "LTE не подтверждено"
        quant_mass = quant_report.total_detected_mass_wt_percent

        exec_summary = (
            f"Комплексное досье спектрального измерения: обнаружено {elem_count} элементов "
            f"({explain_report.summary}). "
            f"Параметры плазмы: {te_str}{ne_str} ({lte_str}). "
            f"Суммарная масса оксидов: {quant_mass:.2f} wt% (доверие {confidence_level:.0%})."
        )

        # 6. Markdown Report Generation
        quant_header = (
            f"**Модель:** `{quant_report.model_id}` | "
            f"**Доверие:** {confidence_level:.0%} | "
            f"**Сумма оксидов:** {quant_mass:.2f} wt%"
        )
        elements_str = (
            ", ".join(explain_report.detected_elements)
            if explain_report.detected_elements
            else "нет"
        )

        md_lines = [
            f"# Измерительное досье спектра {dossier_id}",
            "",
            f"**Дата генерации (UTC):** `{now_utc}`  ",
            f"**Идентификатор спектра:** `{spectrum_id or 'Прямой массив'}`  ",
            f"**Криптографический хэш (SHA-256):** `{spectrum_sha256}`  ",
            f"**Спектральный диапазон:** {wave[0]:.2f} – {wave[-1]:.2f} нм ({len(wave)} точек)  ",
            f"**Профиль переноса калибровки:** `{transfer_profile_id}`  ",
            "",
            "## 1. Резюме измерения",
            exec_summary,
            "",
            "## 2. Физическое состояние плазмы (LTE)",
            f"- **Электронная температура $T_e$:** {te_str}",
            f"- **Электронная плотность $n_e$:** {ne_str if ne_str else 'N/A'}",
            f"- **Критерий МакВиртера (LTE):** "
            f"{'Выполнен' if plasma_summary['lte_valid'] else 'Не выполнен'}",
            f"- **Коэффициент детерминации $R^2$ графика Больцмана:** "
            f"{plasma_summary['boltzmann_r_squared']:.4f}",
            "",
            "## 3. Количественный состав (Assay)",
            quant_header,
            "",
            "| Оксид | Концентрация (wt%) | Доверительный интервал | Статус области (AD) | "
            "Leverage $h(x)$ |",
            "| :--- | :---: | :---: | :---: | :---: |",
        ]
        for pred in quant_report.predictions:
            md_lines.append(
                f"| **{pred.species}** | {pred.value_wt_percent:.3f} | "
                f"[{pred.lower_wt_percent:.3f} – {pred.upper_wt_percent:.3f}] | "
                f"`{pred.status}` | {pred.leverage:.2f} |"
            )

        md_lines.extend(
            [
                "",
                "## 4. Спектральная атрибуция аналитических линий",
                f"- **Чистота спектра:** {explain_report.spectral_purity_score:.1%}",
                f"- **Обнаруженные элементы:** {elements_str}",
                f"- **Число обнаруженных спектральных наложений:** {explain_report.blend_count}",
                "",
                "| Линия (NIST) | $\\lambda_{vac}$ (нм) | $\\lambda_{meas}$ (нм) | Peak | "
                "Фон | Net | SBR | Наложение |",
                "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
            ]
        )
        for line in explain_report.lines:
            if line.detected:
                blend_str = (
                    f"Да ({', '.join(line.interfering_elements)})" if line.is_blend else "Нет"
                )
                md_lines.append(
                    f"| {line.line_name} | {line.wavelength_vacuum_nm:.2f} | "
                    f"{line.measured_wavelength_nm:.2f} | {line.peak_intensity:.1f} | "
                    f"{line.background_intensity:.1f} | {line.net_intensity:.1f} | "
                    f"{line.signal_to_background_ratio:.2f} | {blend_str} |"
                )

        markdown_report = "\n".join(md_lines)

        dossier_data: dict[str, Any] = {
            "dossier_id": dossier_id,
            "spectrum_id": spectrum_id,
            "created_at_utc": now_utc,
            "sha256": spectrum_sha256,
            "point_count": len(wave),
            "wavelength_range_nm": [float(wave[0]), float(wave[-1])],
            "transfer": transfer_dto,
            "plasma": plasma_summary_dto,
            "quantitative": quant_dto,
            "explainability": explain_dto,
            "executive_summary": exec_summary,
            "markdown_report": markdown_report,
        }

        # Cache in memory and write to disk
        self._cache[dossier_id] = dossier_data
        out_file = self.directory / f"{dossier_id}.json"
        out_file.write_text(
            json.dumps(dossier_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return dossier_data

    def get(self, dossier_id: str) -> dict[str, Any] | None:
        if dossier_id in self._cache:
            return self._cache[dossier_id]
        path = self.directory / f"{dossier_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._cache[dossier_id] = data
            return data
        except (OSError, json.JSONDecodeError):
            return None

    def render_html(self, dossier: dict[str, Any]) -> str:
        """Render a clean HTML document suitable for browser preview or PDF printing."""
        title = f"Измерительное досье — {dossier['dossier_id']}"
        now = dossier["created_at_utc"]
        sha = dossier["sha256"]
        wl_range = dossier["wavelength_range_nm"]
        exec_sum = dossier["executive_summary"]
        plasma = dossier.get("plasma") or {}
        quant = dossier.get("quantitative") or {}
        explain = dossier.get("explainability") or {}

        # Quant table rows
        quant_rows = []
        for p in quant.get("predictions", []):
            st_color = (
                "#10b981"
                if p["status"] == "in_domain"
                else ("#f59e0b" if p["status"] == "near_boundary" else "#ef4444")
            )
            val = p["value_wt_percent"]
            low = p["lower_wt_percent"]
            high = p["upper_wt_percent"]
            st = p["status"]
            lev = p["leverage"]
            badge_style = (
                f"background:{st_color}22;color:{st_color};padding:2px 6px;"
                "border-radius:4px;font-size:12px;font-weight:600;"
            )
            quant_rows.append(
                f"<tr>"
                f"<td style='font-weight:600;'>{p['species']}</td>"
                f"<td>{val:.3f}</td>"
                f"<td>[{low:.3f} – {high:.3f}]</td>"
                f"<td><span style='{badge_style}'>{st}</span></td>"
                f"<td>{lev:.2f}</td>"
                f"</tr>"
            )
        quant_table = "\n".join(quant_rows)

        # Lines table rows
        line_rows = []
        for line in explain.get("lines", []):
            if line.get("detected"):
                is_blend = line.get("is_blend", False)
                interf = ", ".join(line.get("interfering_elements", []))
                blend_badge = (
                    f"<span style='color:#ef4444;font-weight:600;'>Да ({interf})</span>"
                    if is_blend
                    else "<span style='color:#10b981;'>Нет</span>"
                )
                line_rows.append(
                    f"<tr>"
                    f"<td>{line['line_name']}</td>"
                    f"<td>{line['wavelength_vacuum_nm']:.2f}</td>"
                    f"<td>{line['measured_wavelength_nm']:.2f}</td>"
                    f"<td>{line['peak_intensity']:.1f}</td>"
                    f"<td>{line['background_intensity']:.1f}</td>"
                    f"<td>{line['net_intensity']:.1f}</td>"
                    f"<td style='font-weight:600;'>{line['signal_to_background_ratio']:.2f}</td>"
                    f"<td>{blend_badge}</td>"
                    f"</tr>"
                )
        lines_table = "\n".join(line_rows)

        t_k = plasma.get("temperature_k", 0)
        t_u = plasma.get("temperature_uncertainty_k", 0)
        ne_val = plasma.get("electron_density_cm3", "N/A")
        lte_txt = "Соблюден" if plasma.get("lte_valid") else "Не соблюден"
        r2_val = plasma.get("boltzmann_r_squared", 0)
        purity_val = explain.get("spectral_purity_score", 1.0)
        blend_num = explain.get("blend_count", 0)

        css = (
            "body { font-family: -apple-system, sans-serif; margin: 40px auto; "
            "max-width: 960px; color: #1e293b; background: #fff; line-height: 1.5; }\n"
            "h1 { color: #0f172a; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }\n"
            "h2 { color: #1e293b; margin-top: 32px; border-bottom: 1px solid #e2e8f0; }\n"
            ".provenance { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; "
            "padding: 16px; margin-bottom: 24px; font-size: 14px; }\n"
            ".summary-box { background: #eff6ff; border-left: 4px solid #3b82f6; "
            "padding: 12px 16px; margin-bottom: 24px; font-size: 15px; color: #1e3a8a; }\n"
            "table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }\n"
            "th, td { padding: 8px 12px; border: 1px solid #e2e8f0; text-align: left; }\n"
            "th { background: #f8fafc; font-weight: 600; color: #475569; }\n"
            "tr:nth-child(even) { background: #fcfcfd; }\n"
        )
        sample_str = dossier.get("spectrum_id") or "Прямой спектральный массив"
        n_pts = dossier["point_count"]
        wl_str = f"{wl_range[0]:.2f} – {wl_range[1]:.2f} нм ({n_pts} точек)"
        purity_str = (
            f"<strong>Чистота спектра:</strong> {purity_val:.1%} | "
            f"<strong>Бленды/наложения:</strong> {blend_num}"
        )
        html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>{css}</style>
</head>
<body>
  <h1>{title}</h1>
  <div class="provenance">
    <div><strong>Дата генерации (UTC):</strong> {now}</div>
    <div><strong>Хэш спектра (SHA-256):</strong> <code>{sha}</code></div>
    <div><strong>Диапазон длин волн:</strong> {wl_str}</div>
    <div><strong>Идентификатор образца:</strong> {sample_str}</div>
  </div>

  <h2>1. Резюме измерения</h2>
  <div class="summary-box">{exec_sum}</div>

  <h2>2. Физическое состояние плазмы (LTE)</h2>
  <ul>
    <li><strong>Электронная температура $T_e$:</strong> {t_k:.0f} ± {t_u:.0f} K</li>
    <li><strong>Плотность электронов $n_e$:</strong> {ne_val}</li>
    <li><strong>Критерий МакВиртера:</strong> {lte_txt}</li>
    <li><strong>Коэффициент Больцмана $R^2$:</strong> {r2_val:.4f}</li>
  </ul>

  <h2>3. Количественный анализ состава (Assay)</h2>
  <table>
    <thead>
      <tr>
        <th>Оксид</th>
        <th>Массовая доля (wt%)</th>
        <th>Конформный доверительный интервал</th>
        <th>Область применимости</th>
        <th>Leverage $h(x)$</th>
      </tr>
    </thead>
    <tbody>
      {quant_table}
    </tbody>
  </table>

  <h2>4. Спектральная атрибуция аналитических линий NIST</h2>
  <p>{purity_str}</p>
  <table>
    <thead>
      <tr>
        <th>Переход NIST</th>
        <th>$\\lambda_{{vac}}$ (нм)</th>
        <th>$\\lambda_{{meas}}$ (нм)</th>
        <th>Пик</th>
        <th>Фон</th>
        <th>Net</th>
        <th>SBR</th>
        <th>Наложение</th>
      </tr>
    </thead>
    <tbody>
      {lines_table}
    </tbody>
  </table>
</body>
</html>
"""
        return html

    def render_csv(self, dossier: dict[str, Any]) -> str:
        """Render CSV string with all cataloged line measurements and attribution."""
        output = io.StringIO()
        fieldnames = [
            "element",
            "ion_stage",
            "line_name",
            "target_oxide",
            "wavelength_vacuum_nm",
            "measured_wavelength_nm",
            "peak_intensity",
            "background_intensity",
            "net_intensity",
            "signal_to_background_ratio",
            "detected",
            "is_blend",
            "interfering_elements",
            "relative_contribution_score",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        lines = (dossier.get("explainability") or {}).get("lines", [])
        for line in lines:
            row = dict(line)
            row["interfering_elements"] = ";".join(row.get("interfering_elements", []))
            writer.writerow(row)

        return output.getvalue()
