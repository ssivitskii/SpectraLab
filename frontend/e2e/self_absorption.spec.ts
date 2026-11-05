import { expect, test } from "@playwright/test";

test("Self-absorption and CF-LIBS card displays doublet optical depth and CF-LIBS elemental inversion", async ({
  page,
  request,
}) => {
  // Generate spectrum with Mg and Ca
  const generated = await request.post("/api/v1/spectra/generate", {
    data: {
      elements: ["Mg", "Ca"],
      component_weights: [1.0, 0.8],
      seed: 42,
    },
  });
  expect(generated.ok()).toBe(true);
  const spectrum = await generated.json();

  // Navigate to Analysis page with the generated spectrum
  await page.goto(`/analysis?spectrum=${spectrum.spectrum_id}`);

  // Self-Absorption Card should be visible
  const card = page.getByTestId("self-absorption-card");
  await expect(card).toBeVisible();
  await expect(
    card.getByRole("heading", {
      name: "Оптическая толщина и CF-LIBS (LTE)",
    }),
  ).toBeVisible();

  // Wait for doublet results to appear
  await expect(card.getByText("Mg II 279.64 / 280.35")).toBeVisible();
  await expect(card.getByText("Ca II 393.48 / 396.96")).toBeVisible();

  // CF-LIBS section
  await expect(
    card.getByText("Бескалибровочный элементный состав (CF-LIBS)"),
  ).toBeVisible();

  // Click details toggle button
  const detailsBtn = card.getByRole("button", { name: "Подробнее" });
  if (await detailsBtn.isVisible()) {
    await detailsBtn.click();
    await expect(card.getByText("Скрыть детали")).toBeVisible();
  }
});

