import { expect, test } from "@playwright/test";

test("Measurement Dossier modal generates comprehensive report and line attribution in Analysis", async ({
  page,
  request,
}) => {
  // Generate synthetic spectrum with Fe, Mg, Ca
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

  // Button to generate dossier should be visible
  const dossierBtn = page.getByTestId("generate-dossier-btn");
  await expect(dossierBtn).toBeVisible();

  // Listen for the dossier generation API request
  const dossierResponsePromise = page.waitForResponse(
    (res) =>
      res.url().includes("/dossier/generate") &&
      res.request().method() === "POST",
  );

  await dossierBtn.click();

  const dossierResponse = await dossierResponsePromise;
  expect(dossierResponse.status()).toBe(200);
  const dossierData = await dossierResponse.json();
  expect(dossierData.dossier_id).toBeDefined();
  expect(dossierData.explainability.lines.length).toBeGreaterThan(0);

  // Modal dialog should be visible
  const modal = page.getByTestId("dossier-modal");
  await expect(modal).toBeVisible();
  await expect(modal.getByText("Измерительное досье спектра")).toBeVisible();
  await expect(modal.getByText(dossierData.dossier_id)).toBeVisible();

  // Check lines tab is active and shows lines
  await expect(modal.getByRole("button", { name: /Линии NIST/i })).toBeVisible();

  // Switch to Assay tab
  const assayTab = modal.getByRole("button", { name: /Оксидный состав \(Assay\)/i });
  await assayTab.click();
  await expect(modal.getByText("FeOT")).toBeVisible();

  // Switch to Protocol tab
  const protocolTab = modal.getByRole("button", { name: /Протокол \(Markdown\)/i });
  await protocolTab.click();
  await expect(modal.getByText(/# Измерительное досье спектра/)).toBeVisible();

  // Close modal
  const closeBtn = modal.getByRole("button", { name: "Закрыть" });
  await closeBtn.click();
  await expect(modal).not.toBeVisible();
});

