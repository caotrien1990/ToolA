// Ring round: 6 non-leader workers, each uses the council_message [A] TOOL to ping the next.
const http = require('http');
function post(path, body) {
  return new Promise(res => {
    const d = JSON.stringify(body);
    const r = http.request({ host: 'localhost', port: 8083, path, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(d), 'Origin': 'http://localhost:8083' } },
      s => { let b = ''; s.on('data', c => b += c); s.on('end', () => res(b)); });
    r.on('error', e => res('ERR ' + e.message)); r.write(d); r.end();
  });
}
const ring = [['codo','ope'],['ope','comc'],['comc','copi'],['copi','clin'],['clin','hermes'],['hermes','codo']];
(async () => {
  const out = [];
  for (const [from, to] of ring) {
    const text = `Bai test ring (dung TOOL [A] council_message qua HTTP /api/call, KHONG MCP, KHONG curl). Gui worker '${to}' bang Invoke-RestMethod POST http://localhost:8083/api/call/council_message body {sender:'${from}',to:'${to}',text:'ping tu ${from}C, tra loi 1 cau ngan'}. Doc field reply tra ve, cho biet ${to}C tra loi gi. 1 dong. Khong bia.`;
    const r = await post('/api/council/message', { from: 'clauC', to: from, text, mode: 'broker', timeout_sec: 300 });
    let reply = '(parse err)';
    try { const j = JSON.parse(r); reply = (j.reply && j.reply.text) || j.error || '(none)'; } catch (e) {}
    out.push(`${from}C -> ${to}C: ${String(reply).replace(/\n/g, ' ').slice(0, 150)}`);
    console.log(out[out.length - 1]);
  }
  console.log('\n=== RING6 SUMMARY ===');
  out.forEach(l => console.log(l));
})();
