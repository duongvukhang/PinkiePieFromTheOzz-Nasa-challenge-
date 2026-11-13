import { expect, test, type Page, type Route } from "@playwright/test";

type FulfillBody = NonNullable<Parameters<Route["fulfill"]>[0]>["body"];
declare const Buffer: { from(ab: ArrayBuffer): FulfillBody };

const SHOT_DIR =
  "/Users/macos/.gemini/antigravity/brain/36402ab4-81d4-4640-aba5-4b79e34a68f9/screenshots/skytrace";

const WISE_RA = 133.79513;
const WISE_DEC = -7.24515;
const CUTOUT_ID = "00000000-0000-0000-0000-000000000855";

function makeSyntheticTile(moverX: number, moverY: number): FulfillBody {
  const w = 64;
  const h = 64;
  const arr = new Float32Array(w * h);
  const stars: ReadonlyArray<[number, number, number]> = [
    [18, 20, 45],
    [48, 44, 65],
    [moverX, moverY, 90],
  ];
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let val = 10 + (Math.sin(x * 1.9 + y * 2.7) + 1) * 0.35;
      for (const [sx, sy, amp] of stars) {
        const d2 = (x - sx) ** 2 + (y - sy) ** 2;
        val += amp * Math.exp(-d2 / 4.2);
      }
      arr[y * w + x] = val;
    }
  }
  return Buffer.from(arr.buffer);
}

const WCS_HDR = [
  "NAXIS1  = 64",
  "NAXIS2  = 64",
  `CRVAL1  = ${WISE_RA}`,
  `CRVAL2  = ${WISE_DEC}`,
  "CRPIX1  = 32.5",
  "CRPIX2  = 32.5",
  "CD1_1   = -0.002604",
  "CD2_2   = 0.002604",
].join("\n");

