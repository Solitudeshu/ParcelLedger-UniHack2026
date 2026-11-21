"use client";

import {useCallback, useEffect, useState} from "react";
import {ExternalLink, RefreshCw, ShieldCheck, Wallet} from "lucide-react";
import {formatUnits, keccak256, parseUnits, stringToHex, type Address, type Hex} from "viem";
import {api, post, shortHash, type Capture, type Report, type Shipment} from "@/lib/api";
import {connectWallet, sendContract, signIntent, walletError} from "@/lib/wallet";
import {ShipmentEscrowAbi} from "../../../../packages/chain/src/ShipmentEscrow";
import {EscrowFactoryAbi} from "../../../../packages/chain/src/EscrowFactory";
import {DemoTokenAbi} from "../../../../packages/chain/src/DemoToken";
import {ArbitrationPanel} from "./arbitration-panel";

type Intent = {id: string; kind: "TERMS"|"REPORT"|"SETTLEMENT"; valid: boolean; typed_data: Record<string, unknown>;
  contract_payload: Record<string, string|number>; expires_at: string; report_version: number;
  signatures: {wallet_address: string; signature: Hex}[]};
type Snapshot = {address: Address; state: string; chain_timestamp: number; terms: Record<string,string>; dispatchHash: Hex; receiptHash: Hex;
  evidenceRoot: Hex; acceptedReportHash: Hex; acceptedReportVersion: string; totalFunded: string; totalWithdrawn: string;
  merchantClaimable: string; buyerClaimable: string; accounting_consistent: boolean};
type Status = {available: boolean; chain_id: number; network_label: string; contracts: Record<string,Address>;
  parties?: {merchant: Address; buyer: Address}; case: Snapshot|null; intents: Intent[]; pending_reason?: string};
const ZERO = "0x"+"00".repeat(32);
const intentText = {
  TERMS: {title: "Điều khoản ký quỹ", sign: "Chấp nhận điều khoản bằng ví", signed: "Bạn đã chấp nhận điều khoản", submit: "Kích hoạt ký quỹ"},
  REPORT: {title: "Phiên bản dùng cho ký quỹ", sign: "Xác nhận phiên bản bằng ví", signed: "Ví của bạn đã xác nhận phiên bản này", submit: "Ghi nhận phiên bản vào hợp đồng"},
  SETTLEMENT: {title: "Phương án chia tiền", sign: "Duyệt chia tiền", signed: "Ví của bạn đã duyệt cách chia này", submit: "Gửi phương án để phân bổ quỹ"},
};
const states: Record<string,string> = {"0":"Chờ nạp quỹ", "1":"Chờ hai bên chấp nhận", "2":"Đang bảo đảm", "3":"Đang tranh chấp", "4":"Đã phân bổ", "5":"Đã hủy"};

