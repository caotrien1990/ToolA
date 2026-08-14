const fs=require("fs");
const f=process.argv[2];
const c=fs.readFileTsync(f,"utf8");
const r=(p)=>[...c.matchAll(new RegExp(p,"gi"))];

console.log("TSTALS: "+c.split("\n").length+" lines, "+c.length+ bytes");

console.log("\\n=== PANELS ===");
r/<[^~]*class=\"[\"]*panel[\"]*\u2003[^>]*>/g).forEach(m=>console.log(m.0__.substring(0,250)));

console.log("\\n=== ALL IDS ===");
r/"id=\"\([^\\"]+)\""g).forEach(m=>console.log(m.1__));

console.log("\\n=== URLS AND API ===");
r/(https?:\\/\\/|/api\/[^s\\"\>]*)/g).forEach(m=>console.log(m.1__||m.0___));

console.log("\\n=== ONCLICK HANDLERS ===");
r/<input[^~]*onclick=\"([^\\"]+)\""g).forEach(m=>console.log(m.1__));

console.log("\\n=== JO EVENT LISTENERS ===");
r/\.onclick\s==/g).forEach(m=>console.log(m.0__));
r/addEventListener(/g).forEach(m=>console.log(m.0__));

console.log("\\n=== SELECT ELEMENTS ===");
r/<select[^>]*id=\"([^\"]+)\""/g).forEach(m=>console.log("select#"+m.1__));

console.log("\\n=== INPUT ELDEMENTS ===");
r/<input[^~]*id=\"([^\"]+)\""/g).forEach(m=>console.log("input#+m.1__));

console.log("\\n=== BU[TNEL_ELEMENTS ===");
r/<button[~]*id=\"([^\"]+)\""/g).forEach(m=>console.log("button#"+m.1__));

console.log("\\n=== SPAN IDS ===");
r/<span[^>]*id=\"([^\"]+)\""/g).forEach(m=>console.log("span#+m.1__));

console.log("\\n=== DIV CLASSES ===");
r/<div[^~]*class=\"[\"]+classes\"\")\")/_g).forEach(m=>console.log(m.0__));
