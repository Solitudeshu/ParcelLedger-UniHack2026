import {ApiSession} from "./api-session.mjs";
import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import {mkdirSync} from "node:fs";
import {BaseError, ContractFunctionRevertedError, hashTypedData, keccak256, stringToHex, concatHex} from "viem";
import {environment, artifact, safeArtifact, readJSON, writeJSON, writeContract, sha256} from "./chain-lib.mjs";

const network = process.argv[2] || "local";
const env = environment(network);
const isFuji = network === "fuji";
const explorer = "https://testnet.snowtrace.io";
const deployment = readJSON(env.manifestPath);
assert.equal(await env.client.getChainId(), env.chain.id);
assert.equal(deployment.status, "DEPLOYED");
assert.equal(deployment.chain_id, env.chain.id);
const [merchantWallet, buyerWallet] = env.wallets.slice(1, 3);
const baseUrl = isFuji ? "http://127.0.0.1:28000" : undefined;
const merchant = new ApiSession(baseUrl), buyer = new ApiSession(baseUrl);
const apiConfig = await merchant.call("/v1/config");
assert.equal(apiConfig.chain_id, env.chain.id, "API is configured for the wrong chain");
assert.equal(apiConfig.escrow_factory_address?.toLowerCase(), deployment.contracts.EscrowFactory.toLowerCase(), "API loaded a different factory");
assert.equal(apiConfig.demo_token_address?.toLowerCase(), deployment.contracts.DemoToken.toLowerCase(), "API loaded a different demo token");
assert.equal(apiConfig.demo_login_enabled, true, "Use the loopback demo stack");
if (isFuji) {
  for (const wallet of [merchantWallet, buyerWallet]) {
    const address = wallet.account.address;
    assert.ok(await env.client.getBalance({address}) > 0n, `Fund ${address} with Fuji test AVAX for gas`);
  }
}
const escrowAbi = artifact("ShipmentEscrow").abi;
const tokenAbi = artifact("DemoToken").abi;
const factoryAbi = artifact("EscrowFactory").abi;
const transactions = [];
const transact = async (wallet, address, abi, fn, args=[]) => {
  const receipt = await writeContract(env, wallet, address, abi, fn, args);
  const evidence = {action: fn, address, hash: receipt.transactionHash, block: receipt.blockNumber.toString()};
  if (isFuji) {
    evidence.explorer = `${explorer}/tx/${receipt.transactionHash}`;
    console.log(fn, evidence.explorer);
  }
  transactions.push(evidence);
  return receipt;
};
const read = (address, fn, args=[]) => env.client.readContract({address, abi: escrowAbi, functionName: fn, args});

await merchant.call("/v1/dev/session", {role: "MERCHANT"});
await buyer.call("/v1/dev/session", {role: "BUYER"});
await merchant.bind(merchantWallet); await buyer.bind(buyerWallet);
const shipment = await merchant.call("/v1/shipments", {reference: `CHAIN-${Date.now()}`, title: "Kiểm thử ký quỹ · hộp đèn gốm",
  recipient_name: "Người nhận thử nghiệm", reserve_amount: "10000000",
  notes: `Dữ liệu mô phỏng · kiểm thử hợp đồng và Safe trên ${isFuji ? "Avalanche Fuji" : "Anvil cục bộ"}.`});
const sid = shipment.id;
const invite = await merchant.call(`/v1/shipments/${sid}/invitations`, {});
await buyer.call("/v1/invitations/claim", {token: invite.token});
const dispatch = await merchant.upload(sid, "DISPATCH", "dispatch.png");
const proposal = await merchant.call(`/v1/shipments/${sid}/chain/terms`, {});
assert.equal(proposal.factory.toLowerCase(), deployment.contracts.EscrowFactory.toLowerCase());
assert.equal(proposal.terms.token.toLowerCase(), deployment.contracts.DemoToken.toLowerCase());
assert.equal(proposal.terms.arbitrationSafe.toLowerCase(), deployment.contracts.ArbitrationSafe.toLowerCase());
assert.equal(proposal.terms.merchant.toLowerCase(), merchantWallet.account.address.toLowerCase());
assert.equal(proposal.terms.buyer.toLowerCase(), buyerWallet.account.address.toLowerCase());
await transact(merchantWallet, proposal.factory, factoryAbi, "createCase", [proposal.terms]);
const address = await env.client.readContract({address: proposal.factory, abi: factoryAbi, functionName: "cases", args: [shipment.case_id]});
await transact(merchantWallet, proposal.terms.token, tokenAbi, "faucet");
await transact(merchantWallet, proposal.terms.token, tokenAbi, "approve", [address, 10000000n]);
await transact(merchantWallet, address, escrowAbi, "fund");
await transact(merchantWallet, address, escrowAbi, "commitDispatch", [dispatch.manifestHash]);