async function mockWise0855Api(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;

    if (path === "/api/v1/resolve") {
      return route.fulfill({
        json: {
          query: url.searchParams.get("name") ?? "WISE 0855",
          ra_deg: WISE_RA,
          dec_deg: WISE_DEC,
          pmra_masyr: -8123.7,
          pmdec_masyr: 673.2,
          resolver: "simbad",
        },
      });
    }
    if (path === "/api/v1/examples") {
      return route.fulfill({
        json: {
          targets: [
            {
              id: "wise_0855",
              name: "WISE 0855",
              category: "brown_dwarf",
              ra_deg: WISE_RA,
              dec_deg: WISE_DEC,
              fov_arcmin: 8,
              passed: true,
            },
            {
              id: "barnards_star",
              name: "Barnard's Star",
              category: "star_pm",
              ra_deg: 269.4485,
              dec_deg: 4.7394,
              fov_arcmin: 10,
              passed: true,
            },
            {
              id: "rw_aur",
              name: "RW Aurigae",
              category: "variable_star",
              ra_deg: 76.9561,
              dec_deg: 30.4012,
              fov_arcmin: 10,
              passed: true,
            },
          ],
        },
      });
    }
    if (path === "/api/v1/releases") {
      return route.fulfill({
        json: [
          {
            id: "spherex_qr_2026-10",
            provider: "spherex_irsa",
            mission: "SPHEREx",
            version_label: "QR 2026-10",
            status: "active",
          },
        ],
      });
    }
    if (path === "/api/v1/fields/frames") {
      return route.fulfill({
        json: {
          field: { ra: WISE_RA, dec: WISE_DEC, fov_arcmin: 10, healpix9: 123456 },
          release: "spherex_qr_2026-10",
          epochs: [
            {
              epoch: "S1",
              mjd_tdb_mean: 60850.41,
              utc_mean: "2025-06-21T09:50:24Z",
              n_frames: 12,
              arrays: [5, 6],
              lambda_um: [3.82, 5.0],
              flagged_fraction: 0.02,
            },
            {
              epoch: "S2",
              mjd_tdb_mean: 61031.77,
              utc_mean: "2025-12-19T18:28:48Z",
              n_frames: 12,
              arrays: [5, 6],
              lambda_um: [3.82, 5.0],
              flagged_fraction: 0.03,
            },
            {
              epoch: "S3",
              mjd_tdb_mean: 61215.1,
              utc_mean: "2026-06-20T02:00:00Z",
              n_frames: 12,
              arrays: [5, 6],
              lambda_um: [3.82, 5.0],
              flagged_fraction: 0.01,
            },
          ],
          warnings: [],
        },
      });
    }
    if (path === "/api/v1/cutouts" && req.method() === "POST") {
      return route.fulfill({
        status: 202,
        json: {
          job_id: "00000000-0000-0000-0000-000000000010",
          status: "succeeded",
          cutout_id: CUTOUT_ID,
        },
      });
    }
    if (path === `/api/v1/cutouts/${CUTOUT_ID}`) {
      return route.fulfill({
        json: {
          cutout_id: CUTOUT_ID,
          field: { ra: WISE_RA, dec: WISE_DEC, fov_arcmin: 10, healpix9: 123456 },
          release: "spherex_qr_2026-10",
          warnings: [],
          frames: [
            {
              frame_id: "f1",
              epoch: "S1",
              mjd_tdb_mid: 60850.41,
              utc_mid: "2025-06-21T09:50:24Z",
              array_id: 6,
              band_label: "arr6",
              display_url: "/display1.webp",
              science_url: `/api/v1/cutouts/${CUTOUT_ID}/frames/f1/science.bin`,
              fits_url: "/f1.fits",
              wcs_header: WCS_HDR,
              flagged_fraction: 0.021,
            },
            {
              frame_id: "f2",
              epoch: "S2",
              mjd_tdb_mid: 61031.77,
              utc_mid: "2025-12-19T18:28:48Z",
              array_id: 6,
              band_label: "arr6",
              display_url: "/display2.webp",
              science_url: `/api/v1/cutouts/${CUTOUT_ID}/frames/f2/science.bin`,
              fits_url: "/f2.fits",
              wcs_header: WCS_HDR,
              flagged_fraction: 0.034,
            },
            {
              frame_id: "f3",
              epoch: "S3",
              mjd_tdb_mid: 61215.1,
              utc_mid: "2026-06-20T02:00:00Z",
              array_id: 6,
              band_label: "arr6",
              display_url: "/display3.webp",
              science_url: `/api/v1/cutouts/${CUTOUT_ID}/frames/f3/science.bin`,
              fits_url: "/f3.fits",
              wcs_header: WCS_HDR,
              flagged_fraction: 0.015,
            },
          ],
        },
      });
    }
    if (path.endsWith("/science.bin")) {
      const idx = path.includes("/f1/") ? 0 : path.includes("/f2/") ? 1 : 2;
      return route.fulfill({
        status: 200,
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Dtype": "float32",
          "X-Shape": "64,64",
        },
        body: makeSyntheticTile(36 - idx * 4, 32),
      });
    }
    if (path === "/api/v1/catalogs/overlay") {
      const cat = url.searchParams.get("cat") ?? "gaia";
      return route.fulfill({
        json: {
          catalog: cat,
          epoch_mjd_tdb: 60850.41,
          warnings: [],
          sources: [
            {
              catalog: cat,
              catalog_id: `${cat}-anchor-1`,
              ra: WISE_RA + 0.03,
              dec: WISE_DEC - 0.03,
              epoch_mjd_tdb: 60850.41,
              mag: 14.2,
            },
            {
              catalog: cat,
              catalog_id: `${cat}-wise0855`,
              ra: WISE_RA,
              dec: WISE_DEC,
              epoch_mjd_tdb: 60850.41,
              mag: 13.7,
            },
          ],
        },
      });
    }
    if (path === "/api/v1/analyses" && req.method() === "POST") {
      return route.fulfill({
        status: 202,
        json: {
          job_id: "00000000-0000-0000-0000-000000000020",
          status: "succeeded",
          analysis_id: "00000000-0000-0000-0000-000000000099",
        },
      });
    }
    if (path.endsWith("/sources")) {
      return route.fulfill({
        json: {
          items: [
            { id: 88801, fits_file_id: CUTOUT_ID, ra: WISE_RA, dec: WISE_DEC, flags: 2, snr: 18.5 },
          ],
          next_cursor: null,
        },
      });
    }
    if (path.endsWith("/suggestions")) {
      return route.fulfill({
        json: {
          analysis_id: "00000000-0000-0000-0000-000000000099",
          suggestions: [
            {
              detection_id: 88801,
              ra: WISE_RA,
              dec: WISE_DEC,
              score: 0.94,
              reasons: ["High PM candidate", "4.5 um excess"],
            },
          ],
        },
      });
    }
    if (path === "/api/v1/objects/88801") {
      return route.fulfill({
        json: {
          id: "00000000-0000-0000-0000-000000088801",
          label: "A",
          public_id: "J085510.8-071442",
          status: "active",
          identity_confidence: 0.97,
          measurements: [
            {
              epoch: "S1",
              source: "detection",
              mjd_tdb: 60850.41,
              ra: WISE_RA,
              dec: WISE_DEC,
              sig_ra_mas: 45,
              sig_dec_mas: 40,
              flags: 2,
            },
            {
              epoch: "S2",
              source: "detection",
              mjd_tdb: 61031.77,
              ra: WISE_RA - 0.003,
              dec: WISE_DEC + 0.001,
              sig_ra_mas: 55,
              sig_dec_mas: 50,
              flags: 0,
            },
            {
              epoch: "S3",
              source: "detection",
              mjd_tdb: 61215.1,
              ra: WISE_RA - 0.006,
              dec: WISE_DEC + 0.002,
              sig_ra_mas: 65,
              sig_dec_mas: 60,
              flags: 128,
            },
          ],
          motion_fit: {
            model: "linear",
            ref_epoch_mjd_tdb: 60941.0,
            params: {},
            pm_total_masyr: 8120,
            pm_total_err: 420,
            pa_deg: 274.9,
            chi2_red: 0.9,
            is_preferred: true,
          },
          classification: {
            top_class: "brown_dwarf",
            top_prob: 0.83,
            probabilities: { brown_dwarf: 0.83 },
            evidence: ["mu = 8.1 arcsec/yr", "W1 - W2 > 2.5"],
          },
          caveats: ["Low optical flux; blended near faint neighbor at S2"],
        },
      });
    }
    if (path.includes("/associations") && req.method() === "PATCH") {
      return route.fulfill({
        json: {
          object_id: "00000000-0000-0000-0000-000000088801",
          epoch: "S2",
          action: "accepted",
          assigned_detection_id: 88802,
          override_by: "current_user",
          updated_at: "2026-11-13T10:00:00Z",
        },
      });
    }
    if (path.includes("/associations")) {
      return route.fulfill({
        json: {
          object_id: "00000000-0000-0000-0000-000000088801",
          associations: [
            {
              id: 101,
              observation_id: "00000000-0000-0000-0000-000000000001",
              detection_id: 88801,
              pred_ra: WISE_RA,
              pred_dec: WISE_DEC,
              confidence: 0.98,
              rank: 1,
              is_chosen: true,
              decided_by: "auto",
              reason: "Global gate within 0.05 arcsec",
              flags: 0,
            },
            {
              id: 102,
              observation_id: "00000000-0000-0000-0000-000000000002",
              detection_id: 88802,
              pred_ra: WISE_RA - 0.003,
              pred_dec: WISE_DEC + 0.001,
              confidence: 0.92,
              rank: 1,
              is_chosen: true,
              decided_by: "auto",
              reason: "Linear motion extrapolation match",
              flags: 0,
            },
          ],
        },
      });
    }
    if (path === "/api/v1/objects/88801/spectrum") {
      const lam = Array.from({ length: 24 }, (_, i) => 0.9 + i * 0.17);
      const flux = lam.map((l) => (l > 4.2 ? 38 + (l - 4.2) * 20 : 2 + l));
      return route.fulfill({
        json: {
          object_id: "00000000-0000-0000-0000-000000088801",
          wavelength_um: lam,
          flux_ujy: flux,
          flux_err_ujy: lam.map(() => 2.1),
          mjd_tdb: lam.map(() => 60850.41),
          flags: lam.map((_, i) => (i === 5 ? 2 : 0)),
          method: "forced_psf",
        },
      });
    }
    return route.fulfill({ status: 404, json: { title: "Unmocked", status: 404 } });
  });
}

