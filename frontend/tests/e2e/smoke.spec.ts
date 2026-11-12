import { expect, test, type Page } from "@playwright/test";

const SHOT_DIR =
  "/Users/macos/.gemini/antigravity/brain/36402ab4-81d4-4640-aba5-4b79e34a68f9/screenshots/skytrace";

function attachErrorCollector(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
  });
  return errors;
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function expectLoadedImages(page: Page) {
  await page.evaluate(() => {
    for (const img of document.images) img.scrollIntoView();
  });
  await page.waitForTimeout(300);
  const broken = await page.evaluate(() =>
    Array.from(document.images)
      .filter((img) => !img.complete || img.naturalWidth === 0)
      .map((img) => img.getAttribute("src")),
  );
  expect(broken).toEqual([]);
}

test.describe("SkyTrace Editorial Redesign Smoke Suite", () => {
  test("Desktop (1440x900): No desktop sidebar, Home, About, Login, Logout, Images", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const errors = attachErrorCollector(page);

    // 1. Home page
    await page.goto("/");
    await expectNoHorizontalOverflow(page);
    await expect(page.locator("header.site-header")).toBeVisible();
    await expect(page.locator("main#main-content")).toBeVisible();
    await expect(page.locator("footer.site-footer")).toBeVisible();
    await expect(page.locator("h1")).toHaveCount(1);

    // Confirm sidebar trigger & drawer are hidden on desktop
    await expect(page.getByRole("button", { name: "Open mobile navigation" })).toBeHidden();
    await expect(page.locator("dialog#mobile-nav-drawer")).toBeHidden();

    // Verify sticky header does not cover H1
    const headerBox = await page.locator("header.site-header").boundingBox();
    const h1Box = await page.locator("h1").boundingBox();
    expect(headerBox!.y + headerBox!.height).toBeLessThanOrEqual(h1Box!.y + 1);

    // Scroll down to trigger lazy images & capture desktop screenshots
    await page.screenshot({ path: `${SHOT_DIR}/home_top.png` });
    await page.locator(".archival-grid").scrollIntoViewIfNeeded();
    await page.waitForTimeout(350);
    await expectLoadedImages(page);
    await page.screenshot({ path: `${SHOT_DIR}/home_archival.png` });

    // Interactive epoch blink stage & mover/variable switch
    await page.getByRole("button", { name: "Variable Star (RW Aurigae)" }).click();
    await expect(page.locator("figcaption")).toContainText("Fixed coordinate");
    await page.getByRole("button", { name: "Next epoch" }).click();
    await expect(page.locator(".pager-count")).toContainText("02 / 03");
    await page.getByRole("heading", { name: "Calibration Fields" }).evaluate((el) => {
      const top = el.getBoundingClientRect().top + window.scrollY - 96;
      window.scrollTo({ top, behavior: "instant" });
    });
    const cardBox = await page.locator(".oat-card").first().boundingBox();
    const stoneBtnBox = await page.locator(".oat-card .btn-stone").first().boundingBox();
    expect(stoneBtnBox!.x + stoneBtnBox!.width).toBeLessThanOrEqual(
      cardBox!.x + cardBox!.width + 1,
    );
    await page.screenshot({ path: `${SHOT_DIR}/home_cards.png` });

    // Coordinate search routes to /v (stub API so preview server does not 404)
    await page.route("**/api/v1/**", (r) => r.fulfill({ json: {} }));
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByLabel(/Target name or ICRS coordinates/i).fill("133.795, -7.245");
    await page.getByRole("button", { name: /Inspect sky/i }).click();
    await expect(page).toHaveURL(/\/v\?ra=133\.795&dec=-7\.245/);

    // 2. About Us page & carousel
    await page.goto("/about");
    await page.unroute("**/api/v1/**");
    await expectNoHorizontalOverflow(page);
    await expect(page.locator("h1")).toContainText(/patience of time/i);
    await page.getByRole("button", { name: "Next chapter" }).click();
    await expect(page.locator(".pager-count")).toContainText("02 / 03");
    await page.screenshot({ path: `${SHOT_DIR}/about.png` });

    // 3. Log in -> Signed-in header -> Log out flow
    await page.goto("/login");
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: `${SHOT_DIR}/login.png` });

    // Trigger validation first
    await page.locator("form button[type='submit']").click();
    await expect(page.getByLabel(/Email address/i)).toHaveAttribute("aria-invalid", "true");

    // Fill & submit login
    await page.getByLabel(/Email address/i).fill("vera.rubin@carnegie.edu");
    await page.getByLabel(/^Password/i).fill("observatory1970");
    await page.locator("form button[type='submit']").click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator(".user-chip")).toContainText("vera.rubin");

    // Navigate to Log out
    await page.getByRole("link", { name: "Log out" }).first().click();
    await expect(page).toHaveURL(/\/logout$/);
    await expect(page.locator("[role='status']")).toContainText(/Signed out/i);
    await page.screenshot({ path: `${SHOT_DIR}/logout.png` });

    expect(errors).toEqual([]);
  });

  test("Mobile (390x844): Mobile-only <dialog> drawer works, zero horizontal overflow", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const errors = attachErrorCollector(page);

    await page.goto("/");
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: `${SHOT_DIR}/mobile_home.png` });

    const menuBtn = page.getByRole("button", { name: "Open mobile navigation" });
    await expect(menuBtn).toBeVisible();
    const btnBox = await menuBtn.boundingBox();
    const svgBox = await menuBtn.locator("svg").boundingBox();
    expect(Math.abs(svgBox!.x + svgBox!.width / 2 - (btnBox!.x + btnBox!.width / 2))).toBeLessThan(
      2,
    );
    expect(
      Math.abs(svgBox!.y + svgBox!.height / 2 - (btnBox!.y + btnBox!.height / 2)),
    ).toBeLessThan(2);
    await menuBtn.click();

    const drawer = page.locator("dialog#mobile-nav-drawer");
    await expect(drawer).toBeVisible();
    await page.screenshot({ path: `${SHOT_DIR}/mobile_drawer.png` });

    // Navigate to About from mobile drawer
    await drawer.getByRole("link", { name: "About" }).click();
    await expect(page).toHaveURL(/\/about$/);
    await expect(drawer).toBeHidden();
    await expectNoHorizontalOverflow(page);

    expect(errors).toEqual([]);
  });
});
