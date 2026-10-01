export function ErrorNotice({ error }: { error: Error | null }) {
  return error ? (
    <div className="notice error" role="alert">
      {error.message}
    </div>
  ) : null;
}
export function Loading({ children = "Загрузка…" }: { children?: string }) {
  return (
    <div className="empty" role="status">
      <span className="loading-dot" />
      {children}
    </div>
  );
}
export function SourceNotice({ source = "demo_fixture" }: { source?: string }) {
  return (
    <div className="notice">
      <strong>Исследовательский прототип</strong>
      <span>
        Справочник: <code>{source}</code>. Результаты на синтетике не
        подтверждают качество на реальных измерениях. Веса компонентов не
        являются концентрациями.
      </span>
    </div>
  );
}
