import { expect, test } from "@playwright/test";

function createSpectrumCsv(): string {
  const rows = ["wavelength_nm,intensity"];
  for (let w = 350.0; w <= 800.0; w += 0.5) {
    let flux = 2.0;
    if (Math.abs(w - 373.593) < 0.25) flux += 25.0; // Fe I
    if (Math.abs(w - 393.477) < 0.25) flux += 30.0; // Ca II
    rows.push(`${w.toFixed(2)},${flux.toFixed(2)}`);
  }
  return rows.join("\n");
}

async function sha256Hex(content: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(content);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
}

test("Batch Quantitative Assay card renders matrix summary and allows CSV export in Batches page", async ({
  page,
  request,
}) => {
  const csvContent = createSpectrumCsv();
  const fileSha = await sha256Hex(csvContent);
  const fileBytes = Buffer.from(csvContent, "utf-8");

  // 1. Create batch via API
  const createRes = await request.post("/api/v1/batches", {
    data: {
      name: "Assay Matrix E2E Test Batch",
      model_ids: ["builtin-nnls"],
      expected_files: [
        {
          relative_path: "e2e_samples/sample_assay.csv",
          sha256: fileSha,
          size_bytes: fileBytes.length,
        },
      ],
      idempotency_key: `assay-e2e-${Date.now()}`,
      measurement_metadata: { sample_id: "E2E-SAMPLE-1" },
    },
  });
  expect(createRes.ok()).toBe(true);
  const batch = await createRes.json();
  const batchId = batch.batch_id;

  // 2. Upload file
  const uploadRes = await request.post(`/api/v1/batches/${batchId}/files`, {
    multipart: {
      relative_path: "e2e_samples/sample_assay.csv",
      file: {
        name: "sample_assay.csv",
        mimeType: "text/csv",
        buffer: fileBytes,
      },
    },
  });
  expect(uploadRes.ok()).toBe(true);

  // 3. Navigate to Batches page with batch selected
  await page.goto(`/batch?batch=${batchId}`);

  // 4. BatchAssayCard should appear and load matrix
  const assayCard = page.getByTestId("batch-assay-card");
  await expect(assayCard).toBeVisible();
  await expect(
    assayCard.getByText("Количественный анализ оксидов и матричный аудит применимости (Assay Matrix)")
  ).toBeVisible();

  // Check sample row and sample ID
  await expect(assayCard.getByText("e2e_samples/sample_assay.csv")).toBeVisible();
  await expect(assayCard.getByText("ID: E2E-SAMPLE-1")).toBeVisible();

  // Check Export button
  const exportBtn = assayCard.getByRole("link", { name: /Экспорт матрицы \(CSV\)/i });
  await expect(exportBtn).toBeVisible();
  const href = await exportBtn.getAttribute("href");
  expect(href).toContain(`/api/v1/batches/${batchId}/assay-matrix/download`);

  // 5. Change confidence level to 99%
  const confSelect = assayCard.getByLabel(/Уровень доверия/i);
  await confSelect.selectOption("0.99");
  await expect(assayCard.getByText("Доверие: 99%")).toBeVisible();
});

