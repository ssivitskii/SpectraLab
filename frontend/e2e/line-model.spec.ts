import { expect, test } from "@playwright/test";

for (const modelId of ["line-logistic", "line-logistic-robust"]) {
  test(`${modelId} can be selected and used from Analysis`, async ({ page, request }) => {
    const models = await (await request.get("/api/v1/models")).json();
    const candidate = models.find((model: { model_id: string }) => model.model_id === modelId);
    expect(candidate.ready).toBe(true);
    expect(candidate.calibrated).toBe(false);
    const generated = await request.post("/api/v1/spectra/generate", {
      data: { elements: ["H", "Na"], component_weights: [1, 0.7], calibration_shift_nm: 0.5, seed: 19 },
    });
    expect(generated.ok()).toBe(true);
    const spectrum = await generated.json();
    await page.goto(`/analysis?spectrum=${spectrum.spectrum_id}`);
    await page.getByRole("combobox", { name: "Метод распознавания", exact: true }).selectOption(modelId);
    const pending = page.waitForResponse((response) =>
      response.url().endsWith("/predict") && response.request().method() === "POST" &&
      response.request().postDataJSON().model_id === modelId,
    );
    await page.getByRole("button", { name: "Распознать элементы" }).click();
    const response = await pending;
    expect(response.status()).toBe(200);
    const prediction = await response.json();
    expect(prediction.model_id).toBe(modelId);
    expect(prediction.spectrum_id).toBe(spectrum.spectrum_id);
    expect(prediction.score_kind).toBe("line_logistic_decision_function");
    expect(prediction.scores).toHaveLength(6);
    for (const score of prediction.scores) expect(Number.isFinite(score.value)).toBe(true);
    await expect(page.getByLabel("Результат распознавания", { exact: true })).toBeVisible();
  });
}
