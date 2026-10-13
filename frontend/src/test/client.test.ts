import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, request, validateUpload } from "../api/client";
import { capability } from "./fixtures";
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
describe("request recovery", () => {
  it("preserves network failure and permits a retry", async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(new Response('{"ok":true}'));
    vi.stubGlobal("fetch", fetch);
    await expect(request("/health")).rejects.toMatchObject({
      code: "network_error",
    });
    await expect(request("/health")).resolves.toEqual({ ok: true });
  });
  it("handles an HTML proxy 502 without exposing HTML", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("<html>proxy error</html>", { status: 502 }),
        ),
    );
    await expect(request("/models")).rejects.toMatchObject({
      code: "invalid_response",
      status: 502,
    });
  });
  it("retains field-level validation errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({
              error: {
                code: "validation_error",
                message: "Bad seed",
                details: [{ loc: ["body", "seed"], msg: "Must be positive" }],
              },
            }),
            { status: 422 },
          ),
        ),
    );
    await expect(request("/spectra/generate")).rejects.toMatchObject({
      code: "validation_error",
      details: [{ loc: ["body", "seed"], msg: "Must be positive" }],
    });
  });
  it("differentiates timeout and caller cancellation", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(
          (_url, init: RequestInit) =>
            new Promise((_resolve, reject) =>
              init.signal?.addEventListener("abort", () =>
                reject(new DOMException("Aborted", "AbortError")),
              ),
            ),
        ),
    );
    const timeout = request("/predict", {}, { timeoutMs: 20 });
    const assertion = expect(timeout).rejects.toMatchObject({
      code: "timeout",
    });
    await vi.advanceTimersByTimeAsync(21);
    await assertion;
    const controller = new AbortController();
    const pending = request("/predict", {}, { signal: controller.signal });
    const cancelled = expect(pending).rejects.toMatchObject({
      code: "cancelled",
    });
    controller.abort();
    await cancelled;
  });
});
describe("client CSV validation", () => {
  it.each([
    ["bad.csv", "wave,intensity\n350,1\n800,2", "invalid_csv_header"],
    ["bad.csv", "wavelength_nm,intensity\n350,NaN\n800,2", "invalid_csv_value"],
    [
      "bad.csv",
      "wavelength_nm,intensity\n350,1\n800,Infinity",
      "invalid_csv_value",
    ],
    ["bad.csv", "wavelength_nm,intensity\n350,\n800,2", "invalid_csv_value"],
  ])("rejects malformed CSV %s %s", async (name, content, code) => {
    await expect(
      validateUpload(new File([content], name), "vacuum", capability),
    ).rejects.toMatchObject({ code });
  });
  it("rejects air and files over the server limit", async () => {
    const file = new File(
      ["wavelength_nm,intensity\n350,1\n800,2"],
      "spectrum.csv",
    );
    await expect(
      validateUpload(file, "air", capability),
    ).rejects.toBeInstanceOf(ApiError);
    await expect(
      validateUpload(file, "vacuum", {
        ...capability,
        spectrum: { ...capability.spectrum, max_upload_bytes: 1 },
      }),
    ).rejects.toMatchObject({ code: "file_too_large" });
    await expect(
      validateUpload(file, "vacuum", capability),
    ).resolves.toBeUndefined();
  });
});
