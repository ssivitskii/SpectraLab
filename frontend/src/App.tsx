import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Analysis } from "./pages/Analysis";
import { Generator } from "./pages/Generator";
import { Experiments } from "./pages/Experiments";
import { SourceNotice } from "./components/Feedback";
export function App() {
  return (
    <>
      <header className="app-header">
        <a className="brand" href="/analysis">
          <span className="brand-mark">∿</span>
          <span>
            Spectra<strong>Lab</strong>
            <small>ЛАБОРАТОРИЯ ЭМИССИОННЫХ СПЕКТРОВ</small>
          </span>
        </a>
        <nav aria-label="Основная навигация">
          <NavLink to="/analysis">Анализ спектра</NavLink>
          <NavLink to="/generator">Генератор</NavLink>
          <NavLink to="/experiments">Эксперименты</NavLink>
        </nav>
        <span className="version">RESEARCH · v0.1</span>
      </header>
      <main>
        <SourceNotice />
        <Routes>
          <Route path="/analysis" element={<Analysis />} />
          <Route path="/generator" element={<Generator />} />
          <Route path="/experiments" element={<Experiments />} />
          <Route path="*" element={<Navigate to="/analysis" replace />} />
        </Routes>
        <details className="methodology">
          <summary>Методика и ограничения</summary>
          <p>
            Шесть классов нейтральных атомов; задача multi-label. Несколько
            элементов могут присутствовать одновременно. NNLS сопоставляет
            спектр со справочными шаблонами; Logistic Regression и Random Forest
            обучаются на синтетических данных.
          </p>
          <p>
            Вакуумные длины волн в nm. В генераторе независимы аппаратное
            уширение, дискретизация и аддитивный гауссов шум. Относительные
            интенсивности — условный prior. Score не равен вероятности; пороги
            обучаемых артефактов выбираются на validation.
          </p>
          <p>
            Справочные линии — диагностическое сопоставление, а не доказанное
            объяснение ML-решения. Пустое обнаружение не доказывает отсутствие
            элементов в образце или наличие неизвестного элемента. Определение
            концентраций и перенос на реальные измерения не реализованы.
          </p>
        </details>
      </main>
      <footer>
        SpectraLab{" "}
        <span>Синтетические данные · открытый исследовательский процесс</span>
        <a href="/docs" target="_blank" rel="noreferrer">
          OpenAPI ↗
        </a>
      </footer>
    </>
  );
}
