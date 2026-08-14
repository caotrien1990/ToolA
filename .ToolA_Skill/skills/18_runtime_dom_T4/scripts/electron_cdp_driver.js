// T4 Electron CDP driver — drive Rawcut [E:U] to retrieve footage.
//
// Prerequisites:
//   - main.js has app.commandLine.appendSwitch('remote-debugging-port', '9223')
//   - Electron launched: `cd <Rawcut>/Source && npx electron .`
//   - playwright npm package installed somewhere in PATH or local
//
// Run:
//   node electron_cdp_driver.js \
//     <sheet_url> <output_folder_absolute>
//
// Example:
//   node electron_cdp_driver.js \
//     "https://docs.google.com/spreadsheets/d/15Te2WS6.../edit?gid=0#gid=0" \
//     "D:\\0. TRIEN\\2. PROJECT\\393. H. 599\\1. FOOTAGE\\New folder\\TEST ELECTRON_UI"

const path = require('path');

async function main() {
  const sheetUrl = process.argv[2];
  const outputFolder = process.argv[3];
  if (!sheetUrl || !outputFolder) {
    console.error('Usage: node electron_cdp_driver.js <sheet_url> <output_folder>');
    process.exit(2);
  }

  let chromium;
  try {
    ({ chromium } = require('playwright'));
  } catch (e) {
    console.error('playwright package not installed. Run: npm i playwright');
    process.exit(3);
  }

  console.log('[T4] Connecting to Electron CDP on 127.0.0.1:9223...');
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9223');
  const contexts = browser.contexts();
  if (contexts.length === 0) {
    console.error('No browser context. Is Electron running with --remote-debugging-port=9223?');
    process.exit(4);
  }
  const page = contexts[0].pages()[0];
  if (!page) {
    console.error('No page in context.');
    process.exit(5);
  }

  console.log('[T4] Page URL:', page.url());

  // Fill source + target via DOM (form fill struggles with backslash paths)
  await page.evaluate(({ url, out }) => {
    document.getElementById('sheetUrl').value = url;
    document.getElementById('sheetUrl').dispatchEvent(new Event('input', { bubbles: true }));
    document.getElementById('outputLink').value = out;
    document.getElementById('outputLink').dispatchEvent(new Event('input', { bubbles: true }));
  }, { url: sheetUrl, out: outputFolder });

  console.log('[T4] Source/target filled. Clicking Step 1...');
  await page.evaluate(() => document.getElementById('btnStep1').click());

  // Poll Step 3 enabled
  console.log('[T4] Polling Step 3 readiness...');
  const ready = await page.waitForFunction(
    () => document.getElementById('btnStep3') && !document.getElementById('btnStep3').disabled,
    null,
    { timeout: 60_000 }
  );
  if (!ready) {
    console.error('Step 3 did not unlock in 60s.');
    process.exit(6);
  }
  console.log('[T4] Step 1 done. Step 3 enabled.');

  // Set up dialog handler for target folder prompt
  page.on('dialog', async (dialog) => {
    if (dialog.type() === 'prompt') {
      console.log('[T4] Accepting prompt with target folder');
      await dialog.accept(outputFolder);
    } else {
      await dialog.accept();
    }
  });

  console.log('[T4] Clicking Step 3 (GET FOOTAGE)...');
  await page.evaluate(() => document.getElementById('btnStep3').click());

  // Poll status until "done" or 5 min timeout
  console.log('[T4] Waiting for copy to finish...');
  let lastCount = -1, stableTicks = 0;
  const fs = require('fs');
  const start = Date.now();
  while (Date.now() - start < 300_000) {
    await new Promise(r => setTimeout(r, 5000));
    let count = 0;
    try { count = fs.readdirSync(outputFolder).length; } catch {}
    if (count === lastCount && count > 0) {
      stableTicks++;
      if (stableTicks >= 4) { // 20s stable
        console.log('[T4] STABLE at', count, 'files');
        break;
      }
    } else {
      stableTicks = 0;
      lastCount = count;
      console.log('[T4] files=', count);
    }
  }

  await browser.close();
  console.log('[T4] Done. Verify:', outputFolder);
}

main().catch((e) => { console.error('FAIL:', e); process.exit(1); });
