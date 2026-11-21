import assert from "node:assert/strict";
import {performance} from "node:perf_hooks";
import {ApiSession} from "./api-session.mjs";
import {writeJSON} from "./chain-lib.mjs";

const batchId=Date.now(), metadata=[], cases=[];
async function measured(session,path,body){
  const start=performance.now();
  const result=await session.call(path,body);
  metadata.push({path:path.replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/g,":id"),milliseconds:performance.now()-start});
  return result;
}
// Independent sessions avoid sharing mutable CSRF/cookie state across cases.
await Promise.all(Array.from({length:10},async(_,i)=>{
  const merchant=new ApiSession(),buyer=new ApiSession();
  await merchant.call("/v1/dev/session",{role:"MERCHANT"});
  await buyer.call("/v1/dev/session",{role:"BUYER"});
  const shipment=await measured(merchant,"/v1/shipments",{reference:`LOAD-${batchId}-${i}`,title:`Kiểm tải ${i+1}`,recipient_name:"Dữ liệu mô phỏng",notes:"Load test; not a customer case"});
  const base=`/v1/shipments/${shipment.id}`;
  const invitation=await measured(merchant,base+"/invitations",{});
  await measured(buyer,"/v1/invitations/claim",{token:invitation.token});
  await merchant.upload(shipment.id,"DISPATCH","dispatch.png");
  const receipt=await buyer.upload(shipment.id,"RECEIPT",i%2?"lighting.png":"changed.png");
  cases.push({buyer,shipment,base,receipt});
}));
const start=performance.now();
await Promise.all(cases.map(async item=>{
  item.queuedAt=performance.now();
  item.job=await measured(item.buyer,item.base+"/analyses",{query_asset_id:item.receipt.id});
}));
const results=[];
while(results.length<cases.length){
  assert.ok(performance.now()-start<180_000,"WORKFLOW_LOAD_TIMEOUT");
  for(const item of cases.filter(c=>!c.done)){
    const job=await measured(item.buyer,`/v1/jobs/${item.job.id}`);
    assert.notEqual(job.status,"FAILED");
    if(job.status==="SUCCEEDED"){
      item.done=true;
      assert.equal(job.result.research_advice.status,"EVALUATED");
      assert.equal(job.result.research_change.status,"EVALUATED");
      results.push({shipment_id:item.shipment.id,job_id:job.id,attempts:job.attempts,seconds:(performance.now()-item.queuedAt)/1000});
    }
  }
  if(results.length<cases.length)await new Promise(r=>setTimeout(r,300));
}
function percentile(values,p){const s=[...values].sort((a,b)=>a-b);return s[Math.min(s.length-1,Math.ceil(s.length*p)-1)];}
const report={status:"PASS",scope:"LOCAL_10_CONCURRENT_CASES_WITH_ONE_CPU_WORKER",cases:10,metadata_requests:metadata.length,
  metadata_p50_ms:percentile(metadata.map(m=>m.milliseconds),.5),metadata_p95_ms:percentile(metadata.map(m=>m.milliseconds),.95),
  queue_plus_inference_p95_seconds:percentile(results.map(r=>r.seconds),.95),total_seconds:(performance.now()-start)/1000,
  results,limitations:["Single run on procedural fixtures; not a production throughput guarantee.","Queue time includes sequential worker execution; model-only latency is reported separately."]};
report.metadata_target_500ms_met=report.metadata_p95_ms<500;
writeJSON("artifacts/evaluation/workflow-load.json",report);
console.log(JSON.stringify({...report,results:undefined}));
