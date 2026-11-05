import { expect, test } from "@playwright/test";

test("Calibration transfer card displays PDS profiles, applies transformation and updates diagnostics in Analysis", async ({
  page,
  request,
}) => {
  // Generate spectrum with Fe, Na and Mg
  const generated = await request.post("/api/v1/spectra/generate", {
    data: {
      elements: ["Fe", "Na", "Mg"],
      component_weights: [1.0, 0.5, 0.5],
      seed: 42,
    },
  });
  expect(generated.ok()).toBe(true);
  const spectrum = await generated.json();

  // Navigate to Analysis page
  await page.goto(`/analysis?spectrum=${spectrum.spectrum_id}`);

  // Calibration Transfer Card should be rendered
  const transferCard = page.getByTestId("calibration-transfer-card");
  await expect(transferCard).toBeVisible();
  await expect(
    transferCard.getByRole("heading", {
      name: "Калибровочный перенос спектра (Calibration Transfer / PDS)",
    }),
  ).toBeVisible();

  // Profile select should have standard profiles loaded
  const profileSelect = transferCard.getByTestId("transfer-profile-select");
  await expect(profileSelect).toBeVisible();

  // Wait for ChemCam PDS option to be available
  await expect(profileSelect.locator("option[value='chemcam_pds_v1']")).toBeAttached();

  // Select ChemCam PDS profile and verify API request
  const transferRequestPromise = page.waitForResponse(
    (res) =>
      res.url().includes("/transfer/apply") &&
      res.request().method() === "POST",
  );

  await profileSelect.selectOption("chemcam_pds_v1");

  const transferResponse = await transferRequestPromise;
  expect(transferResponse.status()).toBe(200);
  const data = await transferResponse.json();
  expect(data.profile_id).toBe("chemcam_pds_v1");
  expect(data.diagnostics).toBeDefined();

  // Verify diagnostic metric cards
  await expect(transferCard.getByText("PDS стандартизация")).toBeVisible();
  await expect(transferCard.getByText("СКО коррекции профиля")).toBeVisible();

  // Toggle diff view
  const toggleDiffBtn = transferCard.getByTestId("toggle-diff-btn");
  await expect(toggleDiffBtn).toBeVisible();
  await toggleDiffBtn.click();
  await expect(toggleDiffBtn).toHaveText("Скрыть разность (Δ)");
});

