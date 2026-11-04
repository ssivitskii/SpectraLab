import { expect, test } from "@playwright/test";

test("Plasma diagnostics card displays Boltzmann temperature and McWhirter status in Analysis", async ({
  page,
  request,
}) => {
  // Generate spectrum with Fe and H
  const generated = await request.post("/api/v1/spectra/generate", {
    data: {
      elements: ["Fe", "H", "Na"],
      component_weights: [1.0, 0.8, 0.5],
      seed: 42,
    },
  });
  expect(generated.ok()).toBe(true);
  const spectrum = await generated.json();

  // Navigate to Analysis page with the generated spectrum
  await page.goto(`/analysis?spectrum=${spectrum.spectrum_id}`);

  // Plasma Diagnostics card should be rendered
  const plasmaCard = page.getByTestId("plasma-diagnostics-card");
  await expect(plasmaCard).toBeVisible();
  await expect(plasmaCard.getByRole("heading", { name: "Параметры плазмы и LTE" })).toBeVisible();

  // Wait for diagnostics result
  await expect(plasmaCard.getByText("Температура Te (Больцман)")).toBeVisible();
  await expect(plasmaCard.getByText("Критерий МакВиртера (LTE)")).toBeVisible();

  // Verify element switcher buttons exist
  const autoBtn = plasmaCard.getByRole("button", { name: "Авто" });
  await expect(autoBtn).toBeVisible();

  const feBtn = plasmaCard.getByRole("button", { name: "Fe I" });
  await expect(feBtn).toBeVisible();

  // Click Fe I button and verify diagnostics request
  const plasmaRequestPromise = page.waitForResponse(
    (res) => res.url().includes("/plasma/diagnostics") && res.request().method() === "POST",
  );
  await feBtn.click();
  const plasmaResponse = await plasmaRequestPromise;
  expect(plasmaResponse.status()).toBe(200);
  const data = await plasmaResponse.json();
  expect(data.summary).toBeDefined();
});

