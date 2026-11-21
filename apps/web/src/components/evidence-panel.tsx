"use client";

import { useRef, useState } from "react";
import { ArrowRight, Camera, Check, Crop, ExternalLink, Focus, LoaderCircle, RotateCcw, ScanLine, Sparkles } from "lucide-react";
import { Asset, Capture, imageUrl, Job, post, reasonLabels, Report, Shipment } from "@/lib/api";
import { ResearchTransformerPanel } from "./research-transformer-panel";

type Point = [number, number];
type Props = {captures: Capture[]; latestJob?: Job; shipment: Shipment; assets: Asset[]; report?: Report; activeJob?: Job; busy: boolean;
  onAction: (label: string, fn: () => Promise<void>) => Promise<void>; onChanged: () => Promise<void>; onReport: () => void};

export function EvidencePanel({captures, latestJob, shipment, assets, report, activeJob, busy, onAction, onChanged, onReport}: Props) {
  const sealed = new Map(captures.filter(c => c.status === "SEALED").map(c => [c.id, c]));
  const ready = assets.filter(a => a.status === "READY" && sealed.has(a.capture_session_id));
  const queries = ready.filter(a => a.stage === "RECEIPT" || (a.stage === "SUPPLEMENT" && sealed.get(a.capture_session_id)?.manifest?.actor_role === "BUYER"));
  const references = ready.filter(a => a.stage === "DISPATCH");
  const shopExtras = ready.filter(a => a.stage === "SUPPLEMENT" && sealed.get(a.capture_session_id)?.manifest?.actor_role === "MERCHANT");
  const [choice, setQueryId] = useState("");
  const queryId = queries.find(a => a.id === choice)?.id ?? queries.find(a => a.id === report?.document.match.query_asset_id)?.id
    ?? queries.find(a => a.stage === "RECEIPT" && !a.parent_overview_id)?.id ?? queries.at(-1)?.id ?? "";
  const [referenceOverride, setReferenceOverride] = useState("");
  const [region, setRegion] = useState<Point[] | null>(null);
  const [cropping, setCropping] = useState(false);
  const start = useRef<{point: Point; id: number} | null>(null);
  const [draft, setDraft] = useState<Point[] | null>(null);
  const match = report?.document.match;
  const researchChange = report?.document.research_change;
  const researchTransformer = report?.document.research_transformer;
  const shownTransformer = !activeJob && researchTransformer?.query_asset_id === queryId
    && (researchTransformer.requested_reference_asset_id ?? "") === referenceOverride
    && JSON.stringify(region) === JSON.stringify(researchTransformer.query_region ?? null)
    ? researchTransformer : undefined;
  const shownMatch = match?.query_asset_id === queryId
    && (!referenceOverride || referenceOverride === match.reference_asset_id)
    && JSON.stringify(region) === JSON.stringify(match.query_region ?? null) ? match : undefined;
  const referenceId = referenceOverride || shownMatch?.reference_asset_id;
  const queryPolygons = draft ? [draft] : region ? [region] : (shownMatch ? report?.document.observations.flatMap(o => o.query_polygon ? [o.query_polygon as Point[]] : []) || [] : []);

  function point(e: React.PointerEvent<HTMLDivElement>): Point {
    const rect = e.currentTarget.getBoundingClientRect();
    return [Math.max(0, Math.min(10000, Math.round((e.clientX - rect.left) / rect.width * 10000))),
      Math.max(0, Math.min(10000, Math.round((e.clientY - rect.top) / rect.height * 10000)))];
  }
  function rectangle(e: React.PointerEvent<HTMLDivElement>): Point[] | null {
    if (!start.current || start.current.id !== e.pointerId) return null;
    const s = start.current.point, end = point(e);
    const [left, right] = [Math.min(s[0],end[0]), Math.max(s[0],end[0])];
    const [top, bottom] = [Math.min(s[1],end[1]), Math.max(s[1],end[1])];
    if (right-left < 100 || bottom-top < 100) return null;
    return [[left,top],[right,top],[right,bottom],[left,bottom]];
  }
  function cancelSelection() { start.current = null; setDraft(null); setCropping(false); }

  return <section className="panel evidence-panel"><div className="section-top"><div><h2>So sánh ảnh lúc gửi và lúc nhận</h2><p>{shownMatch?.status === "MATCHED_SUFFICIENT" ? "Xem cùng một mặt kiện ở hai thời điểm, rồi mở biên bản để đọc nhận xét." : referenceOverride ? "Hệ thống sẽ so sánh ảnh nhận với ảnh gửi bạn đã chọn." : "Bắt đầu từ ảnh người nhận chụp. Khi bạn bấm đối chiếu, hệ thống sẽ tìm ảnh cùng mặt kiện trong bộ ảnh của cửa hàng."}</p></div><span className="icon-tile small"><ScanLine size={20}/></span></div>
    {!queries.length ? <div className="empty-evidence"><Focus size={35}/><h3>Chờ ảnh lúc nhận kiện</h3><p>Người nhận thêm ảnh qua liên kết của hồ sơ, sau đó khóa bộ ảnh để bắt đầu.</p></div> : <>

      <div className="comparison parcel-pair"><div className="comparison-side"><div className="comparison-label">Cửa hàng chụp trước khi gửi</div>{referenceId ? <div className="evidence-image"><img src={imageUrl(referenceId)} alt="Ảnh trước khi gửi được chọn làm tham chiếu"/>{!referenceOverride && shownMatch?.reference_region && <Overlay polygons={[shownMatch.reference_region as Point[]]} color="green"/>}<a className="image-expand" href={`${imageUrl(referenceId)}?variant=original`} aria-label="Tải ảnh gửi gốc" target="_blank" rel="noreferrer"><ExternalLink size={15}/></a></div> : <div className="reference-placeholder"><Focus size={28}/><p>{shownMatch ? "Chưa tìm được ảnh gửi chụp cùng mặt kiện." : "Ảnh cửa hàng sẽ hiện ở đây sau khi đối chiếu."}</p></div>}
        <label className="photo-choice">Ảnh gửi để so sánh<select value={referenceOverride} onChange={e => setReferenceOverride(e.target.value)}>
          <option value="">Tự tìm ảnh cùng mặt kiện</option>{references.map((a,i) => <option key={a.id} value={a.id} title={a.filename}>Ảnh gửi {i+1}{a.parent_overview_id ? " · Cận cảnh" : ""}</option>)}</select></label></div>
        <div className="comparison-side"><div className="comparison-label">Người nhận chụp khi nhận kiện</div><div className={`evidence-image ${cropping ? "cropping" : ""}`} onPointerDown={e => {if (cropping && !start.current && e.button === 0) {start.current = {point: point(e), id: e.pointerId}; e.currentTarget.setPointerCapture(e.pointerId);}}}
          onPointerMove={e => {if (start.current?.id === e.pointerId) setDraft(rectangle(e));}}
          onPointerUp={e => {if (start.current?.id === e.pointerId) {setRegion(rectangle(e)); cancelSelection();}}}
          onPointerCancel={cancelSelection} onLostPointerCapture={cancelSelection}><img src={imageUrl(queryId)} alt="Ảnh lúc người nhận kiểm tra kiện hàng" draggable={false}/><Overlay polygons={queryPolygons} color="orange"/>{!cropping && <a className="image-expand" href={`${imageUrl(queryId)}?variant=original`} aria-label="Tải ảnh nhận gốc" target="_blank" rel="noreferrer"><ExternalLink size={15}/></a>}</div>
          {queries.length > 1 ? <label className="photo-choice">Ảnh nhận cần kiểm tra
            <select value={queryId} onChange={e => {setQueryId(e.target.value); setRegion(null); cancelSelection();}}>
              {queries.map((a,i) => <option key={a.id} value={a.id} title={a.filename}>Ảnh nhận {i+1}{a.parent_overview_id ? " · Cận cảnh" : a.stage === "SUPPLEMENT" ? " · Bổ sung" : ""}</option>)}</select></label> :
            <div className="photo-choice">Ảnh nhận cần kiểm tra<span className="single-photo">Đã chọn ảnh nhận duy nhất</span></div>}</div></div>
      <div className="region-controls"><button className={`button secondary ${cropping ? "selected" : ""}`} aria-pressed={cropping} onClick={() => cropping ? cancelSelection() : setCropping(true)}><Crop size={16}/>{cropping ? "Hủy khoanh vùng" : "Khoanh vùng trên ảnh nhận"}</button>{region && <button className="button ghost" onClick={() => setRegion(null)}><RotateCcw size={14}/>Dùng toàn ảnh</button>}
        <span className="fine">{cropping ? "Kéo trên ảnh người nhận để đánh dấu phần cần xem." : region ? "Sẽ so sánh vùng bạn đã khoanh." : "Mặc định so sánh toàn ảnh."}</span></div>
      {activeJob ? <div className="analysis-state"><LoaderCircle className="spin" size={22}/><div><strong>Đang đối chiếu ảnh</strong><p>{activeJob.progress}</p></div></div> : shownMatch && <div className={`match-result ${shownMatch.status === "MATCHED_SUFFICIENT" ? "sufficient" : "insufficient"}`}><span className="match-icon">{shownMatch.status === "MATCHED_SUFFICIENT" ? <Check/> : <Camera/>}</span><div><strong>{shownMatch.status === "MATCHED_SUFFICIENT" ? "Đã tìm được vùng tương ứng để kiểm tra" : shownMatch.status === "MATCHED_INSUFFICIENT" ? "Cùng vùng, nhưng cần ảnh rõ hơn" : "Chưa tìm được ảnh tương ứng"}</strong><p>{shownMatch.reason_codes.length ? shownMatch.reason_codes.map(c => reasonLabels[c] || c).join(" · ") : "Mở biên bản để xem từng nhận xét và xác nhận bằng mắt trên ảnh gốc."}</p>{shownMatch.status !== "MATCHED_SUFFICIENT" && <p className="recapture-tip">Chụp thêm mặt hộp có cùng băng keo hoặc nhãn nhận diện. Giữ góc cần xem rõ nét, tránh chói và bàn tay che.</p>}</div></div>}
      {shownMatch && report?.document.recapture_instructions?.length ? <div className="recapture-plan"><h3>Ảnh cần bổ sung tiếp theo</h3><ol>{report.document.recapture_instructions.map(step=><li key={step.action}><strong>{step.title}</strong><span className="fine"> · {step.responsible_party === "MERCHANT" ? "Người gửi" : "Người nhận"}</span><p>{step.instruction}</p></li>)}</ol><p className="fine">Ảnh bổ sung được lưu ở mốc mới; bộ ảnh đã khóa được giữ nguyên.</p></div> : null}
      {latestJob?.status === "FAILED" && !activeJob && <p role="alert" className="alert error">Lần đối chiếu gần nhất chưa hoàn tất. Bạn có thể gửi lại yêu cầu bên dưới.</p>}
      <div className="analysis-actions"><button className={shownMatch ? "button secondary" : "button primary"} disabled={busy || !!activeJob} onClick={() => onAction("Gửi yêu cầu đối chiếu…", async () => {await post(`/v1/shipments/${shipment.id}/analyses`, {query_asset_id: queryId, query_region: region, reference_asset_id: referenceOverride || null}); await onChanged();})}><Sparkles size={17}/>{shownMatch ? "Đối chiếu lại" : "Đối chiếu và tạo biên bản"}</button>{report && <button className={shownMatch ? "button primary" : "button secondary"} onClick={onReport}>Xem biên bản<ArrowRight size={16}/></button>}</div>
      <details className="method-details" hidden={!shownMatch && !shownTransformer}><summary>Cách đối chiếu và mô hình thử nghiệm</summary>
      {shownTransformer && <ResearchTransformerPanel result={shownTransformer}/>}
      {shownMatch && report?.document.research_advice && <details className="method-details"><summary>Thử nghiệm chọn ảnh tham chiếu</summary><p>Mô hình đã train trên ảnh mô phỏng. Kết quả dưới đây phục vụ đánh giá, chưa thay kết quả đối chiếu của biên bản.</p><p>{report.document.research_advice.status === "EVALUATED" ? `Mô hình: ${report.document.research_advice.model_version}` : "Mô hình nghiên cứu chưa trả được kết quả cho lượt này."}</p>{report.document.research_advice.suggested_reference_asset_id && <p>Ảnh gợi ý: {assets.find(a=>a.id===report.document.research_advice?.suggested_reference_asset_id)?.filename || "Ảnh trong bộ gửi"}</p>}<p className="fine">Chưa xác thực trên hộp vật lý; không dùng để quyết định trách nhiệm hoặc bồi hoàn.</p></details>}
      {shownMatch && researchChange && <details className="method-details research-change"><summary>Thử nghiệm khoanh vùng khác biệt</summary>
        <p>Kết quả từ mô hình học trên ảnh mô phỏng, hiển thị riêng để kiểm tra. Biên bản vẫn dùng phương pháp đối chiếu chính.</p>
        {researchChange.status === "EVALUATED" && researchChange.query_asset_id === queryId ? <>
          <div className="evidence-image"><img src={imageUrl(queryId)} alt="Ảnh nhận với lớp vùng thay đổi thử nghiệm"/><Overlay polygons={researchChange.query_polygons || []} color="purple"/></div>
          <p>{researchChange.query_polygons?.length ? "Vùng tím là khác biệt mô hình đề nghị xem lại trên ảnh gốc." : "Mô hình thử nghiệm chưa đánh dấu vùng nào. Điều này không xác nhận kiện hàng nguyên vẹn."}</p>
        </> : <p>{researchChange.status === "ABSTAIN" ? "Chưa đủ điều kiện đối chiếu; mô hình không đưa ra vùng thay đổi." : "Mô hình thử nghiệm chưa trả được kết quả cho lượt này."}</p>}
        <p className="fine">Chưa kiểm chứng trên hộp thật. Bóng đổ, bàn tay che hoặc vết nhỏ có thể làm kết quả sai. Không xác định trách nhiệm hoặc bồi hoàn.</p>
      </details>}

      {shownMatch && <details className="method-details"><summary>Thông tin phương pháp đối chiếu</summary><p>Baseline hình học SIFT/RANSAC, chưa qua huấn luyện và hiệu chuẩn trên dữ liệu thực. Điểm nội bộ không phải xác suất đúng.</p><code>{shownMatch.model_version}</code><p>{Math.round(shownMatch.metrics?.inliers || 0)} điểm tương ứng đồng thuận · {Math.round((shownMatch.metrics?.overlap || 0) * 100)}% vùng ảnh có phần đối chiếu.</p></details>}
      </details>
    </>}
    {shopExtras.length > 0 && <details className="method-details"><summary>Ảnh bổ sung của bên gửi ({shopExtras.length})</summary><div className="capture-gallery">{shopExtras.map(a => <a key={a.id} href={imageUrl(a.id) + "?variant=original"} target="_blank" rel="noreferrer"><img width="120" src={imageUrl(a.id)} alt={a.filename}/></a>)}</div></details>}
  </section>;
}

export function Overlay({polygons, color}: {polygons: Point[][]; color: string}) {
  if (!polygons.length) return null;
  return <svg className={`region-overlay ${color}`} viewBox="0 0 10000 10000" preserveAspectRatio="none" aria-hidden="true">{polygons.map((p, i) => {
    const points = p.map(([x, y]) => `${x},${y}`).join(" ");
    return <g key={i}>{color === "monochrome" && <polygon className="outline-halo" points={points} vectorEffect="non-scaling-stroke"/>}
      <polygon points={points} vectorEffect="non-scaling-stroke"/></g>;
  })}</svg>;
}
