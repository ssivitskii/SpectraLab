import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  type BackupStatus,
  type QueueStatus,
  type StorageCategory,
  type StorageStatus,
  type SystemStatusResponse,
  type WorkerStatus,
} from "../api/client";
import { ErrorNotice, Loading } from "../components/Feedback";

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes === 0) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const safeI = Math.min(i, units.length - 1);
  const val = bytes / Math.pow(1024, safeI);
  return `${val.toFixed(val >= 100 || safeI === 0 ? 0 : 1)} ${units[safeI]}`;
}

function formatDate(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return d.toLocaleString("ru");
  } catch {
    return isoString;
  }
}

function formatTime(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return d.toLocaleTimeString("ru");
  } catch {
    return isoString;
  }
}

/**
 * Strips absolute filesystem paths from archive names and messages
 * so no server-internal paths are leaked to the browser.
 */
function sanitizePath(str: string | null | undefined): string {
  if (!str) return "—";
  return str.replace(/(?:\/[a-zA-Z0-9._-]+)+\/([a-zA-Z0-9._-]+\.[a-zA-Z0-9._-]+)/g, "$1");
}

const CATEGORY_NAMES: Record<string, string> = {
  spectra: "Спектры (артефакты)",
  predictions: "Предикшены (синтетика)",
  measured_predictions: "Измерения (приборные)",
  jobs: "Задания (генерация)",
  batches: "Пакеты (метаданные)",
  batch_inputs: "Входные CSV пакетов",
  batch_cache: "Кэш вычислений пакетов",
  experiments: "Эксперименты (отчёты)",
};

export function SystemStatus() {
  const [lastRefreshedAt, setLastRefreshedAt] = useState<string>("");

  const statusQuery = useQuery({
    queryKey: ["systemStatus"],
    queryFn: async ({ signal }) => {
      const result = await api.systemStatus({ signal });
      setLastRefreshedAt(new Date().toISOString());
      return result;
    },
    refetchInterval: () =>
      typeof document !== "undefined" && document.visibilityState === "visible"
        ? 10_000
        : false,
  });

  // Re-fetch immediately when user returns to tab
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void statusQuery.refetch();
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [statusQuery]);

  const data: SystemStatusResponse | undefined = statusQuery.data;

  return (
    <>
      <header className="page-title">
        <div>
          <h1>Состояние системы</h1>
          <p>
            Диагностика рабочего окружения: статус воркера, очереди заданий,
            дисковое пространство и верификация резервных копий.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          {lastRefreshedAt && (
            <span style={{ fontSize: "12px", color: "#64748b" }}>
              Обновлено: {formatTime(lastRefreshedAt)}
            </span>
          )}
          <button
            type="button"
            className="button btn-sm primary"
            onClick={() => void statusQuery.refetch()}
            disabled={statusQuery.isFetching}
            id="refresh-status-button"
            aria-label="Обновить состояние системы"
          >
            {statusQuery.isFetching ? "Обновление…" : "⟳ Обновить"}
          </button>
        </div>
      </header>

      {/* Query Error Notice */}
      {statusQuery.error && (
        <ErrorNotice error={statusQuery.error} id="system-status-error" />
      )}

      {/* Initial Loading */}
      {statusQuery.isLoading && !data && (
        <Loading children="Загрузка диагностических параметров системы…" />
      )}

      {data && (
        <div className="status-grid">
          {/* CARD 1: Worker Status */}
          <WorkerCard worker={data.worker} />

          {/* CARD 2: Queues Status */}
          <QueuesCard queues={data.queues} />

          {/* CARD 3: Storage & Disk Status */}
          <StorageCard storage={data.storage} />

          {/* CARD 4: Backup Status */}
          <BackupCard backup={data.backup} />
        </div>
      )}
    </>
  );
}

