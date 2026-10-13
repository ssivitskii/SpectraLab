import { expect, test, type APIRequestContext } from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import type { Batch, ModelInfo } from "../src/api/client";

async function generatedCsv(request: APIRequestContext) {
  const capabilities = await (await request.get("/api/v1/capabilities")).json();
  const generated = await request.post("/api/v1/spectra/generate", {
    data: {
      elements: [capabilities.elements[0]],
      component_weights: [1],
      seed: 17,
    },
  });
  expect(generated.ok()).toBe(true);
  const spectrum = await generated.json();
  return (
    "wavelength_nm,intensity\n" +
    spectrum.wavelength_nm
      .map((x: number, i: number) => `${x},${spectrum.intensity[i]}`)
      .join("\n")
  );
}
const descriptor = (relative_path: string, csv: string) => ({
  relative_path,
  size_bytes: Buffer.byteLength(csv),
  sha256: createHash("sha256").update(csv).digest("hex"),
});

test("folder queue preserves bad files, independent model results, reload, retry and provenance export", async ({
  page,
  request,
}, info) => {
  const directory = await mkdtemp(join(tmpdir(), "spectralab-folder-"));
  try {
    const csv = await generatedCsv(request);
    await mkdir(join(directory, "nested"));
    await writeFile(join(directory, "nested", "sample.csv"), csv);
    await writeFile(join(directory, "bad.csv"), "wave,value\n350,NaN\n800,1");
    await writeFile(join(directory, "README.txt"), "Measurement notes");
    const models: ModelInfo[] = await (
      await request.get("/api/v1/models")
    ).json();
    const trained = models.find(
      (model) => model.ready && model.model_id !== "builtin-nnls",
    )!;
    await page.goto("/batch");
    await page.getByLabel("Название очереди").fill("E2E folder");
    await page
      .getByRole("checkbox", {
        name: `${trained.model_id} · v${trained.version}`,
        exact: true,
      })
      .check();
    await expect(page.getByLabel("Выбрать папку CSV")).toBeEnabled();
    const uploadPaths: string[] = [];
    const rejections: unknown[] = [];
    page.on("request", (req) => {
      if (/\/batches\/[^/]+\/files$/.test(req.url()))
        uploadPaths.push(req.url());
      if (/\/batches\/[^/]+\/rejections$/.test(req.url()))
        rejections.push(req.postDataJSON());
    });
    await page.getByLabel("Выбрать папку CSV").setInputFiles(directory);
    await expect(
      page.getByText("2 файлов, локальных ошибок: 1."),
    ).toBeVisible();
    await expect(page.getByText(/Пропущено файлов другого типа: 1/)).toBeVisible();
    await page
      .getByRole("button", { name: "Загрузить и запустить очередь" })
      .click();
    await expect(page).toHaveURL(/batch=/);
    const batchId = new URL(page.url()).searchParams.get("batch")!;
    const region = page.getByRole("region", { name: "Текущая очередь" });
    await expect(
      region.getByText("Завершено с ошибками", { exact: true }),
    ).toBeVisible();
    expect(uploadPaths).toHaveLength(1);
    expect(rejections).toHaveLength(1);
    expect(rejections[0]).toMatchObject({
      relative_path: `${basename(directory)}/bad.csv`,
      error_code: "invalid_csv_header",
    });
    const before: Batch = await (
      await request.get(`/api/v1/batches/${batchId}`)
    ).json();
    expect(before.progress).toEqual({ completed: 4, failed: 2, total: 4 });
    expect(
      before.results.filter((result) => result.status === "completed"),
    ).toHaveLength(2);
    expect(before.files.find((file) => file.status === "ready")).toMatchObject(
      descriptor(`${basename(directory)}/nested/sample.csv`, csv),
    );
    const completedIds = before.results
      .filter((result) => result.status === "completed")
      .map((result) => result.prediction_id)
      .sort();
    await expect(region.getByText(/Проверка браузера/)).toBeVisible();
    await expect(region.getByText(/Обнаружены:/)).toHaveCount(2);
    await page.reload();
    await expect(
      region.getByText("Завершено с ошибками", { exact: true }),
    ).toBeVisible();
    const resumed = page.waitForResponse((response) =>
      response.url().endsWith(`/batches/${batchId}/resume`),
    );
    await region
      .getByRole("button", { name: "Повторить незавершённые" })
      .click();
    expect((await resumed).ok()).toBe(true);
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/v1/batches/${batchId}`)).json())
            .status,
      )
      .toBe("partial");
    await expect(
      region.getByText("Завершено с ошибками", { exact: true }),
    ).toBeVisible();
    const after: Batch = await (
      await request.get(`/api/v1/batches/${batchId}`)
    ).json();
    expect(after.progress).toEqual({ completed: 4, failed: 2, total: 4 });
    expect(
      after.results
        .filter((result) => result.status === "completed")
        .map((result) => result.prediction_id)
        .sort(),
    ).toEqual(completedIds);
    const downloadPromise = page.waitForEvent("download");
    await region.getByRole("button", { name: "Сводный отчёт JSON" }).click();
    const downloaded = await downloadPromise;
    const exported = JSON.parse(
      await readFile((await downloaded.path())!, "utf8"),
    );
    expect(exported.record.batch_id).toBe(batchId);
    expect(
      exported.record.model_snapshots[trained.model_id].model_version,
    ).toBe(trained.version);
    expect(
      exported.record.model_snapshots[trained.model_id].reference_sha256,
    ).toMatch(/^[a-f0-9]{64}$/);
    expect(
      exported.record.results.filter(
        (result: { prediction?: unknown }) => result.prediction,
      ),
    ).toHaveLength(2);
    expect(
      exported.record.files.find(
        (file: { status: string }) => file.status === "invalid",
      ).validation_origin,
    ).toBe("client");
    expect(exported.manifest.files["summary.csv"].sha256).toMatch(
      /^[a-f0-9]{64}$/,
    );
    await region.getByText("Зафиксированные модели и источник").click();
    await expect(
      region.getByText("Оценки разных типов", { exact: false }),
    ).toBeVisible();
    await region.getByText("Зафиксированные модели и источник").click();
    await expect(region.locator(".tag")).toHaveText("Завершено с ошибками");
    await page.screenshot({
      path: info.outputPath("batch-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 820, height: 1100 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: info.outputPath("batch-820.png"),
      fullPage: true,
    });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("draft restored from server accepts only missing files and rejects changed content", async ({
  page,
  request,
}) => {
  const csv = await generatedCsv(request);
  const created = await request.post("/api/v1/batches", {
    data: {
      name: "Interrupted intake",
      model_ids: ["builtin-nnls"],
      expected_files: [
        descriptor("first.csv", csv),
        descriptor("second.csv", csv),
      ],
      idempotency_key: randomUUID(),
    },
  });
  expect(created.status()).toBe(201);
  const batch: Batch = await created.json();
  const upload = await request.post(`/api/v1/batches/${batch.batch_id}/files`, {
    multipart: {
      file: {
        name: "first.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(csv),
      },
      relative_path: "first.csv",
      units: "nm",
      wavelength_medium: "vacuum",
    },
  });
  expect(upload.ok()).toBe(true);
  await page.goto(`/batch?batch=${batch.batch_id}`);
  await expect(page.getByText(/Ожидают загрузки: 1/)).toBeVisible();
  const uploadedFiles: string[] = [];
  page.on("request", (req) => {
    if (/\/batches\/[^/]+\/files$/.test(req.url()))
      uploadedFiles.push(req.url());
  });
  await page.getByLabel("Выбрать CSV-файлы").setInputFiles({
    name: "second.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv + "\n"),
  });
  await page.getByRole("button", { name: "Продолжить загрузку" }).click();
  await expect(page.getByText(/Содержимое изменилось/).first()).toBeVisible();
  expect(uploadedFiles).toHaveLength(0);
  await page.reload();
  await page.getByLabel("Выбрать CSV-файлы").setInputFiles(
    ["first.csv", "second.csv"].map((name) => ({
      name,
      mimeType: "text/csv",
      buffer: Buffer.from(csv),
    })),
  );
  await page.getByRole("button", { name: "Продолжить загрузку" }).click();
  const region = page.getByRole("region", { name: "Текущая очередь" });
  await expect(region.locator(".tag")).toHaveText("Завершено");
  expect(uploadedFiles).toHaveLength(1);
  const final: Batch = await (
    await request.get(`/api/v1/batches/${batch.batch_id}`)
  ).json();
  expect(final.progress).toEqual({ completed: 2, total: 2, failed: 0 });
  expect(
    final.results.map((result) => result.prediction_id).every(Boolean),
  ).toBe(true);
});
