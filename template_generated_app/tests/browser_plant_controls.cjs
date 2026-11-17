/* Optional real-browser smoke test; the Python suite does not require Playwright.
 * Start the app, install/use Playwright, then run:
 *   EMS_APP_URL=http://localhost:8502 node tests/browser_plant_controls.cjs
 * Optional: PLAYWRIGHT_MODULE, EMS_BROWSER_EXECUTABLE, EMS_BROWSER_CDP_URL,
 * EMS_EXPECTED_REVISION, EMS_SCREENSHOT_DIR. CDP must use a dedicated test browser.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const url = process.env.EMS_APP_URL || 'http://localhost:8502';
const revision = process.env.EMS_EXPECTED_REVISION || 'plant-table-v5';
const npp = 'Maximum net production (g C/m²/day)';
const lai = 'Maximum leaf area index (m²/m²)';
const tableLabel = 'Plant species with sortable name, functional group, model preset and source ID';

async function ready(page) {
  await page.waitForFunction(() => document.querySelector('[data-testid="stApp"]')?.getAttribute('data-test-script-state') === 'notRunning');
}

async function select(page, label, option) {
  await ready(page);
  await page.getByLabel(label, { exact: true }).click();
  await page.getByLabel(label, { exact: true }).press('ArrowDown');
  await page.getByRole('option', { name: option, exact: typeof option === 'string' }).click();
}

async function value(page, label, expected) {
  await page.waitForFunction(({ label, expected }) => {
    const input = [...document.querySelectorAll('input')].find(el => el.getAttribute('aria-label') === label);
    return input && input.value === expected;
  }, { label, expected: String(expected) });
}

async function expand(page, name) {
  const text = page.getByText(name, { exact: true });
  if (!(await text.evaluate(el => Boolean(el.closest('details')?.open)))) await text.click();
}

async function advanced(page) {
  await page.getByRole('tab', { name: 'Advanced', exact: true }).click();
  await expand(page, 'Plants');
  await page.getByLabel('Filter group / ecosystem', { exact: true }).waitFor();
  await ready(page);
}

async function currentRevision(page) {
  await ready(page);
  await page.getByRole('tab', { name: 'Data', exact: true }).click();
  await ready(page);
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download source report', exact: true }).click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  assert.ok(stream, 'Source report download is readable');
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const report = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  assert.equal(report.application.application_revision, revision);
  await ready(page);
}

async function canvas(page) {
  const table = page.getByRole('region', { name: tableLabel, exact: true });
  await table.scrollIntoViewIfNeeded();
  const result = table.locator('canvas[data-testid="data-grid-canvas"]');
  await result.waitFor();
  return result;
}

async function main() {
  const browser = process.env.EMS_BROWSER_CDP_URL
    ? await chromium.connectOverCDP(process.env.EMS_BROWSER_CDP_URL)
    : await chromium.launch({ headless: true, executablePath: process.env.EMS_BROWSER_EXECUTABLE });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    const started = Date.now();
    await page.goto(url);
    await currentRevision(page);
    await advanced(page);
    await page.getByText(/^66,042 plants · Click a column to sort\./).waitFor();
    await canvas(page);
    console.log(`PASS: current revision and complete native table rendered in ${Date.now() - started} ms`);

    const searched = Date.now();
    await page.getByLabel('Search plant species', { exact: true }).fill('Quercus robur');
    await page.getByText(/^1 plants · Click a column to sort\./).waitFor();
    console.log(`PASS: real-browser search settled in ${Date.now() - searched} ms (including debounce)`);
    const grid = await canvas(page);
    // Glide Data Grid draws its row selectors on canvas: header 36 px, row 35 px.
    // These are real pointer events, not injection into Streamlit's session state.
    await grid.click({ position: { x: 90, y: 18 }, force: true });
    await grid.click({ position: { x: 17, y: 53 }, force: true });
    await page.getByRole('button', { name: 'Use selected plant', exact: true }).click();
    await page.getByText(/^Applied plant: Quercus robur · /).waitFor();
    await value(page, 'Ecosystem', 'Forest');
    await value(page, npp, 6);
    await value(page, lai, 6);
    console.log('PASS: native header/row selection and explicit Apply use the real species preset');

    await page.getByLabel(npp, { exact: true }).fill('11');
    await page.getByLabel(npp, { exact: true }).press('Enter');
    await page.getByRole('tab', { name: 'Model', exact: true }).click();
    await advanced(page);
    await select(page, 'Filter group / ecosystem', 'C4 grassland');
    await page.getByText(/^0 plants · Click a column to sort\./).waitFor();
    await select(page, 'Sort / group', 'Group, then name');
    await value(page, npp, 11);
    await value(page, 'Ecosystem', 'Forest');
    await page.getByText(/^Applied plant: Quercus robur · /).waitFor();
    console.log('PASS: filtering and grouping do not silently alter the applied species or manual parameters');

    await select(page, 'Ecosystem', 'Crop');
    await value(page, npp, 8);
    await value(page, lai, 5);
    await page.getByText('Active plant preset: Crop', { exact: true }).waitFor();
    await page.getByText(/^Applied plant: Quercus robur · /).waitFor({ state: 'hidden' });
    await page.getByRole('tab', { name: 'Model', exact: true }).click();
    console.log('PASS: explicit ecosystem change clears species and updates plant parameters');

    await page.reload();
    await currentRevision(page);
    await advanced(page);
    await select(page, 'Ecosystem', 'Forest');
    await value(page, npp, 6);
    await value(page, lai, 6);
    await select(page, 'Ecosystem', 'C4 grassland');
    await value(page, npp, 6);
    await value(page, lai, 4);
    await value(page, 'Optimum growth temperature (°C)', 30);
    console.log('PASS: reloaded session serves the current module and applies presets');
    assert.equal(await page.locator('[data-testid="stException"]').count(), 0);
    assert.deepEqual(errors, []);
    if (process.env.EMS_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.EMS_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.EMS_SCREENSHOT_DIR, 'plant-table-current.png'), fullPage: true });
    }
    console.log(`PASS: real-browser plant controls at ${url}`);
  } catch (error) {
    if (process.env.EMS_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.EMS_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.EMS_SCREENSHOT_DIR, 'plant-table-failure.png'), fullPage: true });
    }
    throw error;
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