function WorkerCard({ worker }: { worker: WorkerStatus }) {
  let badgeClass = "badge-neutral";
  let statusText = "Неизвестно";

  switch (worker.state) {
    case "running":
      badgeClass = "badge-success";
      statusText = "Активен (running)";
      break;
    case "stopped":
      badgeClass = "badge-neutral";
      statusText = "Остановлен (stopped)";
      break;
    case "stale":
      badgeClass = "badge-warning";
      statusText = "Устаревший heartbeat (stale)";
      break;
    case "absent":
      badgeClass = "badge-danger";
      statusText = "Не запущен (absent)";
      break;
  }

  return (
    <article className="status-card" aria-label="Состояние воркера">
      <div className="card-header">
        <h2>Фоновый воркер</h2>
        <span className={`badge ${badgeClass}`}>{statusText}</span>
      </div>

      <p style={{ margin: 0, fontSize: "13px", color: "#334155" }}>
        {worker.message || "Нет дополнительного диагностического сообщения."}
      </p>

      <div style={{ marginTop: "4px" }}>
        <div className="metric-row">
          <span className="metric-label">ID воркера:</span>
          <span className="metric-value">
            {worker.worker_id ? <code>{worker.worker_id}</code> : "Не назначен"}
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">PID процесса:</span>
          <span className="metric-value">
            {worker.pid !== null && worker.pid !== undefined ? worker.pid : "—"}
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Последний heartbeat:</span>
          <span className="metric-value">{formatDate(worker.updated_at)}</span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Интервал heartbeat:</span>
          <span className="metric-value">
            {worker.heartbeat_interval_seconds} сек
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Порог устаревания:</span>
          <span className="metric-value">
            {worker.stale_threshold_seconds} сек
          </span>
        </div>
      </div>

      <div style={{ borderTop: "1px solid #edf2f7", paddingTop: "12px" }}>
        <small style={{ fontWeight: 600, color: "#475569" }}>
          Текущая задача:
        </small>
        {worker.current_task ? (
          <div
            style={{
              background: "#f8fafc",
              border: "1px solid #e2e8f0",
              borderRadius: "6px",
              padding: "10px 12px",
              marginTop: "6px",
              fontSize: "12px",
            }}
          >
            <div>
              <strong>
                {worker.current_task.kind === "job" ? "Задание (Job)" : "Пакет (Batch)"}
              </strong>
              : <code>{worker.current_task.task_id}</code>
            </div>
            {worker.current_task.description && (
              <p style={{ margin: "4px 0 0", color: "#64748b" }}>
                {worker.current_task.description}
              </p>
            )}
            {worker.current_task.started_at && (
              <small style={{ color: "#94a3b8", display: "block", marginTop: "4px" }}>
                Запущено: {formatDate(worker.current_task.started_at)}
              </small>
            )}
          </div>
        ) : (
          <p style={{ margin: "6px 0 0", fontSize: "12px", color: "#64748b" }}>
            Нет активной задачи (воркер в режиме ожидания)
          </p>
        )}
      </div>
    </article>
  );
}

