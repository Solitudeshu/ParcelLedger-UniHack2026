import {existsSync, mkdirSync} from "node:fs";
import {decodeEventLog, encodeFunctionData, formatEther, parseEther} from "viem";
import {artifact, safeArtifact, environment, writeJSON, readJSON, confirmed, writeContract, sha256, zeroAddress} from "./chain-lib.mjs";

const env = environment(process.argv[2] || "local");
if (await env.client.getChainId() !== env.chain.id) throw new Error("CHAIN_ID_MISMATCH");
mkdirSync("artifacts/deployments", {recursive: true});
const balance = await env.client.getBalance({address: env.accounts[0].address});
if (balance < parseEther("0.08")) {
  const result = {status: "AWAITING_TEST_AVAX", network: env.network, chain_id: env.chain.id,
    deployer: env.accounts[0].address, balance_avax: formatEther(balance), target_test_avax: "0.15",
    next_command: `node scripts/deploy-chain.mjs ${env.network}`};
  writeJSON(`artifacts/deployments/${env.network}-preflight.json`, result);
  console.log(JSON.stringify(result));
  process.exit(2);
}
let deployment = existsSync(env.manifestPath) ? readJSON(env.manifestPath) : null;
if (deployment && deployment.chain_id !== env.chain.id) throw new Error("DEPLOYMENT_CHAIN_MISMATCH");
deployment ||= {status: "DEPLOYING", network: env.network, chain_id: env.chain.id, rpc_url: env.url,
  start_block: (await env.client.getBlockNumber()).toString(), deployer: env.accounts[0].address,
  contracts: {}, transactions: [], bytecode_hashes: {}, safe_owners: env.accounts.slice(3).map(a => a.address),
  safe_threshold: 2, custody_scope: "Demonstration keys controlled by one local operator; not independent production arbitration."};
async function deploy(name, compiled, args=[]) {
  if (deployment.contracts[name]) {
    if (!await env.client.getCode({address: deployment.contracts[name]})) throw new Error("DEPLOYMENT_CODE_MISSING");
    return deployment.contracts[name];
  }
  const bytecode = typeof compiled.bytecode === "string" ? compiled.bytecode : compiled.bytecode.object;
  const hash = await env.owner.deployContract({abi: compiled.abi, bytecode, args});
  const receipt = await confirmed(env.client, hash, {action: `deploy:${name}`, from: env.owner.account.address});
  deployment.contracts[name] = receipt.contractAddress;
  deployment.bytecode_hashes[name] = sha256(bytecode);
  deployment.transactions.push({action: `deploy:${name}`, hash: receipt.transactionHash, block: receipt.blockNumber.toString()});
  writeJSON(env.manifestPath, deployment);
  console.log("Deployed", name, receipt.contractAddress);
  return receipt.contractAddress;
}
const token = await deploy("DemoToken", artifact("DemoToken"));
const factory = await deploy("EscrowFactory", artifact("EscrowFactory"), [env.accounts[0].address]);
const singleton = await deploy("SafeSingleton", safeArtifact("Safe"));
const handler = await deploy("SafeFallbackHandler", safeArtifact("CompatibilityFallbackHandler"));
const proxyFactory = await deploy("SafeProxyFactory", safeArtifact("SafeProxyFactory"));
if (!deployment.contracts.ArbitrationSafe) {
  const initializer = encodeFunctionData({abi: safeArtifact("Safe").abi, functionName: "setup",
    args: [deployment.safe_owners, 2n, zeroAddress, "0x", handler, zeroAddress, 0n, zeroAddress]});
  const receipt = await writeContract(env, env.owner, proxyFactory, safeArtifact("SafeProxyFactory").abi,
    "createProxyWithNonce", [singleton, initializer, BigInt(Date.now())]);
  const event = receipt.logs.map(log => {try {return decodeEventLog({abi: safeArtifact("SafeProxyFactory").abi, ...log});} catch {return null;}})
    .find(item => item?.eventName === "ProxyCreation");
  if (!event) throw new Error("SAFE_PROXY_EVENT_MISSING");
  deployment.contracts.ArbitrationSafe = event.args.proxy;
  deployment.transactions.push({action: "create:ArbitrationSafe", hash: receipt.transactionHash, block: receipt.blockNumber.toString()});
  writeJSON(env.manifestPath, deployment);
}
for (const [name, address, functionName, allowedFunction] of [
  ["token", token, "setAllowedToken", "allowedTokens"],
  ["arbitrator", deployment.contracts.ArbitrationSafe, "setAllowedArbitrator", "allowedArbitrators"],
]) {
  if (!await env.client.readContract({address: factory, abi: artifact("EscrowFactory").abi, functionName: allowedFunction, args: [address]})) {
    const receipt = await writeContract(env, env.owner, factory, artifact("EscrowFactory").abi, functionName, [address, true]);
    deployment.transactions.push({action: `allow:${name}`, hash: receipt.transactionHash, block: receipt.blockNumber.toString()});
    writeJSON(env.manifestPath, deployment);
  }
}
const safeAddress = deployment.contracts.ArbitrationSafe;
const threshold = await env.client.readContract({address: safeAddress, abi: safeArtifact("Safe").abi, functionName: "getThreshold"});
const owners = await env.client.readContract({address: safeAddress, abi: safeArtifact("Safe").abi, functionName: "getOwners"});
if (threshold !== 2n || [...owners].sort().join().toLowerCase() !== [...deployment.safe_owners].sort().join().toLowerCase()) throw new Error("SAFE_CONFIGURATION_MISMATCH");
deployment.status = "DEPLOYED";
deployment.safe_configuration_verified = true;
writeJSON(env.manifestPath, deployment);
console.log(JSON.stringify({status: deployment.status, chain_id: env.chain.id, contracts: deployment.contracts}));
