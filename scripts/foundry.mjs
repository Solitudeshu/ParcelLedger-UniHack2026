import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
const tool = process.argv[2] || "forge";
if (!["forge", "anvil"].includes(tool)) throw new Error("Unsupported Foundry tool");
const path = process.platform === "win32" ? resolve(`node_modules/@foundry-rs/${tool}-win32-amd64/bin/${tool}.exe`) : tool;
const result = spawnSync(path, process.argv.slice(3), {stdio: "inherit", windowsHide: true});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
