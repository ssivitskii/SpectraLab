import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { SystemStatus } from "../pages/SystemStatus";
import { systemStatus } from "./fixtures";

function wrap(element: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
          },
        })
      }
    >
      <MemoryRouter>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "systemStatus").mockResolvedValue(systemStatus);
});

describe("SystemStatus page scenarios", () => {
  it("renders worker in running state with task, queues, storage, and verified backup", async () => {
    wrap(<SystemStatus />);

    // Worker
    expect(await screen.findByText("Активен (running)")).toBeInTheDocument();
    expect(screen.getByText("worker-demo-1")).toBeInTheDocument();
    expect(screen.getByText("41234")).toBeInTheDocument();
    expect(screen.getByText("Генерация тестовой серии")).toBeInTheDocument();

    // Queues
    expect(screen.getByText("Активно: 2")).toBeInTheDocument();
    expect(screen.getByText("Очередь заданий (Jobs)")).toBeInTheDocument();
    expect(screen.getByText("Очередь пакетов (Batches)")).toBeInTheDocument();

    // Storage categories
    expect(screen.getByText("Спектры (артефакты)")).toBeInTheDocument();
    expect(screen.getByText("Измерения (приборные)")).toBeInTheDocument();
    expect(screen.getByText("Входные CSV пакетов")).toBeInTheDocument();

    // Null quota handling: does NOT turn null into 0!
    // e.g. batch_inputs quota_records is null -> "Не ограничено"
    const unlimitedEntries = screen.getAllByText("Не ограничено");
    expect(unlimitedEntries.length).toBeGreaterThanOrEqual(1);

    // Backup verification with path sanitization:
    // Original path: /Users/scryde/SpectraLab/artifacts/backups/backup-20261020T100000Z.tar.gz
    // Sanitized: backup-20261020T100000Z.tar.gz (NO absolute server paths)
    expect(
      screen.getAllByText("backup-20261020T100000Z.tar.gz").length,
    ).toBe(2);
    expect(
      screen.queryByText(
        "/Users/scryde/SpectraLab/artifacts/backups/backup-20261020T100000Z.tar.gz",
      ),
    ).not.toBeInTheDocument();
  });

  it("handles absent worker state correctly", async () => {
    vi.spyOn(api, "systemStatus").mockResolvedValue({
      ...systemStatus,
      worker: {
        state: "absent",
        status: "absent",
        worker_id: null,
        pid: null,
        updated_at: null,
        current_task: null,
        heartbeat_interval_seconds: 5.0,
        stale_threshold_seconds: 15.0,
        message: "Worker has not recorded any heartbeat",
      },
    });

    wrap(<SystemStatus />);
    expect(
      await screen.findByText("Не запущен (absent)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Worker has not recorded any heartbeat"),
    ).toBeInTheDocument();
    expect(screen.getByText("Не назначен")).toBeInTheDocument();
  });

  it("handles stale worker state with outdated heartbeat", async () => {
    vi.spyOn(api, "systemStatus").mockResolvedValue({
      ...systemStatus,
      worker: {
        state: "stale",
        status: "stale",
        worker_id: "worker-stale-1",
        pid: 9999,
        updated_at: "2026-10-20T10:00:00Z",
        current_task: null,
        heartbeat_interval_seconds: 5.0,
        stale_threshold_seconds: 15.0,
        message: "Worker heartbeat is outdated",
      },
    });

    wrap(<SystemStatus />);
    expect(
      await screen.findByText("Устаревший heartbeat (stale)"),
    ).toBeInTheDocument();
    expect(screen.getByText("worker-stale-1")).toBeInTheDocument();
  });

  it("handles cleanly stopped worker state", async () => {
    vi.spyOn(api, "systemStatus").mockResolvedValue({
      ...systemStatus,
      worker: {
        state: "stopped",
        status: "stopped",
        worker_id: "worker-stopped",
        pid: 1234,
        updated_at: "2026-10-20T11:00:00Z",
        current_task: null,
        heartbeat_interval_seconds: 5.0,
        stale_threshold_seconds: 15.0,
        message: "Worker was stopped cleanly",
      },
    });

    wrap(<SystemStatus />);
    expect(
      await screen.findByText("Остановлен (stopped)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Worker was stopped cleanly"),
    ).toBeInTheDocument();
  });

  it("handles missing backup verification data without pretending all is well", async () => {
    vi.spyOn(api, "systemStatus").mockResolvedValue({
      ...systemStatus,
      backup: {
        last_archive_verified: null,
        last_restore_verified: null,
      },
    });

    wrap(<SystemStatus />);
    expect(await screen.findByText("Нет данных")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Данные о верифицированных архивных копиях отсутствуют/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Данные о верифицированных тестовых восстановлениях отсутствуют/i,
      ),
    ).toBeInTheDocument();
  });

  it("triggers manual refresh when clicking refresh button", async () => {
    const fetchSpy = vi.spyOn(api, "systemStatus").mockResolvedValue(systemStatus);
    wrap(<SystemStatus />);

    await screen.findByText("Активен (running)");

    const refreshBtn = screen.getByRole("button", {
      name: "Обновить состояние системы",
    });
    await userEvent.click(refreshBtn);

    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("displays error notice when status query fails", async () => {
    vi.spyOn(api, "systemStatus").mockRejectedValue(
      new Error("Не удалось подключиться к серверу"),
    );

    wrap(<SystemStatus />);
    expect(
      await screen.findByText("Не удалось подключиться к серверу"),
    ).toBeInTheDocument();
  });
});
