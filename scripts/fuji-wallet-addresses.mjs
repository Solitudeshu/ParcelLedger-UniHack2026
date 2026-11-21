import assert from "node:assert/strict";
import {formatEther} from "viem";
import {environment} from "./chain-lib.mjs";

const {accounts, chain, client} = environment("fuji");
assert.equal(await client.getChainId(), chain.id);
const roles = ["deployer", "merchant", "buyer"];
const wallets = [];
for (const [index, role] of roles.entries()) {
  const {address} = accounts[index];
  wallets.push({role, address, test_avax: formatEther(await client.getBalance({address}))});
}
console.log(JSON.stringify({chain_id: chain.id, wallets}, null, 2));
