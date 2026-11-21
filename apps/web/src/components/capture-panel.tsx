"use client";

import { useRef, useState } from "react";
import { Camera, Check, ImagePlus, LockKeyhole, Upload, X } from "lucide-react";
import { Asset, Capture, imageUrl, post, reasonLabels, Shipment, uploadFile } from "@/lib/api";

type Props = {viewerId: string; shipment: Shipment; captures: Capture[]; assets: Asset[]; busy: boolean;
  onAction: (label: string, fn: () => Promise<void>) => Promise<void>; onChanged: () => Promise<void>};

export function CapturePanel({viewerId, shipment, captures, assets, busy, onAction, onChanged}: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [progress, setProgress] = useState("");
  const [source, setSource] = useState("UPLOADED_ARCHIVE");
  const [parent, setParent] = useState("");
  const uploadInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const baseStage = shipment.current_role === "MERCHANT" ? "DISPATCH" : "RECEIPT";
  const sealed = captures.some(c => c.stage === baseStage && c.status === "SEALED");
  const stage = sealed ? "SUPPLEMENT" : baseStage;
  const openCapture = captures.find(c => c.actor_id === viewerId && c.stage === stage && c.status === "OPEN");
  const currentAssets = assets.filter(a => a.capture_session_id === openCapture?.id);
  const readyAssets = currentAssets.filter(a => a.status === "READY");

  async function send() {
    for (const file of files) {
      if (!["image/jpeg","image/png","image/webp"].includes(file.type)) throw new Error(`Ảnh ${file.name} cần ở định dạng JPG, PNG hoặc WebP.`);
      if (!file.size || file.size > 20 * 1024 * 1024) throw new Error(`Ảnh ${file.name} phải có dữ liệu và tối đa 20 MB.`);
    }
    const capture = openCapture || await post<Capture>(`/v1/shipments/${shipment.id}/captures`, {stage});
    try {
      for (const [index, file] of files.entries()) {
        setProgress(`Đang tải ảnh ${index + 1}/${files.length}…`);
        await uploadFile(shipment.id, capture.id, file, readyAssets.some(a => a.id === parent) ? parent : undefined, source);
        setFiles(pending => pending.slice(1));
      }
    } finally {
      setProgress("");
      await onChanged();
    }
  }

  return <section className="panel capture-panel"><div className="section-top"><div><h2><Camera size={20}/>{sealed ? "Bổ sung bằng chứng" : baseStage === "DISPATCH" ? "Ảnh trước khi gửi" : "Ảnh lúc nhận kiện"}</h2><p>{sealed ? "Bộ ảnh gốc đã khóa. Ảnh thêm được lưu trong một mốc riêng." : "Chụp tổng quan trước, rồi chụp gần mặt hoặc góc cần kiểm tra."}</p></div>{sealed && <span className="badge green"><LockKeyhole size={13}/>Bộ ảnh gốc đã khóa</span>}</div>
    <div className="capture-tips"><span><b>01</b>Toàn bộ kiện hàng</span><span><b>02</b>Rõ mặt và góc hộp</span><span><b>03</b>Cận cảnh vùng cần xem</span></div>
    <input ref={uploadInput} type="file" accept="image/jpeg,image/png,image/webp" multiple hidden onChange={e => {setSource("UPLOADED_ARCHIVE"); setFiles(Array.from(e.target.files || [])); e.target.value = "";}}/>
    <input ref={cameraInput} type="file" accept="image/*" capture="environment" hidden onChange={e => {setSource("IN_APP_CAPTURE"); setFiles(Array.from(e.target.files || [])); e.target.value = "";}}/>
    <div className="upload-zone"><div className="upload-symbol"><ImagePlus size={24}/></div><div><h3>{files.length ? `Đã chọn ${files.length} ảnh` : "Thêm ảnh của kiện hàng"}</h3><p>JPG, PNG hoặc WebP · Tối đa 20 MB/ảnh</p></div><div className="button-row"><button className="button secondary" disabled={busy} onClick={() => cameraInput.current?.click()}><Camera size={16}/>Chụp ảnh</button><button className="button secondary" disabled={busy} onClick={() => uploadInput.current?.click()}><Upload size={16}/>Chọn ảnh</button></div></div>
    {files.length > 0 && <div className="pending-files">{files.map((f, i) => <div key={`${f.name}-${i}`}><span>{f.name}<small>{(f.size / 1024 / 1024).toFixed(1)} MB</small></span><button aria-label={`Bỏ ảnh ${f.name}`} className="icon-button" disabled={busy} onClick={() => setFiles(files.filter((_, index) => index !== i))}><X size={15}/></button></div>)}
      {readyAssets.length > 0 && <label>Ảnh tổng quan liên quan (nếu có)<select value={parent} onChange={e => setParent(e.target.value)}><option value="">Không gắn ảnh tổng quan</option>{readyAssets.map(a => <option key={a.id} value={a.id}>{a.filename}</option>)}</select></label>}
      <button className="button primary" disabled={busy} onClick={() => onAction("Tải và kiểm tra ảnh…", send)}><Upload size={16}/>{busy ? progress || "Đang tải…" : "Tải ảnh lên hồ sơ"}</button></div>}
    {currentAssets.filter(a => a.status === "PENDING").map(a => <div className="pending-upload" key={a.id}><span>{a.filename} · Tải chưa xong</span>
      <button className="button ghost" disabled={busy} onClick={() => onAction("Bỏ ảnh chưa tải…", async () => {await post(`/v1/assets/${a.id}/cancel-upload`); await onChanged();})}>Bỏ ảnh chưa tải</button></div>)}
    {readyAssets.length > 0 && <><div className="capture-gallery">{readyAssets.map(a => <div className="capture-thumb" key={a.id}><img src={imageUrl(a.id)} alt={`Ảnh đã tải: ${a.filename}`}/><div><Check size={13}/><span>{a.filename}</span></div>{((a.quality.reason_codes as string[]) || []).map(code => <small className="quality-warning" key={code}>{reasonLabels[code] || code}</small>)}</div>)}</div><div className="seal-row"><p><LockKeyhole size={15}/>Khóa bộ ảnh để giữ nguyên nội dung làm bằng chứng.</p><button className="button primary" disabled={busy || files.length > 0 || currentAssets.some(a => a.status === "PENDING")} onClick={() => onAction("Khóa bộ ảnh…", async () => {await post(`/v1/shipments/${shipment.id}/captures/${openCapture!.id}/seal`, {expected_version: openCapture!.version}); await onChanged();})}>Khóa {readyAssets.length} ảnh<LockKeyhole size={15}/></button></div></>}
  </section>;
}
