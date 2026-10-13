import { test, expect } from "@playwright/test";
import { cpus, platform, arch, totalmem } from "node:os";
import { writeFile } from "node:fs/promises";
test("100000 points and 1000 reference lines: raw vs display envelope", async ({
  page,
  browser,
}, info) => {
  test.setTimeout(180000);
  await page.goto("/analysis");
  const measurements = await page.evaluate(async () => {
    // Import the shipped implementation: benchmark never changes analysis/export input.
    const { displaySeries, Plotly: plotly } = await import(
      "/e2e/benchmark-runtime.ts"
    );
    const x = Array.from({ length: 100000 }, (_, i) => 350 + (450 * i) / 99999);
    const y = x.map(
      (v, i) => Math.exp(-((v - 589) ** 2) / 0.02) + 0.1 * Math.sin(i / 7),
    );
    const lineX = Array.from({ length: 1000 }, (_, i) => 350 + i * 0.45);
    const target = document.createElement("div");
    target.style.width = "1000px";
    document.body.appendChild(target);
    const run = async (optimized: boolean) => {
      plotly.purge(target);
      const start = performance.now();
      const points = optimized ? displaySeries(x, y) : { x, y };
      const data = [{ ...points, type: "scatter", mode: "lines" }];
      if (optimized)
        data.push({
          x: lineX.flatMap((v) => [v, v, null]),
          y: lineX.flatMap(() => [0, 1, null]),
          type: "scatter",
          mode: "lines",
        });
      await plotly.newPlot(
        target,
        data,
        {
          width: 1000,
          height: 430,
          shapes: optimized
            ? []
            : lineX.map((v) => ({
                type: "line",
                x0: v,
                x1: v,
                y0: 0,
                y1: 1,
                yref: "paper",
              })),
        },
        { staticPlot: true },
      );
      await new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      );
      return {
        duration_ms: performance.now() - start,
        display_points: points.x.length,
      };
    };
    await run(false);
    await run(true);
    const before = [],
      after = [];
    for (let i = 0; i < 3; i++) {
      before.push(await run(false));
      after.push(await run(true));
    }
    plotly.purge(target);
    target.remove();
    return {
      points: x.length,
      reference_lines: lineX.length,
      before,
      after,
      user_agent: navigator.userAgent,
      logical_processors: navigator.hardwareConcurrency,
      viewport: [innerWidth, innerHeight],
      method:
        "3 alternating runs after warmup; performance.now through Plotly.newPlot + 2 requestAnimationFrame; raw SVG+1000 shapes versus min/max envelope+one reference trace",
    };
  });
  expect(
    measurements.after.every(
      (result: { display_points: number }) => result.display_points <= 4000,
    ),
  ).toBe(true);
  const report = {
    ...measurements,
    browser_version: browser.version(),
    device: {
      platform: platform(),
      architecture: arch(),
      cpu: cpus()[0]?.model,
      ram_gib: totalmem() / 1024 ** 3,
    },
    measured_at: new Date().toISOString(),
  };
  await writeFile(
    info.outputPath("dense-spectrum-benchmark.json"),
    JSON.stringify(report, null, 2),
  );
  await info.attach("dense-spectrum-benchmark", {
    body: JSON.stringify(report, null, 2),
    contentType: "application/json",
  });
  console.log(JSON.stringify(report));
});
