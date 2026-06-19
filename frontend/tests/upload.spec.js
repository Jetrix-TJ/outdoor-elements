import { test, expect } from "@playwright/test";

const LANDSCAPE_PDF =
  "C:\\Users\\absar\\Downloads\\2811 Kirby\\2811 Kirby\\2811 Kirby - LANDSCAPE.pdf";

const EXPECTED_SHEETS = ["L1.01", "L1.02", "L1.04", "L5.01", "L5.02", "L5.03"];

test("upload selects the 6 required landscape pages", async ({ page }) => {
  await page.goto("/");

  // Dropzone is the initial state.
  await expect(page.getByText("Drop the drawing PDF here")).toBeVisible();

  // The file input is hidden; setInputFiles works on it directly.
  await page.locator('input[type="file"]').setInputFiles(LANDSCAPE_PDF);

  // Wait for Stage 1 to finish and the summary to render (selection ~27s).
  await expect(page.getByText("6 required")).toBeVisible({ timeout: 60_000 });

  // Heading reports the right kept count.
  await expect(
    page.getByRole("heading", { name: /Required pages \(6\)/ })
  ).toBeVisible();

  // All six expected sheet codes show up as cards.
  for (const sheet of EXPECTED_SHEETS) {
    await expect(page.locator(".card.keep .sheet", { hasText: sheet })).toBeVisible();
  }

  // At least one thumbnail actually loaded (decoded with real pixels).
  const firstThumb = page.locator(".card.keep img").first();
  await expect(firstThumb).toBeVisible();
  await expect
    .poll(async () => firstThumb.evaluate((img) => img.naturalWidth), { timeout: 20_000 })
    .toBeGreaterThan(0);

  // Continue is enabled once we have kept pages.
  await expect(page.getByRole("button", { name: /Continue/ })).toBeEnabled();
});

test("clicking a kept page opens an enlarged preview", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles(LANDSCAPE_PDF);
  await expect(page.getByText("6 required")).toBeVisible({ timeout: 60_000 });

  await page.locator(".card.keep").first().click();

  const dialogImg = page.locator(".lightbox-img img");
  await expect(dialogImg).toBeVisible();
  await expect
    .poll(async () => dialogImg.evaluate((img) => img.naturalWidth), { timeout: 20_000 })
    .toBeGreaterThan(0);

  // Close it.
  await page.locator(".lightbox .close").click();
  await expect(page.locator(".lightbox")).toHaveCount(0);
});

const QTO_PDF =
  "C:\\Users\\absar\\Downloads\\2811 Kirby\\2811 Kirby\\2811 KIRBY QTO.pdf";

test("Stage 2 detects & colors surface regions on a colored page", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  // QTO is a colored drawing; Stage 1 may keep 0 (no title block) but Stage 2
  // still runs on page 0 via Continue.
  await page.locator('input[type="file"]').first().setInputFiles(QTO_PDF);
  await expect(page.getByText(/required/)).toBeVisible({ timeout: 120_000 });

  await page.getByRole("button", { name: /Continue/ }).click();

  const overlay = page.locator(".s2img img");
  await expect(overlay).toBeVisible({ timeout: 120_000 });
  await expect
    .poll(async () => overlay.evaluate((img) => img.naturalWidth), { timeout: 120_000 })
    .toBeGreaterThan(0);

  // colored surfaces were detected -> a non-empty legend
  await expect.poll(async () => page.locator(".s2legend li").count(), { timeout: 10_000 })
    .toBeGreaterThan(0);
});

test("engine path: config → scale fix → L1.01 areas validated vs QTO", async ({ page }) => {
  test.setTimeout(360000);
  await page.goto("/");
  await page.locator('input[type="file"]').first().setInputFiles(LANDSCAPE_PDF);
  await page.getByText(/required/).waitFor({ timeout: 120000 });
  // Gemini auto-config panel appears; correct L1.01 scale 1/10 -> 1/16
  await page.locator(".cfg-table").waitFor({ timeout: 180000 });
  const scaleInput = page.locator(".cfg-table tbody tr").first().locator(".scale-in");
  await scaleInput.fill("16");
  await scaleInput.press("Enter");
  await page.waitForTimeout(800);

  await page.getByRole("button", { name: /Continue → Stage 2/ }).click();
  await page.locator(".s2img img").waitFor({ timeout: 180000 });
  await page.getByRole("button", { name: /Continue → Stage 3/ }).click();
  // validation verdict present and at least the M.5/M.7 pass
  await expect(page.locator(".verdict")).toContainText(/within 10%/, { timeout: 30000 });
  await expect(page.locator(".cmp3 td.ok")).not.toHaveCount(0);
});

test("rejects a non-PDF upload", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("not a pdf"),
  });
  await expect(page.locator(".error")).toContainText(/pdf/i);
});
