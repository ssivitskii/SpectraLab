import { expect, test } from "@playwright/test";

test("Quantitative assay card displays oxide predictions, conformal intervals and AD status in Analysis", async ({
  page,
  request,
}) => {
  // Generate spectrum with Fe, Mg, and Ca
  const generated = await request.post("/api/v1/spectra/generate", {
    data: {
      elements: ["Fe", "Mg", "Ca"],
      component_weights: [1.0, 0.6, 0.4],
      seed: 42,
    },
  });
  expect(generated.ok()).toBe(true);
  const spectrum = await generated.json();

  // Navigate to Analysis page with the generated spectrum
  await page.goto(`/analysis?spectrum=${spectrum.spectrum_id}`);

  // Quantitative Assay Card should be rendered
  const assayCard = page.getByTestId("quantitative-assay-card");
  await expect(assayCard).toBeVisible();
  await expect(
    assayCard.getByRole("heading", {
      name: "Количественный анализ (Assay & Конформные интервалы)",
    }),
  ).toBeVisible();

  // Wait for predictions to load
  await expect(assayCard.getByTestId("oxide-card-FeOT")).toBeVisible();
  await expect(assayCard.getByTestId("oxide-card-MgO")).toBeVisible();

  // Total detected mass summary
  await expect(assayCard.getByText("Сумма определенных оксидов:")).toBeVisible();

  // Verify changing confidence level to 99% triggers API request
  const predictRequestPromise = page.waitForResponse(
    (res) =>
      res.url().includes("/quantitative/predict") &&
      res.request().method() === "POST",
  );

  const confidenceSelect = assayCard.getByTestId("confidence-select");
  await confidenceSelect.selectOption("0.99");

  const predictResponse = await predictRequestPromise;
  expect(predictResponse.status()).toBe(200);
  const data = await predictResponse.json();
  expect(data.confidence_level).toBe(0.99);
  expect(data.predictions.length).toBeGreaterThan(0);

  // Toggle table view
  const toggleBtn = assayCard.getByTestId("toggle-table-btn");
  await toggleBtn.click();
  await expect(assayCard.getByText("Оценка (wt%)")).toBeVisible();
  await expect(assayCard.getByText("Leverage h(x)")).toBeVisible();
});

