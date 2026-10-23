import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
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
import { Icon } from "../components/Icon";

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes === 0) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, index);
  return `${value.toFixed(value >= 100 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatDate(value: string | null | undefined, timeOnly = false): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return timeOnly ? date.toLocaleTimeString("ru") : date.toLocaleString("ru");
}

function sanitizePath(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(/(?:\/[a-zA-Z0-9._-]+)+\/([a-zA-Z0-9._-]+\.[a-zA-Z0-9._-]+)/g, "$1");
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
  const [lastRefreshedAt, setLastRefreshedAt] = useState("");
  const statusQuery = useQuery({
    queryKey: ["systemStatus"],
    queryFn: async ({ signal }) => {
      const result = await api.systemStatus({ signal });
      setLastRefreshedAt(new Date().toISOString());
      return result;
    },
    refetchInterval: () => document.visibilityState === "visible" ? 10_000 : false,
  });
  const { refetch } = statusQuery;
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void refetch();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [refetch]);
  const data: SystemStatusResponse | undefined = statusQuery.data;

  return (
    <>
      <header className="page-title">
        <div>
          <h1>Состояние системы</h1>
          <p>Фоновая обработка, очереди и сохранность данных на этом компьютере.</p>
        </div>
        <div className="status-refresh">
          <button type="button" onClick={() => void statusQuery.refetch()} disabled={statusQuery.isFetching} id="refresh-status-button" aria-label="Обновить состояние системы">
            <Icon name="refresh" size={17} />{statusQuery.isFetching ? "Обновление…" : "Обновить"}
          </button>
          <small aria-live="polite">{lastRefreshedAt ? `Обновлено: ${formatDate(lastRefreshedAt, true)}` : "Ожидание данных"} · каждые 10 с</small>
        </div>
      </header>
      {statusQuery.error && <ErrorNotice error={statusQuery.error} id="system-status-error" />}
      {statusQuery.isLoading && !data && <Loading>Загрузка диагностических параметров системы…</Loading>}
      {data && (
        <div className="status-grid">
          <WorkerCard worker={data.worker} />
          <QueuesCard queues={data.queues} />
          <StorageCard storage={data.storage} />
          <BackupCard backup={data.backup} />
        </div>
      )}
    </>
  );
}

function WorkerCard({ worker }: { worker: WorkerStatus }) {
  const states = {
    running: ["badge-success", "Активен (running)"],
    stopped: ["badge-neutral", "Остановлен (stopped)"],
    stale: ["badge-warning", "Устаревший heartbeat (stale)"],
    absent: ["badge-danger", "Не запущен (absent)"],
  };
  const [badgeClass, statusText] = states[worker.state] ?? ["badge-neutral", "Неизвестно"];
  return (
    <article className="panel status-panel" aria-label="Состояние воркера">
      <div className="panel-heading status-heading"><h2><Icon name="activity" />Фоновый воркер</h2><span className={`badge ${badgeClass}`}>{statusText}</span></div>
      <p className="status-description">{worker.message || "Нет дополнительного диагностического сообщения."}</p>
      <dl className="status-facts">
        <div><dt>ID воркера</dt><dd>{worker.worker_id ? <code>{worker.worker_id}</code> : "Не назначен"}</dd></div>
        <div><dt>PID процесса</dt><dd>{worker.pid ?? "—"}</dd></div>
        <div><dt>Последний heartbeat</dt><dd>{formatDate(worker.updated_at)}</dd></div>
        <div><dt>Интервал heartbeat</dt><dd>{worker.heartbeat_interval_seconds} сек</dd></div>
        <div><dt>Порог устаревания</dt><dd>{worker.stale_threshold_seconds} сек</dd></div>
      </dl>
      <div className="status-task">
        <h3>Текущая задача</h3>
        {worker.current_task ? <>
          <p><strong>{worker.current_task.kind === "job" ? "Задание (Job)" : "Пакет (Batch)"}</strong> · <code>{worker.current_task.task_id}</code></p>
          {worker.current_task.description && <p className="muted">{worker.current_task.description}</p>}
          {worker.current_task.started_at && <small className="muted">Запущено: {formatDate(worker.current_task.started_at)}</small>}
        </> : <p className="muted">{worker.state === "running" ? "Нет активной задачи (воркер в режиме ожидания)" : "Активная задача не зарегистрирована"}</p>}
      </div>
    </article>
  );
}

function QueuesCard({ queues }: { queues: QueueStatus }) {
  const rows = [
    ["В очереди", queues.jobs.queued, queues.batches.queued, "info"],
    ["Выполняются", queues.jobs.running, queues.batches.running, "success"],
    ["Завершены", queues.jobs.completed, queues.batches.completed, ""],
    ["Ошибки", queues.jobs.failed, queues.batches.failed, "danger"],
    ["Отменены", queues.jobs.cancelled, queues.batches.cancelled, ""],
    ["Частично", null, queues.batches.partial, ""],
    ["Черновики", null, queues.batches.draft, ""],
  ] as const;
  return (
    <article className="panel status-panel" aria-label="Состояние очередей">
      <div className="panel-heading status-heading"><h2><Icon name="layers" />Очереди задач</h2><span className={`badge ${queues.active_total > 0 ? "badge-info" : "badge-neutral"}`}>Активно: {queues.active_total}</span></div>
      <div className="table-scroll" tabIndex={0} aria-label="Счётчики очередей">
        <table className="queue-status-table">
          <thead><tr><th scope="col">Состояние</th><th scope="col"><span className="sr-only">Очередь заданий (Jobs)</span><span aria-hidden="true">Задания</span></th><th scope="col"><span className="sr-only">Очередь пакетов (Batches)</span><span aria-hidden="true">Пакеты</span></th></tr></thead>
          <tbody>{rows.map(([label, jobs, batches, tone]) => <tr key={label}><th scope="row">{label}</th><td className={jobs && tone ? `status-number status-${tone}` : "status-number"}>{jobs ?? "—"}</td><td className={batches && tone ? `status-number status-${tone}` : "status-number"}>{batches ?? "—"}</td></tr>)}</tbody>
          <tfoot><tr><th scope="row">Всего записей</th><td>{queues.jobs.total}</td><td>{queues.batches.total}</td></tr></tfoot>
        </table>
      </div>
      <p className="muted">Доступно слотов заданий: {queues.jobs.remaining_pending} из {queues.jobs.quota_pending}. Пакетов: {queues.batches.total}.</p>
      <Link className="status-detail-link" to="/batch">Открыть пакетный анализ<Icon name="arrowRight" size={16} /></Link>
    </article>
  );
}

function StorageCard({ storage }: { storage: StorageStatus }) {
  const { total_bytes: diskTotal, used_bytes: diskUsed, free_bytes: diskFree } = storage.disk;
  const usedPercent = diskTotal > 0 ? Math.max(0, Math.min(100, diskUsed / diskTotal * 100)) : 0;
  const progressClass = usedPercent > 90 ? "danger" : usedPercent > 75 ? "warn" : "";
  return (
    <article className="panel status-panel status-wide" aria-label="Использование хранилища и квоты">
      <div className="panel-heading status-heading"><h2><Icon name="folder" />Хранилище и дисковое пространство</h2><span className="badge badge-info">Артефакты: {formatBytes(storage.total_used_bytes)}</span></div>
      <div className="disk-summary"><span>Занято <strong>{formatBytes(diskUsed)}</strong> из {formatBytes(diskTotal)} · {usedPercent.toFixed(1)}%</span><span className="status-success">Свободно: {formatBytes(diskFree)}</span></div>
      <div className="progress-track" role="progressbar" aria-valuenow={Math.round(usedPercent)} aria-valuemin={0} aria-valuemax={100} aria-label="Использование дискового раздела"><div className={`progress-fill ${progressClass}`} style={{ width: `${usedPercent}%` }} /></div>
      {storage.quota_headroom_bytes != null && <small className="muted">Доступный запас по квотам хранилища: {formatBytes(storage.quota_headroom_bytes)}</small>}
      <h3>Категории данных и лимиты</h3>
      <div className="table-scroll" tabIndex={0} aria-label="Таблица категорий хранилища">
        <table className="table-compact storage-table"><thead><tr><th scope="col">Категория</th><th scope="col">Записей</th><th scope="col">Занято</th><th scope="col">Квота записей</th><th scope="col">Квота размера</th><th scope="col">Остаток квоты</th></tr></thead>
          <tbody>{Object.entries(storage.categories).map(([key, category]: [string, StorageCategory]) => {
            const remaining = [category.remaining_records != null ? `${category.remaining_records.toLocaleString("ru")} зап.` : null, category.remaining_bytes != null ? formatBytes(category.remaining_bytes) : null].filter(Boolean).join(" / ") || "—";
            return <tr key={key}><th scope="row">{CATEGORY_NAMES[key] ?? key}</th><td>{category.record_count.toLocaleString("ru")}</td><td>{formatBytes(category.size_bytes)}</td><td>{category.quota_records != null ? category.quota_records.toLocaleString("ru") : "Не ограничено"}</td><td>{category.quota_bytes != null ? formatBytes(category.quota_bytes) : "Не ограничено"}</td><td>{remaining}</td></tr>;
          })}</tbody>
        </table>
      </div>
    </article>
  );
}

function BackupCard({ backup }: { backup: BackupStatus }) {
  const archive = backup.last_archive_verified;
  const restore = backup.last_restore_verified;
  return (
    <article className="panel status-panel status-wide" aria-label="Верификация резервных копий">
      <div className="panel-heading status-heading"><h2><Icon name="check" />Верификация резервных копий</h2><span className={`badge ${archive?.verification_passed ? "badge-success" : archive ? "badge-danger" : "badge-neutral"}`}>{archive?.verification_passed ? "Архивация проверена" : archive ? "Ошибка проверки архива" : "Нет данных"}</span></div>
      <p className="status-description">Последние завершённые проверки целостности архивов и тестового восстановления. Отсутствие записи не подтверждает сохранность данных.</p>
      <div className="backup-columns">
        <section><div className="section-heading"><h3>Последняя архивация</h3><span className={`badge ${archive ? archive.verification_passed ? "badge-success" : "badge-danger" : "badge-neutral"}`}>{archive ? archive.verification_passed ? "Пройдена" : "Сбой" : "Нет проверок"}</span></div>
          {archive ? <><dl className="status-facts"><div><dt>Время завершения</dt><dd>{formatDate(archive.completed_at)}</dd></div><div><dt>Архив</dt><dd><code>{sanitizePath(archive.archive_name)}</code></dd></div><div><dt>Размер архива</dt><dd>{formatBytes(archive.archive_size_bytes)}</dd></div><div><dt>Проверено записей</dt><dd>{archive.records_count.toLocaleString("ru")}</dd></div><div><dt>SHA-256 архива</dt><dd><code title={archive.archive_sha256 ?? undefined}>{archive.archive_sha256 ? `${archive.archive_sha256.slice(0, 16)}…` : "—"}</code></dd></div></dl>{archive.details && <p className="muted">{sanitizePath(archive.details)}</p>}</> : <p className="muted">Данные о верифицированных архивных копиях отсутствуют. Проверьте запуск периодических процедур обслуживания.</p>}
        </section>
        <section><div className="section-heading"><h3>Тестовое восстановление</h3><span className={`badge ${restore ? restore.verification_passed ? "badge-success" : "badge-danger" : "badge-neutral"}`}>{restore ? restore.verification_passed ? "Подтверждено" : "Сбой" : "Нет проверок"}</span></div>
          {restore ? <><dl className="status-facts"><div><dt>Время завершения</dt><dd>{formatDate(restore.completed_at)}</dd></div><div><dt>Архив</dt><dd><code>{sanitizePath(restore.archive_name)}</code></dd></div><div><dt>Восстановлено записей</dt><dd>{restore.restored_records_count.toLocaleString("ru")}</dd></div><div><dt>SHA-256 архива</dt><dd><code title={restore.archive_sha256 ?? undefined}>{restore.archive_sha256 ? `${restore.archive_sha256.slice(0, 16)}…` : "—"}</code></dd></div></dl>{restore.details && <p className="muted">{sanitizePath(restore.details)}</p>}</> : <p className="muted">Данные о верифицированных тестовых восстановлениях отсутствуют.</p>}
        </section>
      </div>
    </article>
  );
}
