import { ApiError, validationMessages } from "../api/client";
import { Icon } from "./Icon";
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
    <div className="loading-state" role="status">
      <span className="loading-spinner" aria-hidden="true" />
      {children}
    </div>
  );
}
export function SourceNotice({ source }: { source?: string }) {
  return (
    <div className="research-notice">
      <Icon name="info" size={18} />
      <div>
      <strong>Исследовательский прототип</strong>
      <p>
        {source && (
          <>
            Справочник: <code>{source}</code>.{" "}
          </>
        )}
        Результаты на синтетике не подтверждают качество на реальных измерениях.
        Веса компонентов не являются концентрациями.
      </p>
      </div>
    </div>
  );
}
