// node dispatch.js <arg>
//   <arg> = plain JSON  | base64-JSON | @<path-to-json-file>
// json={from,to,text,mode,project_path,timeout_sec}
const http=require('http');
const fs=require('fs');
let raw=process.argv[2]||'';
let p;
function tryParse(s){try{return JSON.parse(s);}catch(e){return null;}}
if(raw.startsWith('@')){
  raw=fs.readFileSync(raw.slice(1),'utf8');
}
p=tryParse(raw);                                   // plain JSON
if(!p){p=tryParse(Buffer.from(raw,'base64').toString('utf8'));} // base64 fallback
if(!p){console.error('bad payload (not json/base64/@file)');process.exit(2);}

const d=JSON.stringify({
  from:p.from||'clauC',
  to:p.to,
  text:p.text,
  mode:p.mode||'broker',
  project_path:p.project_path||'',
  timeout_sec:p.timeout_sec||6000
});

const r=http.request({
  host:'localhost',
  port:8083,
  path:'/api/council/message',
  method:'POST',
  headers:{
    'Content-Type':'application/json',
    'Content-Length':Buffer.byteLength(d),
    'Origin':'http://localhost:8083'
  }
},s=>{
  let b='';
  s.on('data',c=>b+=c);
  s.on('end',()=>process.stdout.write(b));
});

r.on('error',e=>{
  console.error('REQERR',e.message);
  process.exit(1);
});

r.write(d);
r.end();