export function ChainPanel({shipment, captures, report, busy, onAction, onChanged}: {
  shipment: Shipment; captures: Capture[]; report?: Report; busy: boolean;
  onAction: (label: string, fn:()=>Promise<void>)=>Promise<void>; onChanged:()=>Promise<void>;
}) {
  const [status, setStatus] = useState<Status|null>(null);
  const [wallet, setWallet] = useState<Address|null>(null);
  const [loadError, setLoadError] = useState("");
  const [buyerAmount, setBuyerAmount] = useState("0");
  const [lastTransaction, setLastTransaction] = useState<Hex|null>(null);
  const [expanded, setExpanded] = useState(false);
  const refresh = useCallback(async()=>{
    const [chain, own] = await Promise.all([api<Status>(`/v1/shipments/${shipment.id}/chain`), api<{address: Address|null}>("/v1/wallet")]);
    setStatus(chain); setWallet(own.address); setLoadError("");
  },[shipment.id]);
  useEffect(()=>{refresh().catch(e=>setLoadError(e.message));},[refresh]);
  useEffect(()=>{
    if (!expanded || !status?.case || ["4","5"].includes(status.case.state)) return;
    const timer = setInterval(()=>refresh().catch(e=>setLoadError(e.message)), 15000);
    return ()=>clearInterval(timer);
  },[expanded, status?.case?.state, refresh]);
  const execute = (label: string, fn:()=>Promise<void>)=>onAction(label, async()=>{
    try {await fn(); await refresh(); await onChanged();} catch(error) {throw walletError(error);}
  });
  const transaction = async(address: Address, abi: typeof ShipmentEscrowAbi|typeof DemoTokenAbi|typeof EscrowFactoryAbi, fn: string, args: readonly unknown[]=[])=>{
    if (!wallet || !status) throw new Error("Cần liên kết ví trước khi thao tác.");
    setLastTransaction(await sendContract(status.chain_id, wallet, address, abi, fn, args));
  };
  const isMerchant = shipment.current_role === "MERCHANT";
  const chainCase = status?.case;
  const reserve = BigInt(shipment.reserve_amount);
  const buyerShare = /^\d+(\.\d{1,6})?$/.test(buyerAmount) ? parseUnits(buyerAmount, 6) : null;
  const validShare = buyerShare !== null && buyerShare <= reserve;
  const acceptedVersion = !!report && chainCase?.acceptedReportHash === "0x"+report.canonical_hash
    && Number(chainCase.acceptedReportVersion) === report.version;
  const acceptanceExpired = !!chainCase && chainCase.chain_timestamp > Number(chainCase.terms.acceptBy);
  const reviewExpired = !!chainCase && chainCase.chain_timestamp > Number(chainCase.terms.reviewDeadline);
  const dispatch = captures.find(c=>c.stage === "DISPATCH" && c.status === "SEALED");
  const receipt = captures.find(c=>c.manifest_hash === report?.document.receipt_manifest_hash) || captures.find(c=>c.stage === "RECEIPT" && c.status === "SEALED");
  const createIntent = async(kind: Intent["kind"])=>{
    const amount = kind === "SETTLEMENT" ? parseUnits(buyerAmount, 6).toString() : "0";
    await post(`/v1/shipments/${shipment.id}/chain/intents`, {kind, buyer_amount: amount});
  };
  const currentIntents = status?.intents.filter(intent=>intent.valid) || [];
  const canCreate = (kind: Intent["kind"])=>!currentIntents.some(intent=>intent.kind === kind);
  return <section className="chain-card chain-interactive"><ShieldCheck size={24}/><h3>Ký quỹ của kiện hàng</h3>
    <p>Khoản ký quỹ dự kiến: <strong>{formatUnits(reserve,6)} kcTEST</strong>. Hai bên dùng ví để xác nhận phiên bản biên bản và duyệt cách chia quỹ.</p>
    <div className="chain-state"><span className="network-dot"/>{status?.network_label || "Đang đọc cấu hình mạng"}</div>
    <p className="fine">kcTEST là token thử nghiệm, không có giá trị tiền thật.</p>
    {loadError && <p role="alert" className="chain-error">{loadError}</p>}
    {status && !status.available && <p>Hợp đồng chưa triển khai trên mạng đã chọn.</p>}
    <button className="button secondary full" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded ? "Thu gọn" : "Mở quy trình ký quỹ"}</button>
    {expanded && <div className="chain-controls">
      <button className="button secondary full" disabled={busy || !status?.available} onClick={()=>execute("Liên kết ví…",async()=>{await connectWallet(status!.chain_id);})}><Wallet size={15}/>{wallet ? "Kết nối ví đã liên kết" : "Kết nối và liên kết ví"}</button>
      {wallet && <code title={wallet}>Ví: {shortHash(wallet)}</code>}
      <button className="button ghost" disabled={busy} onClick={()=>execute("Đọc trạng thái chain…", refresh)}><RefreshCw size={14}/>Cập nhật trạng thái</button>
      {status?.pending_reason === "PARTY_WALLETS_REQUIRED" && <p>Cả hai bên cần liên kết ví để bắt đầu.</p>}
      {status?.parties && <><div className="hash-row"><span>Người gửi</span><code>{shortHash(status.parties.merchant)}</code></div><div className="hash-row"><span>Người nhận</span><code>{shortHash(status.parties.buyer)}</code></div></>}
      {status?.parties && !chainCase && isMerchant && <button className="button primary full" disabled={busy} onClick={()=>execute("Tạo khoản bảo đảm…",async()=>{
        const proposal = await post<{terms: Record<string,string>; factory: Address}>(`/v1/shipments/${shipment.id}/chain/terms`);
        await transaction(proposal.factory, EscrowFactoryAbi, "createCase", [proposal.terms]);
      })}>Tạo quỹ {formatUnits(BigInt(shipment.reserve_amount),6)} kcTEST</button>}
      {chainCase && <>
        <strong>{states[chainCase.state]}</strong><code title={chainCase.address}>Hợp đồng: {shortHash(chainCase.address)}</code>
        <p className="fine">Hạn chấp nhận: {new Date(Number(chainCase.terms.acceptBy)*1000).toLocaleString("vi-VN")}<br/>Hạn kiểm tra: {new Date(Number(chainCase.terms.reviewDeadline)*1000).toLocaleString("vi-VN")}</p>
        {(["0","1"].includes(chainCase.state) && (acceptanceExpired || (chainCase.state === "0" && isMerchant))) && <div className="chain-intent"><p>{acceptanceExpired ? "Đã hết hạn chấp nhận. Khoản đã nạp sẽ được trả về quyền rút của người gửi." : "Quỹ chưa nạp có thể hủy ngay."}</p><button className="button secondary full" disabled={busy || !wallet} onClick={()=>execute("Hủy hồ sơ chưa kích hoạt…",async()=>{await transaction(chainCase.address,ShipmentEscrowAbi,"cancelUnactivated");})}>Hủy khoản bảo đảm</button></div>}
        {chainCase.state === "2" && reviewExpired && <div className="chain-intent"><p>Đã hết hạn kiểm tra và không mở tranh chấp. Theo điều khoản đã ký, toàn bộ quỹ được trả về quyền rút của người gửi.</p><button className="button secondary full" disabled={busy || !wallet} onClick={()=>execute("Đóng hồ sơ quá hạn…",async()=>{await transaction(chainCase.address,ShipmentEscrowAbi,"closeExpiredUncontested");})}>Đóng hồ sơ quá hạn</button></div>}
        {status!.chain_id === 43113 && <a href={`https://testnet.snowtrace.io/address/${chainCase.address}`} target="_blank" rel="noreferrer">Xem hợp đồng<ExternalLink size={12}/></a>}
        <div className="hash-row"><span>Đã nạp</span><strong>{formatUnits(BigInt(chainCase.totalFunded),6)} kcTEST</strong></div>
        <div className="hash-row"><span>Đã rút</span><strong>{formatUnits(BigInt(chainCase.totalWithdrawn),6)} kcTEST</strong></div>
        {!chainCase.accounting_consistent && <p role="alert" className="chain-error">Số dư chưa đối chiếu được. Chưa nên tiếp tục.</p>}
        {isMerchant && chainCase.state === "0" && !acceptanceExpired && <button className="button primary full" disabled={busy} onClick={()=>execute("Nạp token thử nghiệm…",async()=>{
          await transaction(status!.contracts.DemoToken,DemoTokenAbi,"faucet");
          await transaction(status!.contracts.DemoToken,DemoTokenAbi,"approve",[chainCase.address,BigInt(shipment.reserve_amount)]);
          await transaction(chainCase.address,ShipmentEscrowAbi,"fund");
        })}>Nhận và nạp kcTEST</button>}
        {isMerchant && !acceptanceExpired && ["0","1"].includes(chainCase.state) && chainCase.dispatchHash === ZERO && dispatch?.manifest_hash && <button className="button secondary full" disabled={busy} onClick={()=>execute("Ghi nhận ảnh gửi…",async()=>{await transaction(chainCase.address,ShipmentEscrowAbi,"commitDispatch",["0x"+dispatch.manifest_hash]);})}>Ghi nhận bộ ảnh gửi</button>}
        {chainCase.state === "1" && !acceptanceExpired && chainCase.dispatchHash !== ZERO && canCreate("TERMS") && <button className="button secondary full" disabled={busy} onClick={()=>execute("Chuẩn bị điều khoản…",()=>createIntent("TERMS"))}>Chuẩn bị ký điều khoản</button>}
        {!isMerchant && (chainCase.state === "3" || (chainCase.state === "2" && !reviewExpired)) && chainCase.receiptHash === ZERO && receipt?.manifest_hash && <button className="button secondary full" disabled={busy} onClick={()=>execute("Ghi nhận ảnh nhận…",async()=>{await transaction(chainCase.address,ShipmentEscrowAbi,"commitReceipt",["0x"+receipt.manifest_hash]);})}>Ghi nhận bộ ảnh nhận</button>}
        {(chainCase.state === "3" || (chainCase.state === "2" && !reviewExpired)) && chainCase.receiptHash !== ZERO && report && !acceptedVersion && canCreate("REPORT") && <button className="button secondary full" disabled={busy} onClick={()=>execute("Chuẩn bị yêu cầu xác nhận…",()=>createIntent("REPORT"))}>Tạo yêu cầu xác nhận phiên bản {report.version}</button>}
        {acceptedVersion && <p>Hợp đồng đã ghi nhận biên bản phiên bản {report.version}.</p>}
        {(chainCase.state === "3" || (chainCase.state === "2" && !reviewExpired)) && acceptedVersion && canCreate("SETTLEMENT") &&
          <div className="chain-allocation">
            <label>Phần cho người nhận (kcTEST)<input inputMode="decimal" maxLength={80} value={buyerAmount} onChange={e=>setBuyerAmount(e.target.value)}/></label>
            <p aria-live="polite">{validShare ? `Phần còn lại cho người gửi: ${formatUnits(reserve-buyerShare,6)} kcTEST.` : `Nhập từ 0 đến ${formatUnits(reserve,6)} kcTEST, tối đa 6 chữ số thập phân.`}</p>
            <button className="button primary full" disabled={busy || !validShare} onClick={()=>execute("Lập phương án chia tiền…",()=>createIntent("SETTLEMENT"))}>Đề xuất cách chia tiền</button>
          </div>}
        {currentIntents.map(intent=>{const signed = intent.signatures.some(signature=>signature.wallet_address === wallet?.toLowerCase());return <div className="chain-intent" key={intent.id}>
          <h4>{intentText[intent.kind].title}</h4><p>{intent.kind === "TERMS" ? `Quỹ ${formatUnits(reserve,6)} kcTEST · hạn kiểm tra ${new Date(Number(intent.contract_payload.reviewDeadline)*1000).toLocaleString("vi-VN")}` : `Biên bản phiên bản ${intent.report_version} · ${shortHash(String(intent.contract_payload.reportHash))}`}</p>
          {intent.kind === "REPORT" && <p>Hai ví xác nhận mã của phiên bản dùng làm căn cứ. Tiền vẫn nằm trong quỹ ở bước này.</p>}
          {intent.kind === "SETTLEMENT" && <p>Người nhận: <strong>{formatUnits(BigInt(intent.contract_payload.buyerAmount),6)} kcTEST</strong><br/>Người gửi: {formatUnits(BigInt(intent.contract_payload.merchantAmount),6)} kcTEST</p>}
          <p className="fine">{intent.signatures.length}/2 chữ ký · hết hạn {new Date(intent.expires_at).toLocaleTimeString("vi-VN")}</p>
          <details><summary>Nội dung sẽ ký</summary><pre>{JSON.stringify(intent.typed_data,null,2)}</pre></details>
          <button className="button secondary full" disabled={busy || signed || !wallet} onClick={()=>execute("Kiểm tra và ký trong ví…",async()=>{
            const signature = await signIntent(wallet!,intent.typed_data);
            await post(`/v1/shipments/${shipment.id}/chain/intents/${intent.id}/signatures`,{signature});
          })}>{signed ? intentText[intent.kind].signed : intentText[intent.kind].sign}</button>
          {intent.signatures.length === 2 && <button className="button primary full" disabled={busy} onClick={()=>execute("Gửi hai chữ ký lên chain…",async()=>{
            const signatures = [status!.parties!.merchant,status!.parties!.buyer].map(address=>intent.signatures.find(signature=>signature.wallet_address === address.toLowerCase())!.signature);
            await transaction(chainCase.address,ShipmentEscrowAbi,intent.kind === "TERMS" ? "activate" : intent.kind === "REPORT" ? "acceptReport" : "settle", [intent.kind === "TERMS" ? intent.contract_payload.expiresAt : intent.contract_payload,...signatures]);
          })}>{intentText[intent.kind].submit}</button>}
        </div>;})}
        {BigInt(isMerchant ? chainCase.merchantClaimable : chainCase.buyerClaimable)>0n && <button className="button primary full" disabled={busy} onClick={()=>execute("Rút phần quỹ của bạn…",async()=>{await transaction(chainCase.address,ShipmentEscrowAbi,"withdraw");})}>Rút {formatUnits(BigInt(isMerchant ? chainCase.merchantClaimable : chainCase.buyerClaimable),6)} kcTEST</button>}
        {chainCase.state === "2" && !reviewExpired && report && <button className="button secondary full" disabled={busy} onClick={()=>execute("Mở tranh chấp…",async()=>{await transaction(chainCase.address,ShipmentEscrowAbi,"openDispute",["0x"+report.canonical_hash]);})}>Mở tranh chấp để phân xử</button>}
        {(chainCase.state === "3" || (chainCase.state === "2" && !reviewExpired)) && report && <button className="button ghost full" disabled={busy} onClick={()=>execute("Vô hiệu chữ ký cũ trên chain…",async()=>{
          const amendment = keccak256(stringToHex(JSON.stringify({kind:"REPORT_REVISION_REQUEST",report_hash:report.canonical_hash,nonce:crypto.randomUUID()})));
          await transaction(chainCase.address,ShipmentEscrowAbi,"appendEvidence",[amendment]);
        })}>Vô hiệu xác nhận bằng ví để sửa biên bản</button>}
      </>}
      {chainCase?.state === "3" && <ArbitrationPanel shipmentId={shipment.id} chainId={status!.chain_id} escrow={chainCase.address} busy={busy} onAction={execute} onChanged={onChanged}/>}
      {lastTransaction && <p className="fine">Giao dịch đã xác nhận: {status?.chain_id === 43113 ? <a href={`https://testnet.snowtrace.io/tx/${lastTransaction}`} target="_blank" rel="noreferrer">{shortHash(lastTransaction)}</a> : <code>{shortHash(lastTransaction)}</code>}</p>}
    </div>}
  </section>;
}
