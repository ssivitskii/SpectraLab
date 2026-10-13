import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
test("generator → analysis → compare → export, history and preset", async ({
  page,
  request,
}, info) => {
  const capabilities = await (await request.get("/api/v1/capabilities")).json();
  const models = await (await request.get("/api/v1/models")).json();
  expect(
    models.filter((model: { ready: boolean }) => model.ready).length,
  ).toBeGreaterThan(1);
  await page.goto("/generator");
  await page
    .getByRole("button", {
      name: `${capabilities.elements[0]} · стадия ${capabilities.generation.ion_stages[0]}`,
      exact: true,
    })
    .click();
  await page.getByText("Пресет генератора", { exact: true }).click();
  await page.getByRole("button", { name: "Сохранить пресет" }).click();
  await expect(page.getByText("Пресет сохранён.")).toBeVisible();
  await page.getByLabel("Random seed").fill("43");
  await page.getByRole("button", { name: "Восстановить пресет" }).click();
  await expect(page.getByLabel("Random seed")).toHaveValue("42");
  const generationPromise = page.waitForResponse(
    (response) =>
      response.url().includes("/spectra/generate") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Сгенерировать спектр" }).click();
  const generated = await (await generationPromise).json();
  expect(generated.reference_source).toBe(capabilities.reference.source);
  expect(generated.parameters.seed).toBe(42);
  await expect(page.getByText("Истинный состав (генератор)")).toBeVisible();
  await expect(page.locator(".js-plotly-plot")).toBeVisible();
  await expect(
    page.getByText("Не удалось загрузить график", { exact: false }),
  ).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("Истинный состав (генератор)")).toBeVisible();
  await page.getByRole("link", { name: "Передать на анализ →" }).click();
  await expect(page.getByText("Истинный состав (генератор)")).toHaveCount(0);
  await page.getByText("Сравнить несколько моделей", { exact: true }).click();
  const trained = models.find(
    (model: { ready: boolean; model_id: string }) =>
      model.ready && model.model_id !== "builtin-nnls",
  );
  await page
    .getByRole("checkbox", {
      name: `${trained.model_id} · v${trained.version}`,
      exact: true,
    })
    .check();
  const predictionPromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/predict") &&
      response.request().postDataJSON().model_id === "builtin-nnls",
  );
  await page.getByRole("button", { name: "Распознать элементы" }).click();
  const response = await predictionPromise;
  expect(Object.keys(response.request().postDataJSON()).sort()).toEqual([
    "input_type",
    "model_id",
    "spectrum_id",
  ]);
  const prediction = await response.json();
  expect(prediction.spectrum_id).toBe(generated.spectrum_id);
  expect(prediction.reference_source).toBe(generated.reference_source);
  expect(prediction.model_id).toBe("builtin-nnls");
  const result = page.getByLabel("Результат распознавания", { exact: true });
  await expect(
    result.getByText(prediction.score_kind, { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel(`Результат ${trained.model_id}`, { exact: true }),
  ).toContainText(trained.model_id);
  await page.getByText(/Таблица справочных линий/).click();
  await page
    .getByRole("button", { name: /Выделить/ })
    .first()
    .focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".line-active")).toHaveCount(1);
  await page.getByLabel("Начало диапазона").fill("400");
  await page.getByLabel("Конец диапазона").fill("500");
  await page.getByRole("button", { name: "Показать диапазон" }).press("Enter");
  const plotRange = () =>
    page
      .locator(".js-plotly-plot")
      .evaluate(
        (node) =>
          (node as unknown as { _fullLayout: { xaxis: { range: number[] } } })
            ._fullLayout.xaxis.range,
      );
  await expect.poll(plotRange).toEqual([400, 500]);
  await page
    .getByRole("checkbox", { name: "Чистый сигнал", exact: true })
    .uncheck();
  await expect.poll(plotRange).toEqual([400, 500]);
  const downloadPromise = page.waitForEvent("download");
  await result.getByRole("button", { name: "Экспорт результата JSON" }).click();
  const download = await downloadPromise;
  const exported = JSON.parse(await readFile((await download.path())!, "utf8"));
  expect(exported.record.model_id).toBe(prediction.model_id);
  expect(exported.record.model_version).toBe(prediction.model_version);
  expect(exported.record.warnings).toEqual(prediction.warnings);
  expect(exported.manifest).toBeTruthy();
  await page.getByText("Метаданные измерения", { exact: true }).click();
  await page.getByLabel("Образец", { exact: true }).fill("E2E-sample");
  await page.getByRole("button", { name: "Сохранить метаданные" }).click();
  await expect(page.getByText("Метаданные сохранены.")).toBeVisible();
  await page.reload();
  await page.getByText("Метаданные измерения", { exact: true }).click();
  await expect(page.getByLabel("Образец", { exact: true })).toHaveValue(
    "E2E-sample",
  );
  await page.getByText("История спектров", { exact: true }).click();
  await expect(page.getByRole("button", { name: /E2E-sample/ })).toBeVisible();
  await page.screenshot({
    path: info.outputPath("analysis-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 820, height: 1100 });
  await expect(
    page.getByRole("button", { name: "Показать диапазон" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByLabel("Начало диапазона").fill("400");
  await page.getByLabel("Конец диапазона").fill("500");
  await page.getByRole("button", { name: "Показать диапазон" }).press("Enter");
  await page.screenshot({
    path: info.outputPath("analysis-820.png"),
    fullPage: true,
  });
});
test("real CSV upload, client rejection without POST, missing spectrum and unavailable model", async ({
  page,
  request,
}) => {
  const capabilities = await (await request.get("/api/v1/capabilities")).json();
  const generate = await request.post("/api/v1/spectra/generate", {
    data: {
      elements: [capabilities.elements[0]],
      component_weights: [1],
      seed: 7,
    },
  });
  const spectrum = await generate.json();
  await page.goto("/analysis");
  await expect(page.getByText("Спектр ещё не выбран")).toBeVisible();
  await expect(page.getByLabel("Загрузить CSV")).toBeEnabled();
  const posts: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/spectra/upload")) posts.push(request.url());
  });
  await page
    .getByLabel("Загрузить CSV")
    .setInputFiles({
      name: "bad.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("wave,value\n350,NaN\n800,1"),
    });
  await expect(page.getByRole("alert")).toContainText("CSV должен начинаться");
  expect(posts).toHaveLength(0);
  const csv =
    "wavelength_nm,intensity\n" +
    spectrum.wavelength_nm
      .map((x: number, i: number) => `${x},${spectrum.intensity[i]}`)
      .join("\n");
  const uploadResponse = page.waitForResponse((response) =>
    response.url().includes("/spectra/upload"),
  );
  await page
    .getByLabel("Загрузить CSV")
    .setInputFiles({
      name: "sample.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(csv),
    });
  const uploaded = await (await uploadResponse).json();
  expect(uploaded.origin).toBe("uploaded");
  await expect(page).toHaveURL(new RegExp(uploaded.spectrum_id));
  await expect(page.getByText("Неизвестен", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Распознать элементы" }).click();
  await expect(
    page.getByLabel("Результат распознавания", { exact: true }),
  ).toContainText("builtin-nnls");
  const models = await (await request.get("/api/v1/models")).json();
  const unavailable = models.find((model: { ready: boolean }) => !model.ready);
  expect(unavailable).toBeTruthy();
  await expect(
    page.getByRole("option", { name: new RegExp(unavailable.model_id) }),
  ).toHaveAttribute("disabled", "");
  await expect(
    page.getByText(`${unavailable.model_id}:`, { exact: false }),
  ).toBeVisible();
  await page.goto(`/analysis?spectrum=${"f".repeat(32)}`);
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Повторить загрузку" }),
  ).toBeVisible();
});
test("actual experiments, empty filter and job idempotency/cancellation", async ({
  page,
}) => {
  await page.goto("/experiments");
  await page.getByRole("button", { name: /e2e-fixture/ }).click();
  await expect(page).toHaveURL(/run=e2e-fixture/);
  await expect(
    page.getByRole("heading", { name: "e2e-fixture" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "e2e-fixture" }),
  ).toBeVisible();
  await page
    .getByLabel("Протокол", { exact: true })
    .fill("nonexistent-protocol");
  await expect(page.getByText("Экспериментов пока нет")).toBeVisible();
  await page.getByLabel("Протокол", { exact: true }).fill("");
  await page
    .getByText("Фоновые исследовательские задания", { exact: true })
    .click();
  await page.getByLabel("Seed серии").fill("101, 202");
  await page.getByRole("button", { name: "Создать задание" }).click();
  await expect(page).toHaveURL(/job=/);
  const jobId = new URL(page.url()).searchParams.get("job");
  await page.getByRole("button", { name: "Создать задание" }).click();
  await expect(page).toHaveURL(new RegExp(jobId!));
  await page.reload();
  await page
    .getByText("Фоновые исследовательские задания", { exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: new RegExp(jobId!) }),
  ).toBeVisible();
  await expect(page.getByLabel("Seed серии")).toHaveValue("101, 202");
  await page.getByRole("button", { name: "Создать задание" }).click();
  await expect(page).toHaveURL(new RegExp(jobId!));
  await page.getByRole("button", { name: "Отменить задание" }).click();
  await expect(
    page.getByRole("heading", { name: new RegExp(`Отменено.*${jobId}`) }),
  ).toBeVisible();
});