function QueuesCard({ queues }: { queues: QueueStatus }) {
  return (
    <article className="status-card" aria-label="Состояние очередей">
      <div className="card-header">
        <h2>Очереди задач</h2>
        <span
          className={`badge ${queues.active_total > 0 ? "badge-info" : "badge-neutral"}`}
        >
          Активно: {queues.active_total}
        </span>
      </div>

      {/* Jobs queue */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong style={{ fontSize: "14px", color: "#1e293b" }}>
            Очередь заданий (Jobs)
          </strong>
          <span style={{ fontSize: "11px", color: "#64748b" }}>
            Доступно слотов: {queues.jobs.remaining_pending} из {queues.jobs.quota_pending}
          </span>
        </div>
        <div className="metric-box-grid">
          <div className="metric-box">
            <small>В очереди</small>
            <strong style={{ color: queues.jobs.queued > 0 ? "#1d4ed8" : "#334155" }}>
              {queues.jobs.queued}
            </strong>
          </div>
          <div className="metric-box">
            <small>Выполняются</small>
            <strong style={{ color: queues.jobs.running > 0 ? "#166244" : "#334155" }}>
              {queues.jobs.running}
            </strong>
          </div>
          <div className="metric-box">
            <small>Завершены</small>
            <strong>{queues.jobs.completed}</strong>
          </div>
          <div className="metric-box">
            <small>Ошибки</small>
            <strong style={{ color: queues.jobs.failed > 0 ? "#b91c1c" : "#334155" }}>
              {queues.jobs.failed}
            </strong>
          </div>
          <div className="metric-box">
            <small>Отменены</small>
            <strong>{queues.jobs.cancelled}</strong>
          </div>
          <div className="metric-box">
            <small>Всего записей</small>
            <strong>{queues.jobs.total}</strong>
          </div>
        </div>
      </div>

      <hr style={{ margin: "6px 0" }} />

      {/* Batches queue */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong style={{ fontSize: "14px", color: "#1e293b" }}>
            Очередь пакетов (Batches)
          </strong>
          <span style={{ fontSize: "11px", color: "#64748b" }}>
            Всего пакетов: {queues.batches.total}
          </span>
        </div>
        <div className="metric-box-grid">
          <div className="metric-box">
            <small>Черновики</small>
            <strong>{queues.batches.draft}</strong>
          </div>
          <div className="metric-box">
            <small>В очереди</small>
            <strong style={{ color: queues.batches.queued > 0 ? "#1d4ed8" : "#334155" }}>
              {queues.batches.queued}
            </strong>
          </div>
          <div className="metric-box">
            <small>Выполняются</small>
            <strong style={{ color: queues.batches.running > 0 ? "#166244" : "#334155" }}>
              {queues.batches.running}
            </strong>
          </div>
          <div className="metric-box">
            <small>Завершены</small>
            <strong>{queues.batches.completed}</strong>
          </div>
          <div className="metric-box">
            <small>Ошибки</small>
            <strong style={{ color: queues.batches.failed > 0 ? "#b91c1c" : "#334155" }}>
              {queues.batches.failed}
            </strong>
          </div>
          <div className="metric-box">
            <small>Частично</small>
            <strong>{queues.batches.partial}</strong>
          </div>
        </div>
      </div>
    </article>
  );
}

function StorageCard({ storage }: { storage: StorageStatus }) {
  const diskTotal = storage.disk.total_bytes;
  const diskUsed = storage.disk.used_bytes;
  const diskFree = storage.disk.free_bytes;
  const usedPercent = diskTotal > 0 ? (diskUsed / diskTotal) * 100 : 0;

  let progressClass = "";
  if (usedPercent > 90) progressClass = "danger";
  else if (usedPercent > 75) progressClass = "warn";

  return (
    <article
      className="status-card"
      style={{ gridColumn: "1 / -1" }}
      aria-label="Использование хранилища и квоты"
    >
      <div className="card-header">
        <h2>Хранилище и дисковое пространство</h2>
        <span className="badge badge-info">
          Артефакты: {formatBytes(storage.total_used_bytes)}
        </span>
      </div>

      {/* Disk overview */}
      <div className="progress-bar-container">
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "12px" }}>
          <span>
            <strong>Дисковый раздел:</strong> Занято {formatBytes(diskUsed)} из{" "}
            {formatBytes(diskTotal)} ({usedPercent.toFixed(1)}%)
          </span>
          <span style={{ color: "#166244", fontWeight: 600 }}>
            Свободно: {formatBytes(diskFree)}
          </span>
        </div>
        <div className="progress-track" role="progressbar" aria-valuenow={Math.round(usedPercent)} aria-valuemin={0} aria-valuemax={100} aria-label="Использование дискового раздела">
          <div
            className={`progress-fill ${progressClass}`}
            style={{ width: `${Math.min(100, Math.max(0, usedPercent))}%` }}
          />
        </div>
        {storage.quota_headroom_bytes !== null && storage.quota_headroom_bytes !== undefined && (
          <small style={{ color: "#64748b", display: "block", marginTop: "6px" }}>
            Доступный запас по квотам хранилища: {formatBytes(storage.quota_headroom_bytes)}
          </small>
        )}
      </div>

      {/* Categories Table */}
      <div style={{ marginTop: "10px" }}>
        <h3 style={{ fontSize: "14px", marginBottom: "8px" }}>
          Категории данных и лимиты
        </h3>
        <div className="table-scroll" tabIndex={0} aria-label="Таблица категорий хранилища">
          <table className="table-compact">
            <thead>
              <tr>
                <th scope="col">Категория</th>
                <th scope="col">Записей</th>
                <th scope="col">Занято</th>
                <th scope="col">Квота (записи)</th>
                <th scope="col">Квота (размер)</th>
                <th scope="col">Остаток квоты</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(storage.categories).map(([catKey, cat]: [string, StorageCategory]) => {
                const displayName = CATEGORY_NAMES[catKey] ?? catKey;
                const quotaRecordsText =
                  cat.quota_records !== null && cat.quota_records !== undefined
                    ? cat.quota_records.toLocaleString("ru")
                    : "Не ограничено";
                const quotaBytesText =
                  cat.quota_bytes !== null && cat.quota_bytes !== undefined
                    ? formatBytes(cat.quota_bytes)
                    : "Не ограничено";

                let remainingText = "—";
                if (cat.remaining_records !== null && cat.remaining_records !== undefined) {
                  remainingText = `${cat.remaining_records.toLocaleString("ru")} зап.`;
                }
                if (cat.remaining_bytes !== null && cat.remaining_bytes !== undefined) {
                  const bytesStr = formatBytes(cat.remaining_bytes);
                  remainingText =
                    remainingText !== "—" ? `${remainingText} / ${bytesStr}` : bytesStr;
                }

                return (
                  <tr key={catKey}>
                    <th scope="row">
                      <strong>{displayName}</strong>
                    </th>
                    <td className="mono">{cat.record_count.toLocaleString("ru")}</td>
                    <td className="mono">{formatBytes(cat.size_bytes)}</td>
                    <td>{quotaRecordsText}</td>
                    <td>{quotaBytesText}</td>
                    <td>{remainingText}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </article>
  );
}

function BackupCard({ backup }: { backup: BackupStatus }) {
  const archive = backup.last_archive_verified;
  const restore = backup.last_restore_verified;

  return (
    <article
      className="status-card"
      style={{ gridColumn: "1 / -1" }}
      aria-label="Верификация резервных копий"
    >
      <div className="card-header">
        <h2>Верификация резервных копий</h2>
        <span
          className={`badge ${archive?.verification_passed ? "badge-success" : archive ? "badge-danger" : "badge-neutral"}`}
        >
          {archive?.verification_passed
            ? "Архивация проверена"
            : archive
              ? "Ошибка проверки архива"
              : "Нет данных"}
        </span>
      </div>

      <p className="muted" style={{ margin: 0 }}>
        Сведения о последних плановых проверках целостности архивов и тестовых
        восстановлений данных.
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: "16px",
          marginTop: "10px",
        }}
      >
        {/* Archive Verification Box */}
        <div
          style={{
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            padding: "16px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ fontSize: "14px" }}>Последняя архивация</h3>
            {archive ? (
              <span
                className={`badge ${archive.verification_passed ? "badge-success" : "badge-danger"}`}
              >
                {archive.verification_passed ? "✓ Пройдена" : "✕ Сбой"}
              </span>
            ) : (
              <span className="badge badge-neutral">Нет проверок</span>
            )}
          </div>

          {archive ? (
            <div style={{ marginTop: "12px" }}>
              <div className="metric-row">
                <span className="metric-label">Время завершения:</span>
                <span className="metric-value">{formatDate(archive.completed_at)}</span>
              </div>
              <div className="metric-row">
                <span className="metric-label">Архив:</span>
                <span className="metric-value">
                  <code>{sanitizePath(archive.archive_name)}</code>
                </span>
              </div>
              <div className="metric-row">
                <span className="metric-label">Размер архива:</span>
                <span className="metric-value">{formatBytes(archive.archive_size_bytes)}</span>
              </div>
              <div className="metric-row">
                <span className="metric-label">Проверено записей:</span>
                <span className="metric-value">{archive.records_count.toLocaleString("ru")}</span>
              </div>
              <div className="metric-row">
                <span className="metric-label">SHA-256 архива:</span>
                <span className="metric-value">
                  <code>{archive.archive_sha256 ? `${archive.archive_sha256.slice(0, 16)}…` : "—"}</code>
                </span>
              </div>
              {archive.details && (
                <div style={{ marginTop: "8px", fontSize: "12px", color: "#64748b" }}>
                  <strong>Детали:</strong> {sanitizePath(archive.details)}
                </div>
              )}
            </div>
          ) : (
            <p style={{ fontSize: "12px", color: "#64748b", marginTop: "12px" }}>
              Данные о верифицированных архивных копиях отсутствуют. Проверьте запуск
              периодических процедур обслуживания.
            </p>
          )}
        </div>

        {/* Restore Verification Box */}
        <div
          style={{
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            padding: "16px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ fontSize: "14px" }}>Тестовое восстановление</h3>
            {restore ? (
              <span
                className={`badge ${restore.verification_passed ? "badge-success" : "badge-danger"}`}
              >
                {restore.verification_passed ? "✓ Подтверждено" : "✕ Сбой"}
              </span>
            ) : (
              <span className="badge badge-neutral">Нет проверок</span>
            )}
          </div>

          {restore ? (
            <div style={{ marginTop: "12px" }}>
              <div className="metric-row">
                <span className="metric-label">Время завершения:</span>
                <span className="metric-value">{formatDate(restore.completed_at)}</span>
              </div>
              <div className="metric-row">
                <span className="metric-label">Архив:</span>
                <span className="metric-value">
                  <code>{sanitizePath(restore.archive_name)}</code>
                </span>
              </div>
              <div className="metric-row">
                <span className="metric-label">Восстановлено записей:</span>
                <span className="metric-value">{restore.restored_records_count.toLocaleString("ru")}</span>
              </div>
              <div className="metric-row">
                <span className="metric-label">SHA-256 архива:</span>
                <span className="metric-value">
                  <code>{restore.archive_sha256 ? `${restore.archive_sha256.slice(0, 16)}…` : "—"}</code>
                </span>
              </div>
              {restore.details && (
                <div style={{ marginTop: "8px", fontSize: "12px", color: "#64748b" }}>
                  <strong>Детали:</strong> {sanitizePath(restore.details)}
                </div>
              )}
            </div>
          ) : (
            <p style={{ fontSize: "12px", color: "#64748b", marginTop: "12px" }}>
              Данные о верифицированных тестовых восстановлениях отсутствуют.
            </p>
          )}
        </div>
      </div>
    </article>
  );
}