async function signBoth(kind, buyerAmount="0") {
  let intent = await merchant.call(`/v1/shipments/${sid}/chain/intents`, {kind, buyer_amount: buyerAmount});
  for (const [session, wallet] of [[merchant, merchantWallet], [buyer, buyerWallet]]) {
    const signature = await wallet.signTypedData(intent.typed_data);
    intent = await session.call(`/v1/shipments/${sid}/chain/intents/${intent.id}/signatures`, {signature});
  }
  const signatures = [merchantWallet, buyerWallet].map(wallet => intent.signatures.find(item => item.wallet_address === wallet.account.address.toLowerCase()).signature);
  return {intent, signatures};
}
let agreement = await signBoth("TERMS");
assert.equal(hashTypedData(agreement.intent.typed_data), await read(address, "termsDigest", [agreement.intent.contract_payload.expiresAt]));
await transact(merchantWallet, address, escrowAbi, "activate", [agreement.intent.contract_payload.expiresAt, ...agreement.signatures]);
const receipt = await buyer.upload(sid, "RECEIPT", "changed.png");
await transact(buyerWallet, address, escrowAbi, "commitReceipt", [receipt.manifestHash]);
const analysis = await buyer.call(`/v1/shipments/${sid}/analyses`, {query_asset_id: receipt.id});
for (let attempt=0; attempt<45; attempt++) {
  const job = await buyer.call(`/v1/jobs/${analysis.id}`);
  if (job.status === "SUCCEEDED") break;
  if (job.status === "FAILED") throw new Error("Analysis failed");
  await new Promise(resolve => setTimeout(resolve, 1000));
}
const reports = await buyer.call(`/v1/shipments/${sid}/reports`);
assert.equal(reports.length, 1);
agreement = await signBoth("REPORT");
assert.equal(hashTypedData(agreement.intent.typed_data), await read(address, "reportDigest", [agreement.intent.contract_payload]));
const blocked = await merchant.call(`/v1/shipments/${sid}/reports`, {expected_version: 1,
  observations: reports[0].document.observations, user_notes: "must not silently invalidate released signatures"}, true);
assert.equal(blocked.status, 409);
assert.equal(blocked.value.error.code, "ONCHAIN_REVISION_REQUIRED");
await transact(merchantWallet, address, escrowAbi, "acceptReport", [agreement.intent.contract_payload, ...agreement.signatures]);
agreement = await signBoth("SETTLEMENT", "3000000");
assert.equal(hashTypedData(agreement.intent.typed_data), await read(address, "settlementDigest", [agreement.intent.contract_payload]));
await transact(merchantWallet, address, escrowAbi, "settle", [agreement.intent.contract_payload, ...agreement.signatures]);
assert.equal(await read(address, "claimable", [buyerWallet.account.address]), 3000000n);
await transact(buyerWallet, address, escrowAbi, "withdraw");
await transact(merchantWallet, address, escrowAbi, "withdraw");
const final = await merchant.call(`/v1/shipments/${sid}/chain`);
assert.equal(final.case.state, "4");
assert.equal(final.case.totalWithdrawn, "10000000");
assert.equal(final.case.tokenBalance, "0");
assert.equal(final.case.accounting_consistent, true);

// Independent disputed case exercises real Safe ERC-1271 compatibility fallback.
const block = await env.client.getBlock();
const disputeTerms = {...proposal.terms, caseId: keccak256(stringToHex(randomUUID())),
  acceptBy: block.timestamp+3600n, reviewDeadline: block.timestamp+7200n};
