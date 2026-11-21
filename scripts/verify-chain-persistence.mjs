import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdirSync, writeFileSync} from "node:fs";
import {setupDisputedCase} from "./chain-fixture.mjs";
import {sha256,writeJSON} from "./chain-lib.mjs";

const fixture = await setupDisputedCase();
const before = (await fixture.merchant.call(fixture.base+"/chain")).case;
assert.equal(fixture.env.chain.id,31337);
assert.equal(before.unallocated,"10000000");
assert.equal(before.tokenBalance,"10000000");
const targetBlock = await fixture.env.client.getBlockNumber({cacheTime:0});
let snapshotBytes, snapshot;
// Assert that the interval writer, not only graceful shutdown, saves new state.
for (let attempt=0;attempt<12;attempt++) {
  try {
    snapshotBytes=execFileSync("docker",["exec","infra-anvil-1","cat","/state/chain.json"],{windowsHide:true,maxBuffer:128*1024*1024});
    snapshot=JSON.parse(snapshotBytes);
    if (BigInt(snapshot.block.number)>=targetBlock) break;
  } catch {}
  await new Promise(ok=>setTimeout(ok,4000));
}
assert(snapshot && BigInt(snapshot.block.number)>=targetBlock,"Periodic snapshot did not reach funded fixture block");
mkdirSync(".local/backups/anvil",{recursive:true});
const backupPath=`.local/backups/anvil/state-${sha256(snapshotBytes).slice(0,12)}.json`;
writeFileSync(backupPath,snapshotBytes);
execFileSync("docker",["compose","-f","infra/compose.yaml","-f","infra/compose.ml.yaml","restart","anvil"],{
  windowsHide:true,stdio:["ignore","pipe","pipe"],timeout:45000});
let after;
for(let attempt=0;attempt<20;attempt++) {
  try {after=(await fixture.merchant.call(fixture.base+"/chain")).case; if(after) break;} catch {}
  await new Promise(ok=>setTimeout(ok,1000));
}
assert(after,"Chain state not available after restart");
for(const key of ["address","state","unallocated","tokenBalance","totalFunded","totalWithdrawn","merchantClaimable","buyerClaimable",
  "evidenceRoot","dispatchHash","receiptHash","acceptedReportHash","reportNonce","settlementNonce","disputeNonce"])
  assert.equal(after[key],before[key],`Lost state: ${key}`);
assert.deepEqual(after.terms,before.terms);
assert.equal(after.accounting_consistent,true);
const receipt=await fixture.env.client.getTransactionReceipt({hash:fixture.transactions.at(-1).hash});
assert.equal(receipt.status,"success");
const block=await fixture.env.client.getBlock({blockNumber:receipt.blockNumber});
assert.equal(block.hash,receipt.blockHash);
const result={status:"PASS",checked_at:new Date().toISOString(),chain_id:31337,
  scope:"Real periodic snapshot + graceful Docker restart of a funded disputed local escrow",
  shipment_id:fixture.shipment.id,escrow:after.address,target_block:targetBlock.toString(),snapshot_block:snapshot.block.number,
  snapshot_sha256:sha256(snapshotBytes),snapshot_bytes:snapshotBytes.length,backup_path:backupPath,
  unallocated_before:before.unallocated,unallocated_after:after.unallocated,token_balance_after:after.tokenBalance,
  historical_receipt_restored:true,historical_block_hash_restored:true,terms_nonces_evidence_preserved:true,
  limits:"Local test funds. Graceful restart and interval snapshot tested; sudden power loss can lose writes since the last 30-second snapshot."};
writeJSON("artifacts/evaluation/restart-persistence.json",result);
console.log(JSON.stringify(result));
