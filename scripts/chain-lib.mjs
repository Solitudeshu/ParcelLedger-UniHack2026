import {createPublicClient, createWalletClient, http, zeroAddress} from "viem";
import {generatePrivateKey, mnemonicToAccount, privateKeyToAccount} from "viem/accounts";
import {foundry, avalancheFuji} from "viem/chains";
import {appendFileSync, existsSync, readFileSync, mkdirSync, writeFileSync} from "node:fs";
import {createHash} from "node:crypto";

export const sha256 = value => createHash("sha256").update(value).digest("hex");
export const readJSON = path => JSON.parse(readFileSync(path, "utf8"));
export const writeJSON = (path, value) => writeFileSync(path, JSON.stringify(value, (_, v) => typeof v === "bigint" ? v.toString() : v, 2));
export const artifact = name => readJSON(`contracts/out/${name}.sol/${name}.json`);
export const safeArtifact = name => readJSON(`contracts/vendor/safe-1.4.1/${name}.json`);
export {zeroAddress};

export function environment(network = "local") {
  if (!["local", "fuji"].includes(network)) throw new Error("TEST_NETWORKS_ONLY");
  const chain = network === "local" ? foundry : avalancheFuji;
  const url = network === "local" ? "http://127.0.0.1:18545" : "https://api.avax-test.network/ext/bc/C/rpc";
  let accounts;
  if (network === "local") {
    // Public Anvil development mnemonic. Never valid for real-fund deployments.
    accounts = Array.from({length: 6}, (_, addressIndex) => mnemonicToAccount(
      "test test test test test test test test test test test junk", {addressIndex}));
  } else {
    mkdirSync(".local", {recursive: true});
    const path = process.env.PARCELLEDGER_FUJI_WALLETS || ".local/fuji-wallets.json";
    const manifestPath = "artifacts/deployments/fuji.json";
    if (!existsSync(path) && (existsSync(manifestPath) || process.env.PARCELLEDGER_FUJI_WALLETS)) {
      throw new Error("FUJI_WALLETS_MISSING: select the original wallet file for this deployment");
    }
    if (!existsSync(path)) writeJSON(path, {purpose: "UNIHACK_FUJI_TEST_FUNDS_ONLY", keys: Array.from({length: 6}, generatePrivateKey)});
    accounts = readJSON(path).keys.map(privateKeyToAccount);
    if (existsSync(manifestPath)) {
      const deployed = readJSON(manifestPath);
      const existingOwners = (deployed.safe_owners || []).map(value => value.toLowerCase()).sort();
      const walletOwners = accounts.slice(3).map(account => account.address.toLowerCase()).sort();
      if (deployed.chain_id !== chain.id || accounts.length !== 6 ||
          accounts[0].address.toLowerCase() !== deployed.deployer?.toLowerCase() ||
          existingOwners.length !== 3 || !existingOwners.every((owner, i) => owner === walletOwners[i])) {
        throw new Error("FUJI_WALLET_MANIFEST_MISMATCH");
      }
    }
  }
  const client = createPublicClient({chain, transport: http(url, {timeout: 15000, retryCount: 2})});
  const wallets = accounts.map(account => createWalletClient({chain, account, transport: http(url)}));
  return {network, chain, url, client, accounts, wallets, owner: wallets[0],
    manifestPath: `artifacts/deployments/${network}.json`};
}

export async function confirmed(client, hash, submission) {
  if (submission && client.chain?.id === avalancheFuji.id) {
    const record = {time: new Date().toISOString(), chain_id: client.chain.id, hash, ...submission};
    console.log("Fuji broadcast", record.action, record.from, hash);
    appendFileSync("artifacts/deployments/fuji-broadcast.jsonl", JSON.stringify(record) + "\n");
  }
  // Give Fuji's load-balanced RPC nodes time to expose the same post-transaction state.
  const confirmations = client.chain?.id === avalancheFuji.id ? 3 : 1;
  const receipt = await client.waitForTransactionReceipt({hash, confirmations, timeout: 60000});
  if (receipt.status !== "success") throw new Error("TRANSACTION_REVERTED: " + hash);
  return receipt;
}

export async function writeContract(env, wallet, address, abi, functionName, args = []) {
  const {request} = await env.client.simulateContract({account: wallet.account, address, abi, functionName, args});
  const hash = await wallet.writeContract(request);
  return confirmed(env.client, hash, {action: functionName, from: wallet.account.address, to: address, nonce: request.nonce});
}