await transact(merchantWallet, proposal.factory, factoryAbi, "createCase", [disputeTerms]);
const disputed = await env.client.readContract({address: proposal.factory, abi: factoryAbi, functionName: "cases", args: [disputeTerms.caseId]});
await transact(merchantWallet, proposal.terms.token, tokenAbi, "approve", [disputed, 10000000n]);
await transact(merchantWallet, disputed, escrowAbi, "fund");
await transact(merchantWallet, disputed, escrowAbi, "commitDispatch", [dispatch.manifestHash]);
const expiry = block.timestamp+3600n;
const termsData = {...agreement.intent.typed_data, primaryType: "TermsAcceptance",
  domain: {...agreement.intent.typed_data.domain, verifyingContract: disputed},
  types: {TermsAcceptance: [
    ["caseId", "bytes32"], ["merchant", "address"], ["buyer", "address"], ["token", "address"], ["reserveAmount", "uint256"],
    ["acceptBy", "uint64"], ["reviewDeadline", "uint64"], ["arbitrationSafe", "address"], ["termsHash", "bytes32"],
    ["dispatchHash", "bytes32"], ["expiresAt", "uint64"],
  ].map(([name, type])=>({name,type}))}, message: {...disputeTerms, dispatchHash: dispatch.manifestHash, expiresAt: expiry}};
await transact(merchantWallet, disputed, escrowAbi, "activate", [expiry,
  await merchantWallet.signTypedData(termsData), await buyerWallet.signTypedData(termsData)]);
await transact(buyerWallet, disputed, escrowAbi, "openDispute", [receipt.manifestHash]);
const resolution = {evidenceRoot: await read(disputed, "evidenceRoot"), decisionHash: keccak256(stringToHex("Local test human decision")),
  merchantAmount: 6000000n, buyerAmount: 4000000n, nonce: await read(disputed, "disputeNonce"), expiresAt: expiry};
const digest = await read(disputed, "resolutionDigest", [resolution]);
const safeData = {domain: {chainId: env.chain.id, verifyingContract: deployment.contracts.ArbitrationSafe},
  primaryType: "SafeMessage", types: {SafeMessage: [{name: "message", type: "bytes"}]}, message: {message: digest}};
const owners = [...env.wallets.slice(3)].sort((a,b)=>a.account.address.toLowerCase().localeCompare(b.account.address.toLowerCase()));
const safeSignatures = await Promise.all(owners.slice(0,2).map(wallet=>wallet.signTypedData(safeData)));
await assert.rejects(
  env.client.simulateContract({account: merchantWallet.account, address: disputed, abi: escrowAbi,
    functionName: "resolveDispute", args: [resolution, safeSignatures[0]]}),
  error => error instanceof BaseError && Boolean(error.walk(cause =>
    cause instanceof ContractFunctionRevertedError && cause.data?.errorName === "SignatureInvalid")),
  "Expected the escrow to revert with SignatureInvalid for a single Safe signature",
);
await transact(merchantWallet, disputed, escrowAbi, "resolveDispute", [resolution, concatHex(safeSignatures)]);
await transact(buyerWallet, disputed, escrowAbi, "withdraw");
await transact(merchantWallet, disputed, escrowAbi, "withdraw");
assert.equal(await read(disputed, "totalWithdrawn"), 10000000n);
const threshold = await env.client.readContract({address: deployment.contracts.ArbitrationSafe, abi: safeArtifact("Safe").abi, functionName: "getThreshold"});
assert.equal(threshold, 2n);
mkdirSync("artifacts/evaluation", {recursive: true});
const evidencePath = isFuji ? "artifacts/evaluation/fuji-chain-integration.json" : "artifacts/evaluation/chain-integration.json";
writeJSON(evidencePath, {status: "PASS", network: isFuji ? "AVALANCHE_FUJI" : "LOCAL_ANVIL", chain_id: env.chain.id,
  shipment_id: sid, escrow: address, dispute_escrow: disputed, safe: deployment.contracts.ArbitrationSafe,
  real_safe_2_of_3_verified: true, one_signature_rejected: true, one_signature_revert: "SignatureInvalid",
  typed_hash_parity: ["TermsAcceptance", "ReportApproval", "Settlement"],
  offchain_revision_while_signed_rejected: true, full_reserve_withdrawn: true, transactions,
  scope: "Test funds, synthetic images, baseline inference and single-operator demo signers."});
console.log(JSON.stringify({status: "PASS", chain_id: env.chain.id, shipment_id: sid,
  transactions: transactions.length, safe_quorum: "2/3", evidence: evidencePath}));
