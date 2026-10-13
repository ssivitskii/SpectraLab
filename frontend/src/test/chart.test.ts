import { describe, expect, it } from "vitest";
import { displaySeries } from "../components/plotData";
describe("display-only envelope", () => {
  it("retains endpoints, peaks and troughs without modifying prediction arrays", () => {
    const x = Array.from({ length: 100000 }, (_, i) => i);
    const y = x.map(() => 0);
    y[137] = 999;
    y[76291] = -321;
    const before = y.slice();
    const view = displaySeries(x, y);
    expect(view.x.length).toBeLessThanOrEqual(4000);
    expect(view.x[0]).toBe(0);
    expect(view.x.at(-1)).toBe(99999);
    expect(view.y).toContain(999);
    expect(view.y).toContain(-321);
    expect(y).toEqual(before);
    expect(x).toHaveLength(100000);
  });
});
