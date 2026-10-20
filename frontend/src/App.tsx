import { useEffect, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api/client";
import { Analysis } from "./pages/Analysis";
import { Generator } from "./pages/Generator";
import { Experiments } from "./pages/Experiments";
import { BatchAnalysis } from "./pages/BatchAnalysis";
import { SourceNotice } from "./components/Feedback";
import { Icon, SpectrumMark, type IconName } from "./components/Icon";

const sections: { path: string; label: string; description: string; icon: IconName }[] = [
  { path: "/analysis", label: "Анализ спектра", description: "Сигнал и элементы", icon: "spectrum" },
  { path: "/generator", label: "Генератор", description: "Синтетические образцы", icon: "flask" },
  { path: "/experiments", label: "Эксперименты", description: "Модели и сравнение", icon: "experiments" },
  { path: "/batch", label: "Пакетный анализ", description: "Очереди измерений", icon: "layers" },
];

export function App() {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  const current = sections.find((section) => section.path === location.pathname) ?? sections[0];
  const capabilities = useQuery({ queryKey: ["capabilities"], queryFn: ({ signal }) => api.capabilities({ signal }) });
  useEffect(() => { document.title = current.label + " · SpectraLab"; }, [current.label]);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace">К рабочей области</a>
      <aside className="sidebar">
        <div className="sidebar-brand-row">
          <Link className="brand" to="/analysis" onClick={() => setMenuOpen(false)}><SpectrumMark /><span>SpectraLab</span></Link>
          <button className="mobile-menu" type="button" aria-label={menuOpen ? "Закрыть меню" : "Открыть меню"} aria-controls="primary-navigation" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}><Icon name={menuOpen ? "close" : "menu"} /></button>
        </div>
        <div className="sidebar-workspace"><span className="workspace-avatar"><Icon name="grid" size={17} /></span><span>Локальная лаборатория<small>Рабочая среда</small></span></div>
        <nav id="primary-navigation" className="primary-navigation" aria-label="Основная навигация" data-open={menuOpen}>
          {sections.map((section) => <NavLink key={section.path} to={section.path} onClick={() => setMenuOpen(false)}><Icon name={section.icon} /><span>{section.label}<small>{section.description}</small></span></NavLink>)}
        </nav>
        <div className="sidebar-bottom">
          <a href="#methodology"><Icon name="info" size={18} />Методика и ограничения</a>
          <a href="/docs" target="_blank" rel="noreferrer"><Icon name="arrowUpRight" size={18} />Документация API</a>
          <div className="local-mode"><Icon name="settings" size={16} /><span>Один компьютер<small>Исследовательский режим</small></span></div>
        </div>
      </aside>
      <div className="app-content">
        <header className="context-bar">
          <div className="breadcrumb"><span>Рабочая среда</span><span aria-hidden="true">/</span><strong>{current.label}</strong></div>
          <div className="context-details">
            {capabilities.data && <span className="context-medium">{capabilities.data.spectrum.wavelength_medium} <span aria-hidden="true">·</span> {capabilities.data.spectrum.units}</span>}
            <span className={"connection " + (capabilities.isError ? "connection-error" : capabilities.isPending ? "connection-pending" : "connection-ready")} role="status"><Icon name="activity" size={16} />{capabilities.isPending ? "Подключение…" : capabilities.isError ? "API недоступен" : "API доступен"}</span>
            {capabilities.isError && <button className="icon-button" aria-label="Повторить подключение к API" onClick={() => void capabilities.refetch()}><Icon name="refresh" size={17} /></button>}
          </div>
        </header>
        <main id="workspace" tabIndex={-1}>
          <Routes>
            <Route path="/analysis" element={<Analysis />} />
            <Route path="/generator" element={<Generator />} />
            <Route path="/experiments" element={<Experiments />} />
            <Route path="/batch" element={<BatchAnalysis />} />
            <Route path="*" element={<Navigate to="/analysis" replace />} />
          </Routes>
          <div className="research-context"><SourceNotice source={capabilities.data ? String(capabilities.data.reference.source ?? "") : undefined} /></div>
          <section className="methodology-section" aria-label="Методика и происхождение">
            <details className="methodology" id="methodology">
              <summary><Icon name="info" size={18} />Методика и ограничения</summary>
              <div className="details-body">
                <p>Задача multi-label для классов выбранного справочника. Несколько элементов могут присутствовать одновременно. NNLS сопоставляет спектр со справочными шаблонами; Logistic Regression и Random Forest обучаются на синтетических данных.</p>
                <p>Диапазон, единицы и система длин волн определяются профилем сервиса. В генераторе независимы аппаратное уширение, дискретизация и шум. Относительные интенсивности — условный prior. Score не равен вероятности; пороги обучаемых артефактов выбираются на validation.</p>
                <p>Справочные линии — диагностическое сопоставление, а не доказанное объяснение ML-решения. Пустое обнаружение не доказывает отсутствие элементов в образце или наличие неизвестного элемента. Определение концентраций и перенос на реальные измерения не реализованы.</p>
              </div>
            </details>
            {capabilities.data && <details className="methodology"><summary><Icon name="file" size={18} />Справочник и версии схем</summary><pre>{JSON.stringify({ reference: capabilities.data.reference, schema_versions: capabilities.data.schema_versions, range: capabilities.data.spectrum }, null, 2)}</pre></details>}
          </section>
        </main>
        <footer className="app-footer"><span>SpectraLab <span className="footer-divider">/</span> Локальная лаборатория</span><span>Данные. Модели. Воспроизводимость.</span></footer>
      </div>
    </div>
  );
}
