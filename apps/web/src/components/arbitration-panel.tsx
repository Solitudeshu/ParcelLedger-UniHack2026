"use client";

import {useCallback, useEffect, useState} from "react";
import {formatUnits, parseUnits, type Address, type Hex} from "viem";
import {api, post, shortHash} from "@/lib/api";
import {requestWallet, sendContract, signIntent} from "@/lib/wallet";
import {ShipmentEscrowAbi} from "../../../../packages/chain/src/ShipmentEscrow";

type Proposal = {id: string; valid: boolean; ready: boolean; safe_signature: Hex;
  decision: {text: string; report_hash: string; evidence_root: string; merchant_amount: string; buyer_amount: string};
  resolution: Record<string,string|number>; typed_data: Record<string,unknown>;
  signatures: {wallet_address: string; signature: Hex}[]};
type Arbitration = {safe: {address: Address; owners: string[]; threshold: number}|null; proposals: Proposal[]};

export function ArbitrationPanel({shipmentId, chainId, escrow, busy, onAction, onChanged}: {
  shipmentId: string; chainId: number; escrow: Address; busy: boolean;
  onAction: (label: string, fn:()=>Promise<void>)=>Promise<void>; onChanged:()=>Promise<void>;
}) {
  const [data, setData] = useState<Arbitration|null>(null);
  const [amount, setAmount] = useState("0");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const refresh = useCallback(async()=>{setData(await api<Arbitration>(`/v1/shipments/${shipmentId}/arbitration`)); setError("");},[shipmentId]);
  useEffect(()=>{refresh().catch(e=>setError(e.message));},[refresh]);
  async function execute(label: string, fn:()=>Promise<void>) {await onAction(label,async()=>{await fn(); await refresh(); await onChanged();});}
  return <div className="arbitration-panel"><h4>Quyết định của hội đồng Safe</h4>
    <p>Người phân xử đọc ảnh và biên bản, viết căn cứ rồi ký bằng các ví thành viên. AI không đề xuất số tiền.</p>
    {error && <p role="alert">{error}</p>}
    {data?.safe && <p className="fine">Cần {data.safe.threshold}/{data.safe.owners.length} ví · Safe {shortHash(data.safe.address)}. Tài khoản đang mở chỉ chuyển tiếp chữ ký; không được thay chữ ký thành viên.</p>}
    <button className="button ghost full" disabled={busy} onClick={()=>execute("Đọc chữ ký phân xử…",refresh)}>Cập nhật chữ ký phân xử</button>
    <label>Phần cho người nhận khi phân xử (kcTEST)<input inputMode="decimal" value={amount} onChange={e=>setAmount(e.target.value)}/></label>
    <label>Căn cứ quyết định của người phân xử<textarea minLength={20} maxLength={5000} value={reason} onChange={e=>setReason(e.target.value)} placeholder="Nêu bằng chứng đã xem và lý do phân bổ…"/></label>
    <button className="button secondary full" disabled={busy || reason.trim().length<20 || !/^\d+(\.\d{1,6})?$/.test(amount)} onClick={()=>execute("Lưu phương án phân xử…",async()=>{
      await post(`/v1/shipments/${shipmentId}/arbitration`,{buyer_amount:parseUnits(amount,6).toString(),decision_text:reason});
      setReason("");
    })}>Lưu phương án để hội đồng kiểm tra</button>
    {data?.proposals.map(proposal=><div className="chain-intent" key={proposal.id}>
      <strong>{proposal.valid ? "Chờ hội đồng quyết định" : "Phương án đã hết hiệu lực"}</strong>
      <p className="decision-text">{proposal.decision.text}</p>
      <p>Người nhận: <strong>{formatUnits(BigInt(proposal.decision.buyer_amount),6)} kcTEST</strong><br/>Người gửi: {formatUnits(BigInt(proposal.decision.merchant_amount),6)} kcTEST</p>
      <p className="fine">Biên bản: {shortHash(proposal.decision.report_hash)}<br/>Hết hạn ký: {new Date(Number(proposal.resolution.expiresAt)*1000).toLocaleString("vi-VN")}</p>
      <p>{proposal.signatures.length}/{data.safe?.threshold} chữ ký cần thiết</p>
      <details><summary>Kiểm tra nội dung và chữ ký</summary><pre>{JSON.stringify(proposal,null,2)}</pre></details>
      {proposal.valid && <button className="button secondary full" disabled={busy} onClick={()=>execute("Ký quyết định với ví thành viên Safe…",async()=>{
        const account=await requestWallet(chainId);
        if (!data.safe?.owners.includes(account.toLowerCase())) throw new Error("Hãy chọn một ví thành viên của Safe phân xử.");
        if (proposal.signatures.some(signature=>signature.wallet_address===account.toLowerCase())) throw new Error("Ví này đã ký phương án. Cần chữ ký của thành viên khác.");
        const signature=await signIntent(account,proposal.typed_data);
        await post(`/v1/shipments/${shipmentId}/arbitration/${proposal.id}/signatures`,{signature});
      })}>Ký với ví thành viên Safe</button>}
      {proposal.ready && <button className="button primary full" disabled={busy} onClick={()=>execute("Thực hiện quyết định đã đủ chữ ký…",async()=>{
        const account=await requestWallet(chainId);
        await sendContract(chainId,account,escrow,ShipmentEscrowAbi,"resolveDispute",[proposal.resolution,proposal.safe_signature]);
      })}>Gửi quyết định đã đủ chữ ký</button>}
    </div>)}
  </div>;
}
