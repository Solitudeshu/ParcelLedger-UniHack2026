import {spawn} from "node:child_process";
import {existsSync, mkdirSync, openSync, closeSync, writeFileSync} from "node:fs";
import {resolve} from "node:path";
import {createServer} from "node:net";

const port = Number(process.env.PORT || 3000);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("INVALID_PORT");
if (!existsSync("apps/web/.next/BUILD_ID")) throw new Error("BUILD_REQUIRED: npm run build");
await new Promise((ok, fail) => {
  const probe = createServer();
  probe.once("error", fail);
  probe.listen(port, "127.0.0.1", () => probe.close(ok));
});
mkdirSync(".local/logs", {recursive:true});
const log = openSync(".local/logs/web-production.log", "a");
const child = spawn(process.execPath, [resolve("node_modules/next/dist/bin/next"), "start", "--hostname", "127.0.0.1", "--port", String(port)], {
  cwd:resolve("apps/web"), detached:true, windowsHide:true, stdio:["ignore", log, log],
  env:{...process.env, NODE_ENV:"production"},
});
closeSync(log);
let failure;
child.on("error", error => {failure = error;});
child.on("exit", code => {failure = new Error(`WEB_EXIT_${code}`);});
const url = `http://127.0.0.1:${port}`;
let ready = false;
for (let attempt = 0; attempt < 40; attempt++) {
  if (failure) throw failure;
  try {if ((await fetch(url, {signal:AbortSignal.timeout(1000)})).ok) {ready = true; break;}} catch {}
  await new Promise(ok => setTimeout(ok, 250));
}
if (!ready) {child.kill(); throw new Error("WEB_READINESS_TIMEOUT: see .local/logs/web-production.log");}
child.unref();
writeFileSync(".local/web-process.json", JSON.stringify({pid:child.pid, url, mode:"production", started_at:new Date().toISOString()}));
console.log(JSON.stringify({status:"READY", mode:"production", url, pid:child.pid}));
