import assert from "node:assert/strict";
import {keccak256, stringToHex} from "viem";
import {ApiSession} from "./api-session.mjs";
import {environment, artifact, readJSON, writeJSON, writeContract} from "./chain-lib.mjs";

import {setupDisputedCase} from "./chain-fixture.mjs";
const {env,merchantWallet,buyerWallet,owners,escrowAbi,merchant,buyer,transactions,tx,sid,base,address,result}=await setupDisputedCase();
const body={buyer_amount:"4000000",decision_text:"Thử nghiệm: hội đồng xem ảnh gốc và thống nhất phân bổ 4 kcTEST cho người nhận, 6 kcTEST cho người gửi."};
let proposal=await merchant.call(base+"/arbitration",body);
const staleSignature=await owners[0].signTypedData(proposal.typed_data);
await tx(merchantWallet,address,escrowAbi,"appendEvidence",[keccak256(stringToHex("New arbitration test evidence"))]);
const stale=await merchant.call(base+`/arbitration/${proposal.id}/signatures`,{signature:staleSignature},true);
assert.equal(stale.status,409);
proposal=await merchant.call(base+"/arbitration",body);
const invalid=await merchant.call(base+`/arbitration/${proposal.id}/signatures`,{signature:await buyerWallet.signTypedData(proposal.typed_data)},true);
assert.equal(invalid.status,403);
const firstSignature=await owners[0].signTypedData(proposal.typed_data);
for(let i=0;i<2;i++) {
  proposal=await merchant.call(base+`/arbitration/${proposal.id}/signatures`,{signature:firstSignature});
  assert.equal(proposal.signatures.length,1);
  assert.equal(proposal.ready,false);
}
let oneRejected=false;
try {await env.client.simulateContract({account:merchantWallet.account,address,abi:escrowAbi,functionName:"resolveDispute",args:[proposal.resolution,proposal.safe_signature]});} catch {oneRejected=true;}
assert.equal(oneRejected,true);
proposal=await buyer.call(base+`/arbitration/${proposal.id}/signatures`,{signature:await owners[1].signTypedData(proposal.typed_data)});
assert.equal(proposal.ready,true);
await tx(merchantWallet,address,escrowAbi,"resolveDispute",[proposal.resolution,proposal.safe_signature]);
await tx(buyerWallet,address,escrowAbi,"withdraw");
await tx(merchantWallet,address,escrowAbi,"withdraw");
const final=await merchant.call(base+"/chain");
assert.equal(final.case.state,"4");
assert.equal(final.case.totalWithdrawn,"10000000");
assert.equal(final.case.accounting_consistent,true);
assert.equal((await merchant.call(base+"/arbitration")).proposals.some(p=>p.valid),false);
writeJSON("artifacts/evaluation/arbitration-integration.json",{status:"PASS",network:"LOCAL_ANVIL",shipment_id:sid,escrow:address,
  api_safe_quorum:true,one_signature_rejected:oneRejected,duplicate_owner_not_counted:true,nonowner_rejected:true,
  evidence_change_invalidates:true,fully_withdrawn:true,shadow_advice:result.research_advice,transactions});
console.log(JSON.stringify({status:"PASS",shipment_id:sid,transactions:transactions.length,shadow_model:result.research_advice.model_version}));
