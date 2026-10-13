import { ApiError, validationMessages } from "../api/client";
export function ErrorNotice({
  error,
  id,
}: {
  error: Error | null | undefined;
  id?: string;
}) {
  return error ? (
    <div className="notice error" role="alert" id={id} tabIndex={-1}>
      {error.message}
      {error instanceof ApiError && <small>Код: {error.code}</small>}
      {validationMessages(error).map((item, i) => (
        <p key={i}>
          <strong>{item.field}:</strong> {item.message}
        </p>
      ))}
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
export function SourceNotice({ source }: { source?: string }) {
  return (
    <div className="notice">
      <strong>Исследовательский прототип</strong>
      <span>
        {source && (
          <>
            Справочник: <code>{source}</code>.{" "}
          </>
        )}
        Результаты на синтетике не подтверждают качество на реальных измерениях.
        Веса компонентов не являются концентрациями.
      </span>
    </div>
  );
}
