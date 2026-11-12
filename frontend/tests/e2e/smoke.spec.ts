import { expect, test, type Page } from "@playwright/test";

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

test.describe("SkyTrace T5a Smoke Suite", () => {
  test("Desktop (1440x900): Home, Sidebar toggle, Epoch switcher, Search, Auth flows", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const errors = attachErrorCollector(page);

    await page.goto("/");
    await expectNoHorizontalOverflow(page);

    // Core landmarks & single H1 & non-overlapping sticky header
    await expect(page.locator("header.site-header")).toBeVisible();
    await expect(page.locator("main#main-content")).toBeVisible();
    await expect(page.locator("footer.site-footer")).toBeVisible();
    await expect(page.locator("a.skip-link")).toHaveAttribute("href", "#main-content");
    await expect(page.locator("h1")).toHaveCount(1);

    const headerBox = await page.locator("header.site-header").boundingBox();
    const h1Box = await page.locator("h1").boundingBox();
    expect(headerBox).not.toBeNull();
    expect(h1Box).not.toBeNull();
    expect(headerBox!.y + headerBox!.height).toBeLessThanOrEqual(h1Box!.y + 1);

    // Desktop sidebar toggle
    const toggleBtn = page.getByRole("button", { name: "Toggle navigation sidebar" });
    const sidebar = page.locator("#skytrace-sidebar");
    await expect(sidebar).toBeVisible();
    await expect(toggleBtn).toHaveAttribute("aria-expanded", "true");
    await toggleBtn.click();
    await expect(sidebar).toBeHidden();
    await expect(toggleBtn).toHaveAttribute("aria-expanded", "false");
    await toggleBtn.click();
    await expect(sidebar).toBeVisible();

    // Multi-epoch switcher updates aria-live readout
    const e3Tab = page.getByRole("button", { name: /E3\s*2026-06-15/i });
    await e3Tab.click();
    await expect(e3Tab).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator("dl[aria-live='polite']")).toContainText("MJD 61206.1 TDB");

    // Coordinate search routes to /v with ra & dec query params
    await page.getByLabel(/Target name or ICRS coordinates/i).fill("133.795, -7.245");
    await page.getByRole("button", { name: /Inspect field/i }).click();
    await expect(page).toHaveURL(/\/v\?ra=133\.795&dec=-7\.245/);
    await expect(page.locator("h1")).toHaveCount(1);
    await expectNoHorizontalOverflow(page);

    // Auth flows: /login <-> /signin + validation + password reveal
    await page.goto("/login");
    await expectNoHorizontalOverflow(page);
    await expect(page.locator("h1")).toContainText("Sign in");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page).toHaveURL(/\/signin$/);
    await expect(page.locator("h1")).toContainText("Create an observatory account");

    // Submit empty -> accessible validation error
    await page.locator("form button[type='submit']").click();
    const nameInput = page.getByLabel(/Full name/i);
    await expect(nameInput).toHaveAttribute("aria-invalid", "true");
    await expect(page.locator("[role='status']")).toContainText(/fix the highlighted fields/i);

    // Password toggle works accessibly
    const pwInput = page.getByLabel(/^Password/i);
    const pwToggle = page.getByRole("button", { name: /Show password/i });
    await expect(pwInput).toHaveAttribute("type", "password");
    await pwToggle.click();
    await expect(pwInput).toHaveAttribute("type", "text");
    await expect(page.getByRole("button", { name: /Hide password/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    expect(errors).toEqual([]);
  });

  test("Mobile (390x844): No overflow, native <dialog> drawer opens/closes, Auth usable", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const errors = attachErrorCollector(page);

    await page.goto("/");
    await expectNoHorizontalOverflow(page);

    // Desktop sidebar is hidden on mobile; drawer opens via showModal()
    await expect(page.locator("#skytrace-sidebar")).toBeHidden();
    const toggleBtn = page.getByRole("button", { name: "Toggle navigation sidebar" });
    await toggleBtn.click();

    const drawer = page.locator("dialog#skytrace-drawer");
    await expect(drawer).toBeVisible();
    await expect(toggleBtn).toHaveAttribute("aria-expanded", "true");

    // Close via Escape key
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(toggleBtn).toHaveAttribute("aria-expanded", "false");

    // Reopen and navigate to /signin from inside drawer
    await toggleBtn.click();
    await expect(drawer).toBeVisible();
    await drawer.getByRole("link", { name: "Create account" }).click();
    await expect(page).toHaveURL(/\/signin$/);
    await expect(drawer).toBeHidden();
    await expectNoHorizontalOverflow(page);

    expect(errors).toEqual([]);
  });
});
