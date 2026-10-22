import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api/client";
import { Analysis } from "./pages/Analysis";
import { Measured } from "./pages/Measured";
import { Generator } from "./pages/Generator";
import { Experiments } from "./pages/Experiments";
import { BatchAnalysis } from "./pages/BatchAnalysis";
import { SystemStatus } from "./pages/SystemStatus";
import { SourceNotice } from "./components/Feedback";
export function App() {
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: ({ signal }) => api.capabilities({ signal }),
  });
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
          <NavLink to="/measured">Измерения</NavLink>
          <NavLink to="/generator">Генератор</NavLink>
          <NavLink to="/experiments">Эксперименты</NavLink>
          <NavLink to="/batch">Пакетный анализ</NavLink>
          <NavLink to="/system">Состояние системы</NavLink>
        </nav>
        <span className="version">RESEARCH · v0.1</span>
      </header>
      <main>
        <SourceNotice
          source={
            capabilities.data
              ? String(capabilities.data.reference.source ?? "")
              : undefined
          }
        />
        <Routes>
          <Route path="/analysis" element={<Analysis />} />
          <Route path="/measured" element={<Measured />} />
          <Route path="/generator" element={<Generator />} />
          <Route path="/experiments" element={<Experiments />} />
          <Route path="/batch" element={<BatchAnalysis />} />
          <Route path="/system" element={<SystemStatus />} />
          <Route path="*" element={<Navigate to="/analysis" replace />} />
        </Routes>
        {capabilities.data && (
          <details className="methodology">
            <summary>Справочник и версии схем</summary>
            <pre>
              {JSON.stringify(
                {
                  reference: capabilities.data.reference,
                  schema_versions: capabilities.data.schema_versions,
                  range: capabilities.data.spectrum,
                },
                null,
                2,
              )}
            </pre>
          </details>
        )}
        <details className="methodology">
          <summary>Методика и ограничения</summary>
          <p>
            Задача multi-label для классов выбранного справочника. Несколько
            элементов могут присутствовать одновременно. NNLS сопоставляет
            спектр со справочными шаблонами; Logistic Regression и Random Forest
            обучаются на синтетических данных.
          </p>
          <p>
            Диапазон, единицы и система длин волн определяются профилем сервиса.
            В генераторе независимы аппаратное уширение, дискретизация и шум.
            Относительные интенсивности — условный prior. Score не равен
            вероятности; пороги обучаемых артефактов выбираются на validation.
          </p>
          <p>
            Справочные линии — диагностическое сопоставление, а не доказанное
            объяснение ML-решения. Пустое обнаружение не доказывает отсутствие
            элементов в образце или наличие неизвестного элемента. Определение
            концентраций и универсальное распознавание для произвольных приборов
            не реализованы; анализ измерений ограничен квалифицированными
            исследовательскими профилями с подтверждённой калибровкой.
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
