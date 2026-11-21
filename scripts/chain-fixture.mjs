import assert from "node:assert/strict";
import {keccak256, stringToHex} from "viem";
import {ApiSession} from "./api-session.mjs";
import {environment, artifact, readJSON, writeJSON, writeContract} from "./chain-lib.mjs";


export async function setupDisputedCase() {
const env=environment("local"), deployed=readJSON(env.manifestPath);
const [merchantWallet,buyerWallet]=env.wallets.slice(1,3);
const owners=env.wallets.slice(3);
const escrowAbi=artifact("ShipmentEscrow").abi, factoryAbi=artifact("EscrowFactory").abi, tokenAbi=artifact("DemoToken").abi;
const merchant=new ApiSession(), buyer=new ApiSession(), transactions=[];
const tx=async(wallet,address,abi,fn,args=[])=>{
  const receipt=await writeContract(env,wallet,address,abi,fn,args);
  transactions.push({hash:receipt.transactionHash,block:receipt.blockNumber.toString()});
};
await merchant.call("/v1/dev/session",{role:"MERCHANT"});
await buyer.call("/v1/dev/session",{role:"BUYER"});
await merchant.bind(merchantWallet); await buyer.bind(buyerWallet);
const shipment=await merchant.call("/v1/shipments",{reference:`SAFE-${Date.now()}`,title:"Kiểm thử hội đồng Safe",recipient_name:"Người nhận thử nghiệm",reserve_amount:"10000000",notes:"Dữ liệu mô phỏng · API arbitration integration"});
const sid=shipment.id, base=`/v1/shipments/${sid}`;
const invitation=await merchant.call(base+"/invitations",{});
await buyer.call("/v1/invitations/claim",{token:invitation.token});
const dispatch=await merchant.upload(sid,"DISPATCH","dispatch.png");
const terms=await merchant.call(base+"/chain/terms",{});
await tx(merchantWallet,terms.factory,factoryAbi,"createCase",[terms.terms]);
const address=await env.client.readContract({address:terms.factory,abi:factoryAbi,functionName:"cases",args:[shipment.case_id]});
await tx(merchantWallet,deployed.contracts.DemoToken,tokenAbi,"faucet");
await tx(merchantWallet,deployed.contracts.DemoToken,tokenAbi,"approve",[address,10000000n]);
await tx(merchantWallet,address,escrowAbi,"fund");
await tx(merchantWallet,address,escrowAbi,"commitDispatch",[dispatch.manifestHash]);
const intent=await merchant.call(base+"/chain/intents",{kind:"TERMS"});
await tx(merchantWallet,address,escrowAbi,"activate",[intent.contract_payload.expiresAt,await merchantWallet.signTypedData(intent.typed_data),await buyerWallet.signTypedData(intent.typed_data)]);
const receipt=await buyer.upload(sid,"RECEIPT","changed.png");
await tx(buyerWallet,address,escrowAbi,"commitReceipt",[receipt.manifestHash]);
const analysis=await buyer.call(base+"/analyses",{query_asset_id:receipt.id});
let result;
for (let i=0;i<60;i++) {
  const job=await buyer.call(`/v1/jobs/${analysis.id}`);
  if(job.status==="SUCCEEDED") {result=job.result; break;}
  if(job.status==="FAILED") throw new Error("ANALYSIS_FAILED");
  await new Promise(resolve=>setTimeout(resolve,1000));
}
assert.ok(result,"ANALYSIS_TIMEOUT");
assert.equal(result.research_advice?.status,"EVALUATED");
assert.equal(result.research_advice.changes_product_decision,false);
await tx(buyerWallet,address,escrowAbi,"openDispute",[receipt.manifestHash]);
return {env,deployed,merchantWallet,buyerWallet,owners,escrowAbi,factoryAbi,tokenAbi,merchant,buyer,transactions,tx,shipment,sid,base,dispatch,terms,address,receipt,result};
}
