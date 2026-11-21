import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import {sha256} from "./chain-lib.mjs";

export class ApiSession {
  cookie = ""; csrf = "";
  constructor(baseUrl = "http://127.0.0.1:8000") {
    this.baseUrl = baseUrl;
  }
  async call(path, body, allowError=false) {
    const response = await fetch(this.baseUrl+path, {method: body === undefined ? "GET" : "POST",
      headers: {"Content-Type": "application/json", "Cookie": this.cookie, "X-CSRF-Token": this.csrf,
        "Idempotency-Key": randomUUID()}, body: body === undefined ? undefined : JSON.stringify(body)});
    if (response.headers.get("set-cookie")) this.cookie = response.headers.get("set-cookie").split(";")[0];
    const value = await response.json();
    if (value.csrf_token) this.csrf = value.csrf_token;
    if (!response.ok && !allowError) throw new Error(`${path}: ${JSON.stringify(value)}`);
    return allowError ? {status: response.status, value} : value;
  }
  async upload(sid, stage, filename) {
    const capture = await this.call(`/v1/shipments/${sid}/captures`, {stage});
    const original = await fetch(`${this.baseUrl}/v1/dev/fixtures/${filename}`, {headers: {Cookie: this.cookie}});
    assert.equal(original.status, 200);
    const raw = Buffer.from(await original.arrayBuffer());
    const intent = await this.call(`/v1/shipments/${sid}/uploads`, {capture_session_id: capture.id,
      filename, mime_type: "image/png", byte_size: raw.length, sha256: sha256(raw)});
    const uploaded = await fetch(this.baseUrl+intent.upload_url, {method: "PUT", body: raw,
      headers: {Cookie: this.cookie, "X-CSRF-Token": this.csrf, "Content-Type": "application/octet-stream"}});
    assert.equal(uploaded.status, 200);
    await this.call(`/v1/assets/${intent.asset_id}/complete`, {});
    const sealed = await this.call(`/v1/shipments/${sid}/captures/${capture.id}/seal`, {expected_version: capture.version});
    return {id: intent.asset_id, manifestHash: "0x"+sealed.manifest_hash};
  }
  async bind(wallet) {
    const challenge = await this.call("/v1/wallet/challenge", {address: wallet.account.address});
    const signature = await wallet.signTypedData(challenge.typed_data);
    return this.call("/v1/wallet/verify", {challenge_id: challenge.id, signature});
  }
}
