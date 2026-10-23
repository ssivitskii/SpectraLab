import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

// Synthetic constant signal exercises transport and UI only; it is not a
// composition standard, a blank control or evidence of model quality.
test("measured fixture: submit, reload, export and phone layout", async ({ page, request }) => {
  const profiles = await (await request.get("/api/v1/instrument-profiles")).json();
  const profile = profiles.find((item: { profile_id: string }) => item.profile_id === "calcium-v4");
  expect(profile.ready).toBe(true);
  expect(profile.final_test_status).toBe("not_performed");
  const rows = ["wavelength_nm,intensity"];
  const step = Math.min(0.05, profile.coverage_requirements.max_gap_nm / 2);
  for (const [start, end] of profile.coverage_requirements.bands_nm) {
    const intervals = Math.ceil((end - start) / step);
    for (let index = 0; index <= intervals; index += 1) rows.push(`${(start + (end - start) * index / intervals).toFixed(8)},1`);
  }
  await page.goto("/measured");
  await expect(page.getByRole("note").getByText("Исследовательский режим", { exact: true })).toBeVisible();
  await page.getByLabel("Файл спектра (CSV)").setInputFiles({ name: "e2e-synthetic-constant.csv", mimeType: "text/csv", buffer: Buffer.from(rows.join("\n")) });
  await page.getByRole("button", { name: /Подставить прибор профиля/ }).click();
  await page.getByRole("checkbox", { name: /Подтверждаю, что длина волны/ }).check();
  await page.getByRole("combobox", { name: /Среда измерения шкалы/ }).selectOption("vacuum");
  await page.getByLabel("Источник калибровки оси (source)").fill("E2E synthetic axis fixture; no real measurement");
  await page.getByLabel("SHA-256 источника оси (source_sha256)").fill(createHash("sha256").update("E2E synthetic axis fixture; no real measurement").digest("hex"));
  const createdResponse = page.waitForResponse((response) => response.url().endsWith("/measured-predictions") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Выполнить анализ измерения" }).click();
  const response = await createdResponse;
  expect(response.status()).toBe(200);
  const prediction = await response.json();
  expect(prediction.final_test_status).toBe("not_performed");
  expect(prediction.qualification).toBe("development_only_no_independent_blank_validation");
  await expect(page).toHaveURL(new RegExp(`prediction=${prediction.prediction_id}`));
  await expect(page.getByRole("heading", { name: "Результат измерения", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText(`ID: ${prediction.prediction_id}`, { exact: true })).toBeVisible();
  await expect(page.getByText("Не поддерживается", { exact: true }).first()).toBeVisible();
  for (const format of ["JSON", "CSV", "ZIP"]) {
    const pendingDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: `Экспорт ${format}`, exact: true }).click();
    const download = await pendingDownload;
    const payload = await readFile((await download.path())!);
    if (format === "JSON") {
      const exported = JSON.parse(payload.toString("utf8"));
      expect(exported.record.prediction_id).toBe(prediction.prediction_id);
      expect(exported.record.final_test_status).toBe("not_performed");
      expect(exported.manifest.kind).toBe("measured_prediction");
    } else if (format === "CSV") expect(payload.toString("utf8")).toMatch(/^element,status,score,threshold/);
    else expect(payload.subarray(0, 2).toString()).toBe("PK");
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByRole("button", { name: "Экспорт ZIP", exact: true })).toBeVisible();
});

test("system status: real worker, explicit backup state and phone layout", async ({ page, request }) => {
  await expect.poll(async () => (await (await request.get("/api/v1/system/status")).json()).worker.state).toBe("running");
  await page.goto("/system");
  await expect(page.getByText("Активен (running)", { exact: true })).toBeVisible();
  await expect(page.getByText("Нет данных", { exact: true })).toBeVisible();
  await expect(page.getByText(/Данные о верифицированных архивных копиях отсутствуют/)).toBeVisible();
  await page.getByRole("button", { name: "Обновить состояние системы" }).click();
  await expect(page.getByRole("button", { name: "Обновить состояние системы" })).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByRole("table").first()).toBeVisible();
});
