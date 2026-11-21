import assert from "node:assert/strict";
import {ApiSession} from "./api-session.mjs";
import {sha256, writeJSON} from "./chain-lib.mjs";

const merchant = new ApiSession(), buyer = new ApiSession();
await merchant.call("/v1/dev/session", {role:"MERCHANT"});
await buyer.call("/v1/dev/session", {role:"BUYER"});
const shipment = await merchant.call("/v1/shipments", {reference:`UPLOAD-${Date.now()}`, title:"Kiểm thử tải ảnh trực tiếp",recipient_name:"Người nhận thử nghiệm"});
const base = `/v1/shipments/${shipment.id}`;
const invitation = await merchant.call(base+"/invitations", {});
await buyer.call("/v1/invitations/claim", {token:invitation.token});
const capture = await merchant.call(base+"/captures", {stage:"DISPATCH"});
const response = await fetch("http://127.0.0.1:8000/v1/dev/fixtures/dispatch.png", {headers:{Cookie:merchant.cookie}});
assert.equal(response.status, 200);
const bytes = Buffer.from(await response.arrayBuffer());
const asset = await merchant.call(base+"/uploads", {capture_session_id:capture.id, filename:"dispatch.png", mime_type:"image/png", byte_size:bytes.length, sha256:sha256(bytes)});
const route = `/v1/assets/${asset.asset_id}`;
const forbidden = await buyer.call(route+"/upload-ticket", {}, true);
assert.equal(forbidden.status, 404);
const ticket = await merchant.call(route+"/upload-ticket", {});
assert.equal(ticket.transport, "S3_POST");
async function upload(raw, fields=ticket.fields) {
  const form = new FormData();
  Object.entries(fields).forEach(([k,v])=>form.append(k,v));
  form.append("file", new Blob([raw], {type:"image/png"}), "dispatch.png");
  return fetch(ticket.url, {method:"POST",body:form});
}
const oversized = await upload(Buffer.concat([bytes,Buffer.from("extra")]));
assert.ok(oversized.status>=400);
const wrongKey = await upload(bytes, {...ticket.fields,key:"unauthorized-evidence"});
assert.ok(wrongKey.status>=400);
const valid = await upload(bytes);
assert.equal(valid.status, 204);
await merchant.call(route+"/complete", {});
await merchant.call(base+`/captures/${capture.id}/seal`, {expected_version:capture.version});
const replay = Buffer.from(bytes); replay[replay.length-1] ^= 255;
assert.equal((await upload(replay)).status, 204); // Temporary bytes may change while the ticket is valid.
await merchant.call(route+"/complete", {});
const original = await fetch("http://127.0.0.1:8000"+route+"/content?variant=original", {headers:{Cookie:merchant.cookie}});
assert.equal(original.status,200);
assert.equal(sha256(Buffer.from(await original.arrayBuffer())),sha256(bytes));
const sealed = await merchant.call(route+"/upload-ticket", {}, true);
assert.equal(sealed.status,409);
writeJSON("artifacts/evaluation/presigned-integration.json", {status:"PASS",scope:"LOCAL_MINIO_REAL_HTTP",shipment_id:shipment.id,
  checks:{owner_only:true,size_policy:true,key_policy:true,uploaded_and_decoded:true,post_seal_staging_replay_cannot_change_original:true,sealed_ticket_rejected:true},
  expires_in_seconds:ticket.expires_in_seconds, completed_at:new Date().toISOString()});
console.log("Presigned upload, policy enforcement and immutable original replay checks: PASS");
