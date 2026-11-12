import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiJson } from "../../src/api/client";

describe("apiJson", () => {
  afterEach(() => vi.restoreAllMocks());

  it("throws a clear ApiError when HTML is returned instead of JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response("<!doctype html><html><body>SPA fallback</body></html>", {
            status: 200,
            headers: { "Content-Type": "text/html" },
          }),
        ),
      ),
    );
    await expect(apiJson("/api/v1/examples")).rejects.toThrow(ApiError);
    await expect(apiJson("/api/v1/examples")).rejects.toThrow(/Backend API unavailable/);
  });
});
