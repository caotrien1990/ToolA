// Drive Rawcut [E:U] via raw CDP (no playwright npm needed).
// Requires: Electron launched with --remote-debugging-port=9223 (see main.js V15 patch).
// Node 22+ for built-in WebSocket.

const SHEET = 'https://docs.google.com/spreadsheets/d/15Te2WS6rDNpc_syKnpPcg_oFC_KfL1FFiGwSqesAqSg/edit?gid=0#gid=0';
const TARGET = String.raw`D:\0. TRIEN\2. PROJECT\393. H. 599\1. FOOTAGE\New folder\TEST ELECTRON_UI`;

(async () => {
  // Find page target
  const r = await fetch('http://127.0.0.1:9223/json');
  const targets = await r.json();
  const page = targets.find(t => t.type === 'page');
  if (!page) { console.error('No page target'); process.exit(1); }
  console.log('[T4-E:U] page url:', page.url.substring(0, 80));

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let msgId = 0;
  const pending = new Map();
  const events = [];

  ws.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.id && pending.has(data.id)) {
      pending.get(data.id)(data);
      pending.delete(data.id);
    } else if (data.method) {
      events.push(data);
    }
  };

  const send = (method, params = {}) => new Promise((resolve) => {
    const id = ++msgId;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });

  const evalJs = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result?.result?.value;
  };

  await new Promise((resolve) => { ws.onopen = resolve; });
  console.log('[T4-E:U] CDP connected');

  // Enable Page domain for dialog events
  await send('Page.enable', {});

  // Override window.prompt to return target folder automatically (Electron disables prompt by default,
  // and Rawcut Step 3 uses prompt(). Inject override before clicking).
  await evalJs(`window.prompt = function(){ return ${JSON.stringify(TARGET)}; }; 'prompt-overridden';`);
  console.log('[T4-E:U] prompt() overridden');

  // Fill source + target inputs
  await evalJs(`
    document.getElementById('sheetUrl').value = ${JSON.stringify(SHEET)};
    document.getElementById('sheetUrl').dispatchEvent(new Event('input', {bubbles:true}));
    document.getElementById('outputLink').value = ${JSON.stringify(TARGET)};
    document.getElementById('outputLink').dispatchEvent(new Event('input', {bubbles:true}));
    'filled';
  `);
  console.log('[T4-E:U] inputs filled');

  // Click Step 1
  await evalJs(`document.getElementById('btnStep1').click(); 'clicked-step1';`);
  console.log('[T4-E:U] Step 1 clicked, waiting...');

  // Poll Step 3 enabled
  let waited = 0;
  while (waited < 90_000) {
    await new Promise(r => setTimeout(r, 2000));
    waited += 2000;
    const disabled = await evalJs(`document.getElementById('btnStep3')?.disabled`);
    process.stdout.write(`.`);
    if (disabled === false) {
      console.log('\n[T4-E:U] Step 3 enabled after', waited, 'ms');
      break;
    }
  }

  // Click Step 3
  await evalJs(`document.getElementById('btnStep3').click(); 'clicked-step3';`);
  console.log('[T4-E:U] Step 3 clicked');

  // Wait copy + report
  const fs = require('fs');
  let lastCount = -1, stable = 0;
  const start = Date.now();
  while (Date.now() - start < 600_000) {
    await new Promise(r => setTimeout(r, 5000));
    let count = 0;
    try { count = fs.readdirSync(TARGET).length; } catch {}
    if (count === lastCount && count > 0) {
      stable++;
      if (stable >= 4) {
        console.log('[T4-E:U] STABLE', count, 'files in', TARGET);
        break;
      }
    } else {
      console.log('[T4-E:U] files=', count);
      stable = 0;
      lastCount = count;
    }
  }

  ws.close();
  console.log('[T4-E:U] Done.');
  process.exit(0);
})().catch(e => { console.error('FAIL:', e); process.exit(1); });
