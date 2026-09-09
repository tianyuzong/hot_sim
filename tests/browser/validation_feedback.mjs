import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFile, spawn} from 'node:child_process';
import {promisify} from 'node:util';

const require = createRequire(process.env.THERMOFLOW_PLAYWRIGHT_PACKAGE || import.meta.url);
const {chromium} = require('playwright');
const python = process.env.THERMOFLOW_PYTHON || 'python';
const run = promisify(execFile);
const fixture = JSON.parse((await run(python, ['tests/browser/seed_validation.py'])).stdout);
const port = Number((await run(python, ['-c', "import socket\nwith socket.socket() as s:\n s.bind(('127.0.0.1',0))\n print(s.getsockname()[1])"])).stdout);
const url = `http://127.0.0.1:${port}`;
const server = spawn(python, ['-m', 'thermoflow'], {env: {...process.env,
  THERMOFLOW_DATA_DIR: fixture.data_dir, THERMOFLOW_PORT: String(port),
  THERMOFLOW_PLANNER: 'deterministic', THERMOFLOW_COMPUTE: 'cpu'}, stdio: 'ignore'});
let browser;
try {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(`${url}/health`)).ok) {ready = true; break;} } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert(ready);
  browser = await chromium.launch({headless: true, ...(process.env.THERMOFLOW_CHROMIUM
    ? {executablePath: process.env.THERMOFLOW_CHROMIUM} : {})});
  const page = await browser.newPage({viewport: {width: 1006, height: 883}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(f => localStorage.setItem('thermoflow.workspace.v1', JSON.stringify({
    selectedProjectId: f.project_id, selectedWorkpieceId: f.workpiece_id,
    selectedStudyId: f.study_id, activeTab: 'scenario', visualizationMode: 'model',
  })), fixture);
  const before = await (await fetch(`${url}/v1/studies/${fixture.study_id}`)).json();
  await page.goto(`${url}/docs`);
  await page.locator('#validationErrors .validation-issue').first().waitFor({timeout: 5000});
  assert.match(await page.locator('#validationErrors').innerText(), /7 K/);
  assert.match(await page.locator('#validationErrors').innerText(), /4 K/);
  assert.match(await page.locator('#validationErrors').innerText(), /删除|保留|移除/);
  assert.equal(await page.locator('#fixedBoundaryRows input[aria-invalid="true"]').count(), 2);
  await page.locator('#validationErrors button').first().click();
  assert(await page.locator('#fixedBoundaryRows select').first().evaluate(el => document.activeElement === el));
  const rect = await page.locator('#fixedBoundaryRows select').first().boundingBox();
  assert(rect.y > 0 && rect.y + rect.height < 883, 'Locate must scroll the offending field into view');
  await page.screenshot({path: 'docs/verification/validation-feedback-desktop.png', fullPage: true});
  await page.reload();
  await page.locator('#validationErrors .validation-issue').first().waitFor();
  assert.deepEqual((await (await fetch(`${url}/v1/studies/${fixture.study_id}`)).json()).plan, before.plan,
    'Showing validation must not change any user parameters');
  await page.locator('#materialsNav').click();
  await page.locator('#confirmMaterials').check();
  await page.locator('#scenarioNav').click();
  await page.locator('#confirmInputs').check();
  let submitted = 0;
  page.on('request', req => { if (/\/(confirm|tasks)$/.test(new URL(req.url()).pathname) && req.method() === 'POST') submitted++; });
  await page.locator('#primaryAction').click();
  assert.equal(submitted, 0, 'A known conflict must be shown locally before submitting a solve');
  const saved = page.waitForResponse(r => r.request().method() === 'PUT' && r.url().endsWith('/draft'));
  await page.locator('#fixedBoundaryRows select').nth(1).selectOption('face.xmax');
  await saved;
  await page.waitForFunction(() => document.querySelectorAll('#validationErrors .validation-issue').length === 0);
  assert.equal(await page.locator('#fixedBoundaryRows input[aria-invalid="true"]').count(), 0);
  assert(!(await page.locator('#validationWarnings').isHidden()), 'Material risk remains a warning, not a blocking conflict');
  const temperature = page.locator('#fixedBoundaryRows input').first();
  await temperature.fill('');
  await page.waitForFunction(() => document.querySelector('#fixedBoundaryRows input').getAttribute('aria-invalid') === 'true');
  assert.match(await page.locator('#validationErrors').innerText(), /必填|不能为空|填写/);
  const restored = page.waitForResponse(r => r.request().method() === 'PUT' && r.url().endsWith('/draft'));
  await temperature.fill('293.15');
  await restored;
  await page.waitForFunction(() => document.querySelector('#fixedBoundaryRows input').getAttribute('aria-invalid') !== 'true');
  const popup = page.waitForEvent('popup');
  await page.getByRole('link', {name: '使用说明', exact: true}).click();
  const help = await popup;
  await help.waitForLoadState();
  assert.match(help.url(), /\/assets\/help.html/);
  assert(await help.getByRole('heading', {name: /快速|入门/}).count());
  for (const width of [1006, 390]) {
    await help.setViewportSize({width, height: 883});
    assert(await help.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.setViewportSize({width, height: 883});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  }
  await page.screenshot({path: 'docs/verification/validation-feedback-mobile.png', fullPage: true});
  assert.deepEqual(errors, []);
  console.log('Validation persists, locates fields, updates after edits, prevents invalid submission and links to readable help.');
} finally {
  if (browser) await browser.close();
  server.kill('SIGTERM');
  await new Promise(resolve => {if (server.exitCode != null) resolve(); else server.once('exit', resolve);});
}
