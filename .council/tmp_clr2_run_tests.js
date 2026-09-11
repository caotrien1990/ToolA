const http = require("http");

function call(tool, body, fields) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const path = "/api/call/" + tool + (fields ? "?fields=" + fields : "");
    const req = http.request(
      {
        hostname: "localhost",
        port: 8083,
        path,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload),
        },
      },
      (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => resolve({ status: res.statusCode, data }));
      }
    );
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

const PP = "D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ";
const mode = process.argv[2];

(async () => {
  if (mode === "validate") {
    const files = [
      "bin/clm_qwen_verify_runner.py",
      "bin/clm_semantic_embed.py",
      "bin/clm_lib/schema.py",
      "bin/clm_qwen_verify_runner_test.py",
      "tests/test_semantic_ttl_cache_tombstone.py",
    ];
    for (const file_path of files) {
      const result = await call(
        "validate_file",
        { project_path: PP, file_path, lang: "python" },
        "ok,lang,checked,error_name,message"
      );
      console.log("VALIDATE " + file_path);
      console.log(result.data);
    }
    const pr = await call(
      "run_project_command",
      {
        project_path: PP,
        command: "pyright bin/clm_qwen_verify_runner.py bin/clm_semantic_embed.py bin/clm_lib/schema.py bin/clm_folder_watcher.py tests/test_semantic_ttl_cache_tombstone.py",
        timeout: 120,
        stdout_max_chars: 8000,
        stderr_max_chars: 4000,
      },
      "ok,exit_code,stdout,stderr,error_name"
    );
    console.log("PYRIGHT");
    console.log(pr.data);
    return;
  }
  const result = await call(
    "run_project_command",
    {
      project_path: PP,
      command: mode,
      timeout: 180,
      stdout_max_chars: 10000,
      stderr_max_chars: 4000,
    },
    "ok,exit_code,stdout,stderr,error_name,message"
  );
  console.log(result.data);
})().catch((err) => {
  console.log("ERR " + err);
  process.exit(1);
});
