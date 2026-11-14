import { expect, test, type Page } from "@playwright/test";

function attachErrorCollector(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      const txt = msg.text();
      // Ignore benign favicon or font 404s
      if (!txt.includes("favicon") && !txt.includes("font")) {
        errors.push(`console.error: ${txt}`);
      }
    }
  });
  return errors;
}

test.describe("Live Backend E2E Verification: 5 Benchmark Targets", () => {
  test("Live Backend Health & Examples Endpoint Contracts", async ({ request }) => {
    const healthRes = await request.get("/health");
    expect(healthRes.status()).toBe(200);
    const healthJson = await healthRes.json();
    expect(healthJson.status).toBe("ok");

    const exRes = await request.get("/api/v1/examples");
    expect(exRes.status()).toBe(200);
    const exData = await exRes.json();
    const targetIds = (exData.targets as Array<{ id: string }>).map((t) => t.id);

    expect(targetIds).toContain("barnards_star");
    expect(targetIds).toContain("eps_indi_b");
    expect(targetIds).toContain("wise_0855");
    expect(targetIds).toContain("rw_aur");
    expect(targetIds).toContain("r_dra");

    // Verify /api/v1/resolve for all 5 targets against reference catalog values
    const expected = [
      { name: "WISE 0855", ra: 133.795, dec: -7.245, pmra: -8123.7 },
      { name: "Barnard's Star", ra: 269.448, dec: 4.739, pmra: -801.55 },
      { name: "eps Ind B", ra: 331.044, dec: -56.783, pmra: 3981.98 },
      { name: "RW Aurigae", ra: 76.956, dec: 30.401, pmra: 2.64 },
      { name: "R Draconis", ra: 248.167, dec: 66.755, pmra: -20.64 },
    ];
    for (const t of expected) {
      const res = await request.get(`/api/v1/resolve?name=${encodeURIComponent(t.name)}`);
      expect(res.status()).toBe(200);
      const json = await res.json();
      expect(json.ra_deg).toBeCloseTo(t.ra, 2);
      expect(json.dec_deg).toBeCloseTo(t.dec, 2);
      expect(json.pmra_masyr).toBeCloseTo(t.pmra, 1);
    }
  });

  test("Target 1: WISE 0855 live cutouts, coordinates, and epoch strip", async ({ page }) => {
    const errors = attachErrorCollector(page);
    await page.goto("/v?ra=133.79513&dec=-7.24515&target=WISE+0855");

    await expect(page.locator(".viewer-presets")).toBeVisible({ timeout: 15_000 });
    const readout = page.locator(".viewer-sky-readout");
    await expect(readout).toBeVisible({ timeout: 15_000 });
    await expect(readout).toContainText("08h 55m");
    await expect(readout).toHaveText(/08h 55m.*[−-]07° 14′/);

    // Live cutouts loaded from backend
    const epochPills = page.locator(".viewer-epoch-btn");
    await expect(epochPills.first()).toBeVisible({ timeout: 30_000 });
    const pillCount = await epochPills.count();
    expect(pillCount).toBeGreaterThanOrEqual(1);

    // Verify epoch status display
    await expect(page.locator(".viewer-epoch-status")).toBeVisible();
    await expect(page.locator(".viewer-epoch-mjd")).toContainText("MJD");

    // Viewer stage canvas and WebP layer
    await expect(page.locator(".viewer-frame-box")).toBeVisible();
    expect(errors).toHaveLength(0);
  });

  test("Target 2: Barnard's Star live high PM mover preset and blink playback", async ({ page }) => {
    const errors = attachErrorCollector(page);
    await page.goto("/v?ra=269.44850&dec=4.73942&target=Barnard%27s+Star");

    await expect(page.locator(".viewer-presets")).toBeVisible({ timeout: 15_000 });
    const readout = page.locator(".viewer-sky-readout");
    await expect(readout).toBeVisible({ timeout: 15_000 });
    await expect(readout).toContainText("17h 57m");
    await expect(readout).toContainText("+04° 44′");

    // Chip active check
    const barnardChip = page.getByRole("button", { name: "Barnard's Star" });
    await expect(barnardChip).toHaveAttribute("aria-pressed", "true");

    // Epoch frames loaded
    const epochPills = page.locator(".viewer-epoch-btn");
    await expect(epochPills.first()).toBeVisible({ timeout: 30_000 });

    // Blink playback controls test
    const stepNext = page.getByRole("button", { name: /Next frame|next epoch/i });
    if (await stepNext.isVisible()) {
      await stepNext.click();
      await page.waitForTimeout(200);
    }
    expect(errors).toHaveLength(0);
  });

  test("Target 3: Epsilon Indi B unresolved brown dwarf live verification", async ({ page }) => {
    const errors = attachErrorCollector(page);
    await page.goto("/v?ra=331.04384&dec=-56.78271&target=eps+Ind+B");

    await expect(page.locator(".viewer-presets")).toBeVisible({ timeout: 15_000 });
    const readout = page.locator(".viewer-sky-readout");
    await expect(readout).toBeVisible({ timeout: 15_000 });
    await expect(readout).toContainText("22h 04m");
    await expect(readout).toHaveText(/22h 04m.*[−-]56° 46′/);

    const epochPills = page.locator(".viewer-epoch-btn");
    await expect(epochPills.first()).toBeVisible({ timeout: 30_000 });
    expect(errors).toHaveLength(0);
  });

  test("Target 4: RW Aurigae variable star live cutouts and compare mode", async ({ page }) => {
    const errors = attachErrorCollector(page);
    await page.goto("/v?ra=76.95608&dec=30.40120&target=RW+Aurigae");

    await expect(page.locator(".viewer-presets")).toBeVisible({ timeout: 15_000 });
    const readout = page.locator(".viewer-sky-readout");
    await expect(readout).toBeVisible({ timeout: 15_000 });
    await expect(readout).toContainText("05h 07m");
    await expect(readout).toContainText("+30° 24′");

    const epochPills = page.locator(".viewer-epoch-btn");
    await expect(epochPills.first()).toBeVisible({ timeout: 30_000 });

    // Toggle Compare mode
    const compareBtn = page.getByRole("button", { name: /^Compare$/i });
    if (await compareBtn.isVisible()) {
      await compareBtn.click();
      await expect(compareBtn).toHaveAttribute("aria-pressed", "true");
      // Return to blink
      await page.getByRole("button", { name: /^Blink$/i }).click();
    }
    expect(errors).toHaveLength(0);
  });

  test("Target 5: R Draconis Mira variable star live cutouts and header readout", async ({
    page,
  }) => {
    const errors = attachErrorCollector(page);
    await page.goto("/v?ra=248.16738&dec=66.75501&target=R+Draconis");

    await expect(page.locator(".viewer-presets")).toBeVisible({ timeout: 15_000 });
    const readout = page.locator(".viewer-sky-readout");
    await expect(readout).toBeVisible({ timeout: 15_000 });
    await expect(readout).toContainText("16h 32m");
    await expect(readout).toContainText("+66° 45′");

    const epochPills = page.locator(".viewer-epoch-btn");
    await expect(epochPills.first()).toBeVisible({ timeout: 30_000 });
    expect(errors).toHaveLength(0);
  });

  test("Cross-Navigation: Sequential Preset Chip Switching Across All 5 Benchmark Targets", async ({
    page,
  }) => {
    const errors = attachErrorCollector(page);
    await page.goto("/v");

    const targets = [
      { name: "Barnard's Star", match: "17h 57m" },
      { name: "Epsilon Indi B (Ba/Bb)", match: "22h 04m" },
      { name: "WISE J085510.83-071442.5", match: "08h 55m" },
      { name: "RW Aurigae", match: "05h 07m" },
      { name: "R Draconis", match: "16h 32m" },
    ];

    for (const t of targets) {
      const chip = page.getByRole("button", { name: t.name, exact: true });
      await expect(chip).toBeVisible({ timeout: 10_000 });
      await chip.click();

      // Check aria-pressed
      await expect(chip).toHaveAttribute("aria-pressed", "true");

      // Verify readout updates
      const readout = page.locator(".viewer-sky-readout");
      await expect(readout).toBeVisible({ timeout: 15_000 });
      await expect(readout).toContainText(t.match);

      // Verify target parameter is correctly reflected in URL
      const currentUrl = new URL(page.url());
      const targetParam = decodeURIComponent(currentUrl.searchParams.get("target") || "");
      expect(targetParam.toLowerCase()).toBe(t.name.toLowerCase());
    }

    // No page errors or uncaught console errors
    expect(errors).toHaveLength(0);
  });
});
