import { expect, test } from "@playwright/test";

test("Applicability domain space toggle displays PCA projection, Hotelling T2 ellipse and Q-residuals", async ({
  page,
  request,
}) => {
  // Generate spectrum
  const generated = await request.post("/api/v1/spectra/generate", {
    data: {
      elements: ["Fe", "Mg", "Ca"],
      component_weights: [1.0, 0.6, 0.4],
      seed: 42,
    },
  });
  expect(generated.ok()).toBe(true);
  const spectrum = await generated.json();

  // Navigate to Analysis page
  await page.goto(`/analysis?spectrum=${spectrum.spectrum_id}`);

  // Find Quantitative Assay Card
  const assayCard = page.getByTestId("quantitative-assay-card");
  await expect(assayCard).toBeVisible();

  // Click toggle button for Applicability Domain Space (PCA)
  const togglePcaBtn = assayCard.getByTestId("toggle-pca-btn");
  await expect(togglePcaBtn).toBeVisible();
  await togglePcaBtn.click();

  // Verify Applicability Domain card is displayed
  const adCard = page.getByTestId("applicability-domain-card");
  await expect(adCard).toBeVisible();
  await expect(
    adCard.getByRole("heading", {
      name: "Пространство применимости (PCA & Hotelling T²)",
    }),
  ).toBeVisible();

  // Verify metrics are loaded
  await expect(adCard.getByText("Hotelling T² (порог 95%)")).toBeVisible();
  await expect(adCard.getByText("Q-residual (порог 95%)")).toBeVisible();

  // Verify influence decomposition section
  await expect(
    adCard.getByText("Вклад спектральных линий в невязку (Influence decomposition)"),
  ).toBeVisible();
});

