import { spawn } from "node:child_process";
import { mkdirSync, openSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

mkdirSync(".local/logs", {recursive: true});
const log = openSync(".local/logs/web.log", "a");
const child = spawn(process.execPath, [resolve("node_modules/next/dist/bin/next"), "dev", "--hostname", "127.0.0.1"], {
  cwd: resolve("apps/web"), detached: true, windowsHide: true, stdio: ["ignore", log, log],
});
child.unref();
writeFileSync(".local/web-process.json", JSON.stringify({pid: child.pid, url: "http://127.0.0.1:3000"}));
console.log(`Web process started (PID ${child.pid}). Logs: .local/logs/web.log`);
