import {spawnSync} from "node:child_process";

const url = "http://127.0.0.1:18545";
try {
  const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({jsonrpc: "2.0", id: 1, method: "eth_chainId", params: []}), signal: AbortSignal.timeout(1500)});
  const value = await response.json();
  if (value.result !== "0x7a69") throw new Error("Unexpected chain on development port");
  console.log("Development chain already running at " + url);
} catch (error) {
  if (error.message?.includes("Unexpected chain")) throw error;
  const result = spawnSync("docker", ["compose", "-f", "infra/compose.yaml", "up", "-d", "anvil"],
    {stdio: "inherit", windowsHide: true});
  if (result.error || result.status !== 0) throw new Error("Cannot start development chain in Docker");
  console.log("Development chain started at " + url);
}
