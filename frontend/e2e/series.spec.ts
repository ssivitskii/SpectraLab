import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("shot series: load demo, submit analysis, render QC diagnostics, export receipt and mobile layout", async ({
  page,
}) => {
  await page.goto("/series");

  // 1. Initial empty state check
  await expect(page.getByRole("heading", { name: "Серии выстрелов и контроль качества", exact: true })).toBeVisible();
  await expect(page.getByText("Серия выстрелов не загружена")).toBeVisible();
  const submitButton = page.getByRole("button", { name: "Анализировать серию", exact: true });
  await expect(submitButton).toBeDisabled();

  // 2. Click load demo button
  await page.getByRole("button", { name: /Демо-серия/ }).click();
  await expect(page.getByText(/Используется демо-набор/)).toBeVisible();
  await expect(submitButton).toBeEnabled();

  // 3. Submit analysis and wait for API response
  const pendingResponse = page.waitForResponse(
    (response) => response.url().includes("/api/v1/series/analyze") && response.request().method() === "POST"
  );
  await submitButton.click();

  const response = await pendingResponse;
  expect(response.status()).toBe(200);
  const data = await response.json();
  expect(data.status).toBe("completed");
  expect(data.shot_count).toBe(4);
  expect(data.accepted_shot_count).toBeGreaterThanOrEqual(2);

  // 4. Results check
  await expect(page.getByRole("heading", { name: "Сводка качества серии", exact: true })).toBeVisible();
  await expect(page.getByText("Успешно агрегирована", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Диагностика каждого выстрела серии", exact: true })).toBeVisible();

  // 5. Test downloading receipt.json
  const pendingDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: /Скачать receipt.json/ }).click();
  const download = await pendingDownload;
  expect(download.suggestedFilename()).toBe("receipt.json");
  const payload = await readFile((await download.path())!);
  const receipt = JSON.parse(payload.toString("utf8"));
  expect(receipt.series_id).toBe(data.series_id);
  expect(receipt.status).toBe("completed");

  // 6. Mobile layout responsiveness check
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByRole("button", { name: /Скачать receipt.json/ })).toBeVisible();
});