test.describe("SkyTrace Viewer /v & /embed (WISE 0855 mocked API)", () => {
  test("Desktop (1440x900): Features A, B, C, D + WebGL + Embed", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockWise0855Api(page);

    // 1. Navigate to /v?target=WISE%200855
    const binDone = page.waitForResponse((r) => r.url().includes("/science.bin") && r.ok());
    await page.goto("/v?target=WISE%200855");
    await binDone;
    await expect(page.locator("canvas.viewer-gl-canvas")).toBeVisible();
    await page.waitForTimeout(80);
    await expect(page.locator(".viewer-frame-badge")).toContainText("S1");
    await expect(page.locator(".spectrum-svg .sed-point")).toHaveCount(24);
    await page.screenshot({ path: `${SHOT_DIR}/viewer_wise0855_blink.png` });

    // 2. Feature A: Toggle Gaia + CatWISE catalog overlays & change FOV -> verify URL sync
    await page.getByRole("button", { name: /Gaia DR3/i }).click();
    await page.getByRole("button", { name: /CatWISE2020/i }).click();
    await page.getByLabel("FOV").selectOption("8");
    await expect(page).toHaveURL(/overlay=gaia%2Ccatwise/);
    await expect(page).toHaveURL(/fov=8/);
    await expect(page.locator("svg.viewer-layer-svg polygon")).toHaveCount(2);

    // 3. Feature B: Step frames, switch array arr6, and enable A - B WebGL diff
    await page.getByRole("button", { name: "Next frame" }).click();
    await expect(page.locator(".viewer-frame-badge")).toContainText("S2");
    await page.getByRole("button", { name: /Arr 6/i }).click();
    await page.getByRole("button", { name: /A − B/i }).click();
    await expect(page).toHaveURL(/bands=arr6/);
    await expect(page).toHaveURL(/op=diff/);
    await page.screenshot({ path: `${SHOT_DIR}/viewer_wise0855_diff.png` });

    // 4. Feature C: Switch Inspector Tabs (Metadata & Flags + Object & Sources)
    await page.getByRole("tab", { name: "Metadata & Flags" }).click();
    await expect(page.locator(".meta-panel")).toContainText("60850.41");
    await expect(
      page.locator(".flag-row.is-active").filter({ hasText: "BLENDED" }),
    ).toBeVisible();
    await page.screenshot({ path: `${SHOT_DIR}/viewer_wise0855_flags.png` });

    await page.getByRole("tab", { name: "Object & Sources" }).click();
    await expect(page.locator(".object-dossier")).toContainText("8120.0");
    await expect(page.locator(".object-dossier")).toContainText("unconfirmed");

    // 5. Feature D: Chrome-less /embed view
    await page.goto("/embed?target=WISE%200855&fov=8&overlay=gaia,catwise");
    await expect(page.locator("header.site-header")).toHaveCount(0);
    await expect(page.locator("footer.site-footer")).toHaveCount(0);
    await expect(page.locator("canvas.viewer-gl-canvas")).toBeVisible();
    await page.screenshot({ path: `${SHOT_DIR}/embed_wise0855.png` });

    expect(errors).toEqual([]);
  });

  test("Mobile (390x844): Viewer layout has zero horizontal overflow", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width: 390, height: 844 });
    await mockWise0855Api(page);

    const binDone = page.waitForResponse((r) => r.url().includes("/science.bin") && r.ok());
    await page.goto("/v?target=WISE%200855");
    await binDone;
    await expect(page.locator("canvas.viewer-gl-canvas")).toBeVisible();
    await page.waitForTimeout(80);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `${SHOT_DIR}/viewer_mobile.png` });
    expect(errors).toEqual([]);
  });

  test("Task 5c: Features A, B, C (Selection, Tracked IDs, Trajectory overlay, Associations)", async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockWise0855Api(page);

    // Load viewer with obj=88801 and track=A:133.79513,-7.24515
    const binDone = page.waitForResponse((r) => r.url().includes("/science.bin") && r.ok());
    await page.goto(
      "/v?target=WISE%200855&track=A:133.79513,-7.24515&obj=88801&inspectorTab=object",
    );
    await binDone;
    await expect(page.locator("canvas.viewer-gl-canvas")).toBeVisible();

    // Ensure Object & Sources tab is active
    await page.getByRole("tab", { name: "Object & Sources" }).click();

    // Feature B: Tracked objects list with persistent ID A
    await expect(page.locator(".tracked-objects-bar")).toBeVisible();
    await expect(page.locator(".tracked-object-pill")).toContainText("A");

    // Feature C: Trajectory overlay with polyline, error ellipses and PM vector
    await expect(page.locator("polyline.trajectory-polyline")).toBeVisible();
    await expect(page.locator("ellipse.error-ellipse")).toHaveCount(3);
    await expect(page.locator(".pm-arrow")).toBeVisible();

    // Feature C: Motion fit + Unconfirmed hypothesis caveat
    await expect(page.locator(".object-dossier")).toContainText("8120.0");
    await expect(page.locator(".unconfirmed-banner")).toContainText("unconfirmed hypotheses");

    // Feature B: Association hypothesis inspector with Accept/Reject buttons
    await expect(page.locator(".associations-section")).toBeVisible();
    const acceptBtn = page.getByRole("button", { name: /Accept match for epoch S2/i });
    await expect(acceptBtn).toBeVisible();

    // Click Accept -> calls PATCH /api/v1/objects/88801/associations/S2
    const patchDone = page.waitForResponse(
      (r) => r.url().includes("/associations/S2") && r.request().method() === "PATCH",
    );
    await acceptBtn.click();
    await patchDone;

    // Feature A: Box-Select Object toggle activates pointer events on SVG
    const boxSelectBtn = page.getByRole("button", { name: "Box-Select Object" });
    await boxSelectBtn.click();
    await expect(boxSelectBtn).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".box-select-active")).toBeVisible();

    await page.screenshot({ path: `${SHOT_DIR}/viewer_trajectory_overlay.png` });
    expect(errors).toEqual([]);
  });

  test("Task 5c: Feature D (Compare view A-vs-B with header diff table & source change filtering)", async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockWise0855Api(page);

    const binDone = page.waitForResponse((r) => r.url().includes("/science.bin") && r.ok());
    await page.goto("/v?target=WISE%200855&mode=compare&op=diff&a=f1&b=f2");
    await binDone;

    // CompareView rendered
    await expect(page.locator(".compare-view")).toBeVisible();
    await expect(page.locator(".compare-toolbar")).toBeVisible();

    // Responsive Header Diff Table (SCIENCE.md §J.3)
    await expect(page.locator(".compare-header-table")).toBeVisible();
    await expect(page.locator(".compare-header-table")).toContainText("DATE-OBS");
    await expect(page.locator(".compare-header-table .diff-pill").first()).toBeVisible();

    // Source Change Classification Filter (SCIENCE.md §J.5)
    await expect(page.locator(".compare-sources-panel")).toBeVisible();
    const varFilter = page.getByRole("button", { name: "Variable (5σ)" });
    await varFilter.click();
    await expect(varFilter).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".compare-sources-table tbody tr")).toHaveCount(1);
    await expect(page.locator(".compare-sources-table")).toContainText("src-90102");

    await page.screenshot({ path: `${SHOT_DIR}/viewer_compare_view.png` });
    expect(errors).toEqual([]);
  });

  test("Task 5c: Feature E (Planet X wizard UI with rate/distance grid & candidate gallery)", async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockWise0855Api(page);

    await page.goto("/v?target=WISE%200855&mode=planetx");
    await expect(page.locator(".planetx-wizard")).toBeVisible();

    // Mandatory Unconfirmed Hypothesis Banner (SCIENCE.md §I.3 & AGENTS.md §7)
    await expect(page.locator(".planetx-mandatory-banner")).toContainText(
      "CANDIDATES ARE UNCONFIRMED HYPOTHESES",
    );

    // Step 2: Rate & Distance Grid
    await page.getByRole("tab", { name: "2. Rate & Distance Grid" }).click();
    await expect(page.locator(".wizard-panel")).toContainText(/Parallactic [Jj]ump/);

    // Step 3: Shift-and-Stack Execution
    await page.getByRole("tab", { name: "3. Digital Tracking Stack" }).click();
    const launchBtn = page.getByRole("button", { name: /Run Digital Tracking Stack/i });
    await launchBtn.click();

    // Step 4: Candidate Gallery automatically opened on completion
    await expect(page.locator(".candidate-card").first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator(".candidate-card")).toHaveCount(1);
    await expect(page.locator(".candidate-card").first()).toContainText("PX-CAND-01");
    await expect(page.locator(".candidate-card").first()).toContainText("Unconfirmed Hypothesis");

    await page.screenshot({ path: `${SHOT_DIR}/viewer_planetx_wizard.png` });
    expect(errors).toEqual([]);
  });
});
