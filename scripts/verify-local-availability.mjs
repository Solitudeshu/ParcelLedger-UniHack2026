// Read-only checks after an unexpected Docker Desktop shutdown.
import assert from "node:assert/strict";
import {createPublicClient, http} from "viem";
import {foundry} from "viem/chains";
import {readJSON, artifact, writeJSON} from "./chain-lib.mjs";

const client = createPublicClient({chain:foundry, transport:http("http://127.0.0.1:18545", {timeout:10000})});
const deployment = readJSON("artifacts/deployments/local.json");
const previous = readJSON("artifacts/evaluation/restart-persistence.json");
assert.equal(await client.getChainId(), 31337);
const web = await fetch("http://127.0.0.1:3000", {signal:AbortSignal.timeout(10000)});
assert.equal(web.status, 200);
const ready = await fetch("http://127.0.0.1:8000/health/ready", {signal:AbortSignal.timeout(10000)});
assert.equal(ready.status, 200);
const health = await ready.json();
assert.equal(health.status, "ok");
const codeChecks = {};
for (const [name, address] of Object.entries(deployment.contracts)) {
  const code = await client.getCode({address});
  assert(code && code !== "0x", `Missing deployed code: ${name}`);
  codeChecks[name] = {address, bytecode_bytes:(code.length - 2)/2};
}
let preservedReceipts = 0;
for (const tx of deployment.transactions) {
  const receipt = await client.getTransactionReceipt({hash:tx.hash});
  assert.equal(receipt.status, "success");
  const block = await client.getBlock({blockNumber:receipt.blockNumber});
  assert.equal(block.hash, receipt.blockHash);
  preservedReceipts++;
}
const balance = await client.readContract({address:deployment.contracts.DemoToken,
  abi:artifact("DemoToken").abi, functionName:"balanceOf", args:[previous.escrow]});
assert.equal(balance.toString(), previous.token_balance_after);
const observedAt = new Date();
const clientTimezone = "Asia/Ho_Chi_Minh";
const clientDate = new Intl.DateTimeFormat("en-CA", {
  timeZone:clientTimezone, year:"numeric", month:"2-digit", day:"2-digit"
}).format(observedAt);
const result = {status:"PASS", client_date:clientDate, client_timezone:clientTimezone, observed_at:observedAt.toISOString(),
  scope:"Read-only availability and historical local-chain checks after Docker restart; no redeployment or settlement",
  web_status:web.status, api:health, chain_id:31337, contracts:codeChecks,
  deployment_receipts_and_block_hashes_preserved:preservedReceipts,
  previous_fixture_escrow:previous.escrow, previous_fixture_token_balance:balance.toString(),
  limits:"Not a full workflow rerun and not proof of zero-loss sudden-power recovery."};
writeJSON("artifacts/evaluation/local-availability.json", result);
console.log(JSON.stringify(result));
