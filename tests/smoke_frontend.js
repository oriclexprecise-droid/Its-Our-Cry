const { chromium } = require('playwright');

// Core smoke test: project type / AI mode selection + client-generation prompts (SRT & WebGaL)
// Usage: BASE_URL=http://127.0.0.1:5123 node tests/smoke_frontend.js
const BASE = process.env.BASE_URL || 'http://127.0.0.1:5123';
const results = [];
let failures = 0;

function check(name, ok, detail) {
  results.push((ok ? 'PASS' : 'FAIL') + ' ' + name + (detail ? ' | ' + detail : ''));
  if (!ok) failures++;
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage();
  page.on('pageerror', e => check('page has no JS error', false, e.message));
  try {
    await page.goto(BASE, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#btn-new-project', { timeout: 15000 });

    // SRT workbench + client generation
    await page.click('#btn-new-project');
    await page.waitForSelector('#new-project-modal:not(.hidden)', { timeout: 5000 });
    await page.fill('#new-project-name', 'smoke-srt-' + Date.now());
    await page.locator('.project-type-card').first().locator('.project-ai-mode', { hasText: '\u5ba2\u6237\u7aef\u751f\u6210' }).click();
    const srtType = await page.evaluate(() => document.querySelector('input[name="new-project-type"]:checked').value);
    check('SRT card type', srtType === 'srt', 'type=' + srtType);
    await page.click('#btn-new-project-ok');
    await page.waitForSelector('#view-workbench:not(.hidden)', { timeout: 6000 });
    await page.fill('#script-input', '\u5343\u65e9\u7231\u97f3\uff1a\u4f60\u597d\u554a\n\u957f\u5d0e\u7d20\u4e16\uff1a\u65e9\u4e0a\u597d');
    const srtBtn = (await page.textContent('#btn-analyze')).trim();
    check('SRT button label', srtBtn === '\u5ba2\u6237\u7aef\u751f\u6210', srtBtn);
    await page.click('#btn-analyze');
    await page.waitForSelector('#client-ai-box:not(.hidden)', { timeout: 10000 });
    await page.waitForFunction(() => document.getElementById('client-prompt-output').value.length > 0, null, { timeout: 10000 });
    check('SRT prompt generated', true, 'len=' + (await page.inputValue('#client-prompt-output')).length);

    // WebGaL + client generation
    await page.goto(BASE, { waitUntil: 'domcontentloaded' });
    await page.click('#btn-new-project');
    await page.waitForSelector('#new-project-modal:not(.hidden)', { timeout: 5000 });
    await page.fill('#new-project-name', 'smoke-wg-' + Date.now());
    await page.locator('.project-type-card', { hasText: 'WebGaL' }).locator('.project-ai-mode', { hasText: '\u5ba2\u6237\u7aef\u751f\u6210' }).click();
    const wgType = await page.evaluate(() => document.querySelector('input[name="new-project-type"]:checked').value);
    check('WebGaL card type', wgType === 'webgal', 'type=' + wgType);
    await page.click('#btn-new-project-ok');
    await page.waitForSelector('#view-webgal:not(.hidden)', { timeout: 6000 });
    await page.fill('#webgal-input', 'anon: hello -id -figureId=anon;');
    await page.click('#btn-webgal-parse');
    await page.waitForSelector('#webgal-settings:not(.hidden)', { timeout: 10000 });
    await page.click('#btn-webgal-analyze');
    await page.waitForSelector('#wg-client-ai-box:not(.hidden)', { timeout: 10000 });
    await page.waitForFunction(() => document.getElementById('wg-client-prompt-output').value.length > 0, null, { timeout: 10000 });
    check('WebGaL prompt generated', true, 'len=' + (await page.inputValue('#wg-client-prompt-output')).length);
  } catch (e) {
    check('flow execution', false, (e && e.message) || String(e));
  } finally {
    await browser.close();
  }
  results.forEach(r => console.log(r));
  console.log(failures ? 'SMOKE FAIL: ' + failures + ' failed' : 'SMOKE PASS');
  process.exit(failures ? 1 : 0);
})();
