"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDownToLine, ArrowLeft, ArrowRight, Box, Camera, Check, CheckCheck, ChevronRight,
  CircleHelp, Clock3, FileText, Link2, LoaderCircle, LogOut,
  Plus, Sparkles, X } from "lucide-react";
import { api, AppConfig, Asset, Capture, createDemo, demoLogin, formatTime, imageUrl,
  Job, post, Report, reportTime, Session, setSession, Shipment, shortHash, statusLabels, Timeline } from "@/lib/api";
import { ReportSignatures } from "./report-signatures";
import { CapturePanel } from "./capture-panel";
import { EvidencePanel, Overlay } from "./evidence-panel";
import { ChainPanel } from "./chain-panel";
import { EntryScreen, Logo } from "./entry-screen";
import parcelPhoto from "@/assets/luc-nhan.webp";
import { initialInbox, merchantQueues, ShipmentInbox, ShowcaseParcels } from "./shipment-inbox";
import { MerchantSidebar } from "./merchant-sidebar";
import { WorkflowMetrics } from "./workflow-metrics";

type Detail = {shipment: Shipment; captures: Capture[]; assets: Asset[]; jobs: Job[]; reports: Report[]; timeline: Timeline[]};
type Tab = "evidence" | "report" | "timeline";
const eventLabels: Record<string, string> = {
  REPORT_SIGNED: "Ký biên bản trực tuyến",
  SHIPMENT_CREATED: "Tạo hồ sơ kiện hàng", INVITATION_CREATED: "Tạo liên kết cho người nhận",
  RECIPIENT_LINKED: "Người nhận đã tham gia", CAPTURE_OPENED: "Bắt đầu ghi nhận ảnh",
  ASSET_READY: "Đã kiểm tra toàn vẹn ảnh", CAPTURE_SEALED: "Khóa bộ ảnh giao nhận",
  ANALYSIS_QUEUED: "Gửi yêu cầu đối chiếu", REPORT_DRAFTED: "Tạo bản nháp biên bản",
  REPORT_REVISED: "Lưu phiên bản biên bản mới", REPORT_REVIEW_CONFIRMED: "Xác nhận đã kiểm tra nội dung",
};

export function Workspace({joining = false}: {joining?: boolean}) {
  const [session, setUser] = useState<Session | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [inbox, setInbox] = useState(initialInbox);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("evidence");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [creating, setCreating] = useState(false);
  const [invitation, setInvitation] = useState("");
  const [joinToken, setJoinToken] = useState("");
  const [scenario, setScenario] = useState("parcel-dent");
  const [buyerView, setBuyerView] = useState(false);
  const selectedRef = useRef<string | null>(null);
  const refreshSequence = useRef(0);
  const [detailFailure, setDetailFailure] = useState("");
  const historyScope = useRef<string | null>(null);
  selectedRef.current = selected;

  const selectView = useCallback((id: string | null, nextTab: Tab = "evidence") => {
    selectedRef.current = id;
    refreshSequence.current++;
    setDetailFailure("");
    setNotice("");
    setSelected(id);
    setTab(nextTab);
    setInvitation("");
    setError("");
  }, []);

  useEffect(() => {
    function restoreView(event: PopStateEvent) {
      if (!historyScope.current) return;
      const view = event.state?.shipmentView;
      if (view?.scope === historyScope.current) selectView(view.id, view.tab);
      else selectView(null);
    }
    window.addEventListener("popstate", restoreView);
    return () => window.removeEventListener("popstate", restoreView);
  }, [selectView]);

  const refreshDetail = useCallback(async (id: string) => {
    if (selectedRef.current !== id) return;
    const sequence = ++refreshSequence.current;
    const prefix = `/v1/shipments/${id}`;
    try {
      const [shipment, captures, assets, jobs, reports, timeline] = await Promise.all([
        api<Shipment>(prefix), api<Capture[]>(`${prefix}/captures`), api<Asset[]>(`${prefix}/assets`),
        api<Job[]>(`${prefix}/jobs`), api<Report[]>(`${prefix}/reports`), api<Timeline[]>(`${prefix}/timeline`),
      ]);
      if (sequence === refreshSequence.current && selectedRef.current === id) {
        setDetailFailure(""); setDetail({shipment, captures, assets, jobs, reports, timeline});
      }
    } catch (err) {
      if (sequence === refreshSequence.current && selectedRef.current === id) setDetailFailure(err instanceof Error ? err.message : "Không tải được hồ sơ.");
    }
  }, []);

  useEffect(() => {
    const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
    if (token) { sessionStorage.setItem("kc-join-token", token); history.replaceState(history.state, "", window.location.pathname); }
    if (joining) setJoinToken(sessionStorage.getItem("kc-join-token") || "");
    Promise.all([
      api<AppConfig>("/v1/config").then(setConfig),
      api<Session>("/v1/auth/session").then(s => {setSession(s); setBuyerView(joining || (localStorage.getItem(`kc-view-${s.user.id}`) === "buyer" || sessionStorage.getItem("kc-buyer-view") === s.user.id)); setUser(s);}).catch(e => {if (e.status !== 401) throw e;}),
    ]).catch(e => setError(e.message)).finally(() => setLoading(false));
  }, [joining]);

  useEffect(() => {
    if (!selected) {setDetail(null); return;}
    setDetail(null);
    void refreshDetail(selected);
  }, [selected, refreshDetail]);
  const activeJob = detail?.jobs.find(job => ["QUEUED", "RUNNING"].includes(job.status));
  useEffect(() => {
    if (!activeJob || !selected) return;
    let stopped = false;
    async function poll() {
      await refreshDetail(selected!);
      if (!stopped) timer = window.setTimeout(poll, 1800);
    }
    let timer = window.setTimeout(poll, 1800);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [activeJob?.id, selected, refreshDetail]);

  useEffect(() => {
    if (tab !== "report" || !selected) return;
    let stopped = false, reading = false;
    async function syncReport() {
      if (reading || document.hidden) return;
      reading = true;
      const sequence = refreshSequence.current;
      try {
        const reports = await api<Report[]>(`/v1/shipments/${selected}/reports`);
        if (!stopped && sequence === refreshSequence.current) {
          setDetail(current => current?.shipment.id === selected && current && JSON.stringify(current.reports) !== JSON.stringify(reports)
            ? {...current, reports} : current);
        }
      } catch { /* Keep the last readable copy while the connection recovers. */ }
      finally { reading = false; }
    }
    const timer = window.setInterval(syncReport, 8000);
    window.addEventListener("focus", syncReport);
    return () => { stopped = true; clearInterval(timer); window.removeEventListener("focus", syncReport); };
  }, [tab, selected]);

  async function action(label: string, fn: () => Promise<void>) {
    const origin = selectedRef.current;
    setBusy(label); setError(""); setNotice("");
    try { await fn(); }
    catch (e) {if (selectedRef.current === origin) setError(e instanceof Error ? e.message : "Chưa hoàn tất thao tác.");}
    finally {setBusy("");}
  }
  async function changed() {if (selected) await refreshDetail(selected);}
  function open(id: string, report = false) {
    const nextTab = report ? "report" : "evidence";
    historyScope.current ??= crypto.randomUUID();
    const entry = {shipmentView: {scope: historyScope.current, id, tab: nextTab}};
    if (selectedRef.current && history.state?.shipmentView?.scope === historyScope.current) history.replaceState(entry, "");
    else history.pushState(entry, "");
    selectView(id, nextTab);
    window.scrollTo(0, 0);
  }
  function returnToInbox() {
    if (!selected) return;
    if (historyScope.current && history.state?.shipmentView?.scope === historyScope.current) history.back();
    else selectView(null);
  }
  function openQueue(stage: string) {
    returnToInbox();
    setInbox({...initialInbox, stage, sort: stage && stage !== "CONFIRMED" ? "oldest" : "recent"});
    window.scrollTo(0, 0);
  }
  function createShipment() {
    setError("");
    setCreating(true);
  }
  function showReport() {
    setTab("report");
    requestAnimationFrame(() => {
      const tabButton = document.getElementById("report-tab");
      tabButton?.focus({preventScroll: true});
      tabButton?.scrollIntoView({block: "start"});
    });
  }
  function enter(s: Session, buyer: boolean) {
    historyScope.current = null;
    setSession(s);
    setUser(s);
    setBuyerView(buyer);
    setInbox(initialInbox);
    localStorage.setItem(`kc-view-${s.user.id}`, buyer ? "buyer" : "merchant");
    if (buyer) sessionStorage.setItem("kc-buyer-view", s.user.id);
    else sessionStorage.removeItem("kc-buyer-view");
    window.scrollTo(0, 0);
  }

  function runDemo(example: string) {
    return action("Đang mở kiện hàng mẫu…", async () => {
      try {
        const demo = await createDemo(setBusy, example);
        enter(demo.session, true);
        setInbox({...initialInbox, source: "sample"});
        open(demo.shipmentId);
      } catch (err) {
        // Demo preparation uses both participant accounts. Restore the actual session after a partial run.
        const actual = await api<Session>("/v1/auth/session").catch(() => null);
        if (actual) enter(actual, sessionStorage.getItem("kc-buyer-view") === actual.user.id);
        throw err;
      }
    });
  }

  const ownStage = detail?.shipment.current_role === "MERCHANT" ? "DISPATCH" : "RECEIPT";
  const ownPhotosSealed = detail?.captures.some(c => c.stage === ownStage && c.status === "SEALED");
  const capturePanel = detail && session && <CapturePanel viewerId={session.user.id} shipment={detail.shipment} captures={detail.captures} assets={detail.assets} busy={!!busy} onAction={action} onChanged={changed}/>;
  const currentReport = detail?.reports[0];


  if (loading) return <main className="loading-screen"><Logo/><LoaderCircle className="spin"/><p>Đang mở không gian giao nhận…</p></main>;

  if (!session) return <EntryScreen demoEnabled={!!config?.demo_login_enabled} joining={joining}
    busy={busy} error={error} onDemo={() => runDemo("parcel-dent")}
    onRole={role => action("Đang mở hồ sơ…", async () => enter(await demoLogin(role), role === "BUYER"))}
    onLogin={(email, password, buyer) => action("Đang đăng nhập…", async () => {
      enter(await post<Session>("/v1/auth/login", {email, password}), buyer || joining);
    })}/>;

  const recipientSpace = detail?.shipment.id === selected ? detail.shipment.current_role === "BUYER" : buyerView;
  const activeQueue = !buyerView && merchantQueues.find(queue => queue.stage === inbox.stage);
  return <div className={`app-shell ${recipientSpace ? "recipient-space" : ""}`}>
    {!recipientSpace && <MerchantSidebar inbox={inbox} viewingCase={!!selected} networkLabel={config?.network_label}
      onCreate={createShipment} onQueue={openQueue}/>}
    <div className="main-shell"><header className="topbar"><div className="breadcrumb">{recipientSpace && <Logo/>}<span>{recipientSpace ? "Cổng người nhận" : "Quản lý cửa hàng"}</span><ChevronRight size={14}/><strong>{selected ? "Chi tiết hồ sơ" : "Hồ sơ kiện hàng"}</strong></div>
      <div className="user-menu"><span className="avatar">{session.user.name[0]}</span><span className="user-name">{session.user.name}</span>
        <button aria-label="Đăng xuất" className="icon-button" onClick={() => action("Đăng xuất…", async () => {await post("/v1/auth/logout"); historyScope.current = null; setUser(null); setDetail(null); selectView(null); setInbox(initialInbox);})}><LogOut size={17}/></button></div></header>
      <main className="main-content">
        {busy && <div className="work-progress" role="status"><LoaderCircle size={17} className="spin"/>{busy}</div>}
        {error && !creating && <div className="alert error" role="alert"><span>{error}</span><button aria-label="Đóng thông báo lỗi" className="icon-button" onClick={() => setError("")}><X size={16}/></button></div>}
        {detailFailure && detail?.shipment.id === selected && <div className="alert error" role="alert">{detailFailure} <button className="button secondary" onClick={changed}>Tải lại hồ sơ</button></div>}
        {notice && <div className="alert success" role="status"><Check size={18}/>{notice}</div>}
        {joinToken && <section className="join-banner"><Link2/><div><h3>Bạn được mời vào một hồ sơ giao nhận</h3><p>Liên kết hồ sơ với tài khoản người nhận hiện tại.</p></div><button className="button primary" disabled={!!busy} onClick={() => action("Đang tham gia…", async () => {
          const s = await post<Shipment>("/v1/invitations/claim", {token: joinToken}); sessionStorage.removeItem("kc-join-token"); setJoinToken(""); enter(session, true); open(s.id);
        })}>Tham gia hồ sơ<ArrowRight size={16}/></button></section>}

        {selected && <button className="return-button case-return" onClick={returnToInbox}><ArrowLeft aria-hidden="true" size={20} strokeWidth={2.5}/>Về danh sách hồ sơ</button>}
        {!selected ? <div key={session.user.id}>
          <div className="page-heading"><div><h1>{activeQueue ? activeQueue.label : buyerView ? "Kiện hàng tôi nhận" : "Hồ sơ kiện hàng"}<span className="heading-dot">.</span></h1><p>{activeQueue ? activeQueue.task : buyerView ? "Mở kiện hàng bạn nhận để thêm ảnh và kiểm tra biên bản của shop." : "Theo dõi kiện chờ ảnh lúc nhận và mở biên bản cần xác nhận."}</p></div>
            {!buyerView && <button className="button primary" onClick={createShipment}><Plus size={18}/>Tạo hồ sơ mới</button>}</div>
          <div className="inbox-source" role="group" aria-label="Loại hồ sơ">
            {([["work", buyerView ? "Kiện của tôi" : "Hồ sơ vận hành"], ["sample", "Kiện mẫu"]] as const).map(([source, label]) =>
              <button key={source} aria-pressed={inbox.source === source} onClick={() => setInbox({...initialInbox, source})}>{label}</button>)}
          </div>
          {config?.demo_login_enabled && !inbox.stage && !inbox.query && <ShowcaseParcels buyerView={buyerView} onOpen={open}/>}
          {config?.demo_login_enabled && inbox.source === "sample" && <section className="sample-case">
            <img src={parcelPhoto.src} alt="Thùng carton mẫu có vết móp ở mặt trước" width="128" height="96"/>
            <div><h2>Thử đối chiếu một kiện mẫu</h2><p>Xem ảnh lúc gửi, ảnh lúc nhận và biên bản của hai bên.</p></div>
            <div className="sample-actions"><label>Tình huống thử nghiệm<select value={scenario} onChange={e => setScenario(e.target.value)}>
              <option value="parcel-dent">Vết móp trên mặt hộp</option><option value="changed.png">Có vùng thay đổi</option>
              <option value="unchanged.png">Ảnh nguyên trạng</option><option value="lighting.png">Chỉ đổi ánh sáng</option><option value="no-reference.png">Không tìm được vùng</option>
            </select></label><button className="button primary" disabled={!!busy} onClick={() => runDemo(scenario)}>Trải nghiệm quy trình<ArrowRight size={16}/></button></div>
          </section>}
          <ShipmentInbox filters={inbox} onFilter={setInbox} buyerView={buyerView} onOpen={open}/>
          {!buyerView && <WorkflowMetrics/>}
        </div> : detail?.shipment.id === selected ? <>
          <div className="page-heading detail-heading"><div><div className="eyebrow">{detail.shipment.reference}<span className="separator">/</span>{detail.shipment.current_role === "MERCHANT" ? "VAI TRÒ NGƯỜI GỬI" : "VAI TRÒ NGƯỜI NHẬN"}</div><h1>{detail.shipment.title}</h1><p>Người nhận: {detail.shipment.recipient_name}<span className="separator">·</span>{formatTime(detail.shipment.created_at)}</p></div><span className="badge amber">{detail.shipment.status === "REVIEW" && (currentReport?.confirmations.length ?? 0) >= 2 ? "Đã đủ hai xác nhận" : statusLabels[detail.shipment.status] || detail.shipment.status}</span></div>
          {detail.shipment.notes.startsWith("Dữ liệu mô phỏng") && <div className="demo-strip"><Sparkles size={15}/>{detail.shipment.notes.includes("do AI tạo") ? "Kiện hàng mẫu · Ảnh mô phỏng do AI tạo" : "Kiện hàng mẫu · Ảnh mô phỏng"}</div>}
          <WorkflowSteps detail={detail} viewerId={session.user.id}/>
          <div className="detail-grid"><div className="detail-main"><div className="tabs" role="tablist" aria-label="Nội dung hồ sơ">{([["evidence", "Ảnh đối chiếu", Camera], ["report", "Biên bản", FileText], ["timeline", "Lịch sử", Clock3]] as const).map(([value, label, Icon]) => <button key={value} id={value + "-tab"} role="tab" aria-selected={tab === value} onClick={() => setTab(value)} className={tab === value ? "active" : ""}><Icon size={17}/>{label}{value === "report" && currentReport && <span>v{currentReport.version}</span>}</button>)}</div>
            {tab === "evidence" && <>
              <div className="evidence-workflow">
                {!ownPhotosSealed && capturePanel}
                <EvidencePanel captures={detail.captures} latestJob={detail.jobs[0]} shipment={detail.shipment} assets={detail.assets} report={currentReport} activeJob={activeJob} busy={!!busy} onAction={action} onChanged={changed} onReport={showReport}/>
                {ownPhotosSealed && <details className="supplement-panel"><summary><Plus size={16}/>Bổ sung ảnh vào hồ sơ</summary>{capturePanel}</details>}
              </div></>}
            {tab === "report" && <ReportPanel key={selected} shipment={detail.shipment} report={currentReport} session={session} shipmentId={selected} busy={!!busy} onAction={action} onChanged={changed}/>}
            {tab === "timeline" && <section className="panel timeline"><h2>Lịch sử hồ sơ</h2><p className="muted">Các thay đổi được ghi nhận theo từng mốc.</p>{[...detail.timeline].reverse().map(e => <div className="timeline-event" key={e.id}><span className="timeline-dot"/><div><strong>{eventLabels[e.kind] || e.kind}</strong><small>{formatTime(e.created_at)}</small>{typeof e.detail.manifest_hash === "string" && <code>{shortHash(e.detail.manifest_hash)}</code>}</div></div>)}</section>}
          </div><aside className="case-aside"><section id="case-participants" className="panel" tabIndex={-1}><h3>Người tham gia</h3><div className="party"><span className="avatar">G</span><div><strong>Người gửi</strong><small>Shop sở hữu hồ sơ</small></div></div><div className="party"><span className="avatar warm">N</span><div><strong>{detail.shipment.recipient_name}</strong><small>Người nhận kiện</small></div></div>
            {detail.shipment.current_role === "MERCHANT" && <button className="button secondary full" disabled={!!busy || detail.shipment.recipient_linked} onClick={() => action("Tạo liên kết…", async () => {const r = await post<{url: string}>(`/v1/shipments/${selected}/invitations`); if (selectedRef.current === selected) setInvitation(r.url);})}><Link2 size={16}/>{detail.shipment.recipient_linked ? "Người nhận đã tham gia" : "Lấy link người nhận"}</button>}{invitation && <label className="link-result">Liên kết riêng<input readOnly value={invitation} onFocus={e => e.currentTarget.select()}/><button className="button ghost" onClick={() => action("Sao chép…", async () => {await navigator.clipboard.writeText(invitation); setNotice("Đã sao chép liên kết người nhận.");})}>Sao chép</button></label>}
          </section>
            {BigInt(detail.shipment.reserve_amount) > 0n && <ChainPanel key={detail.shipment.id} shipment={detail.shipment} captures={detail.captures} report={currentReport} busy={!!busy} onAction={action} onChanged={changed}/>}
            <div className="help-note"><CircleHelp size={18}/><p>Ảnh thể hiện tình trạng bên ngoài. Hai bên quyết định trách nhiệm và phương án xử lý sau khi xem bằng chứng.</p></div></aside></div>
        </> : <div className="empty-state">{detailFailure ? <><p role="alert">{detailFailure}</p><button className="button primary" onClick={() => {setDetailFailure(""); changed();}}>Thử mở lại</button></> : <><LoaderCircle className="spin"/><p>Đang mở hồ sơ…</p></>}</div>}
        <footer className="footer"><span>ParcelLedger · Giao nhận có bằng chứng</span></footer>
      </main></div>
      {creating && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="new-title"><button className="icon-button modal-close" aria-label="Đóng" disabled={!!busy} onClick={() => {setCreating(false); setError("");}}><X/></button><span className="icon-tile"><Box/></span><h2 id="new-title">Tạo hồ sơ kiện hàng</h2><p className="muted">Bắt đầu với thông tin đơn hàng và bộ ảnh trước khi gửi.</p><form onSubmit={e => {e.preventDefault(); const data = new FormData(e.currentTarget); action("Tạo hồ sơ…", async () => {const s = await post<Shipment>("/v1/shipments", {reference: data.get("reference"), title: data.get("title"), recipient_name: data.get("recipient_name"), notes: data.get("notes"), reserve_amount: (BigInt(String(data.get("reserve_amount") || "0"))*1000000n).toString()}); setCreating(false); open(s.id);});}}>
        {error && <p role="alert" className="alert error">{error}</p>}
        <label>Mã kiện / mã đơn<input name="reference" required maxLength={80} placeholder="VD: DH-2026-001" autoFocus/></label><label>Tên hàng<input name="title" required maxLength={200} placeholder="VD: Bộ cốc gốm 6 chiếc"/></label><label>Tên người nhận<input name="recipient_name" required maxLength={120} placeholder="Người nhận kiện"/></label>
        <ReserveFields/>
        <label>Ghi chú bàn giao<textarea name="notes" maxLength={3000} placeholder="Các mặt hoặc góc cần chú ý…"/></label><button className="button primary full" disabled={!!busy}>Tạo và thêm ảnh<ArrowRight size={17}/></button></form></section></div>}
  </div>;
}

function ReserveFields() {
  const [withEscrow, setWithEscrow] = useState(false);
  return <>
    <label>Loại giao nhận<select value={withEscrow ? "escrow" : "standard"} onChange={e => setWithEscrow(e.target.value === "escrow")}>
      <option value="standard">Giao nhận thông thường</option>
      <option value="escrow">Giao nhận có ký quỹ</option>
    </select></label>
    {withEscrow && <label>Khoản ký quỹ thử nghiệm (kcTEST)<input name="reserve_amount" type="number" min="1" max="1000" step="1" defaultValue="10" required/></label>}
  </>;
}

function WorkflowSteps({detail, viewerId}: {detail: Detail; viewerId: string}) {
  const report = detail.reports[0];
  const merchant = detail.shipment.current_role === "MERCHANT";
  const isSealed = (stage: string) => detail.captures.some(c => c.stage === stage && c.status === "SEALED");
  const steps = [
    {label: "Ảnh lúc gửi", done: isSealed("DISPATCH"), tip: merchant ? "Thêm ảnh kiện hàng và khóa bộ ảnh trước khi gửi." : "Chờ shop thêm và khóa bộ ảnh trước khi gửi."},
    {label: "Ảnh lúc nhận", done: isSealed("RECEIPT"), tip: merchant ? (detail.shipment.recipient_linked ? "Người nhận đã tham gia. Đang chờ họ thêm và khóa ảnh lúc nhận." : "Gửi liên kết hồ sơ cho người nhận để họ thêm ảnh lúc nhận.") : "Chụp cùng mặt hộp, thêm ảnh rồi khóa bộ ảnh lúc nhận."},
    {label: "Đối chiếu", done: !!report, tip: detail.jobs.some(j => j.status === "RUNNING" || j.status === "QUEUED") ? "Hai bộ ảnh đang được đối chiếu. Kết quả sẽ hiện bên dưới." : "Chọn ảnh lúc nhận rồi bấm “Đối chiếu và tạo biên bản”."},
    {label: "Xác nhận", done: (report?.confirmations.length ?? 0) >= 2, tip: report?.confirmations.some(c => c.user_id === viewerId) ? "Bạn đã xác nhận. Đang chờ bên còn lại xem biên bản." : "Xem hai ảnh rồi mở biên bản để kiểm tra nhận xét và xác nhận."},
  ];
  const current = steps.findIndex(step => !step.done);
  return <section className="case-progress" aria-label="Tiến độ hồ sơ">
    <ol>{steps.map((step, index) => <li key={step.label} className={step.done ? "done" : undefined} aria-current={index === current ? "step" : undefined}>
      <span>{step.done ? <Check size={14} aria-label="Hoàn tất"/> : index + 1}</span>{step.label}
    </li>)}</ol>
    <p>{current === -1 ? "Hai bên đã xác nhận phiên bản biên bản này." : steps[current].tip}{merchant && current === 1 && <button className="invite-shortcut" onClick={() => document.getElementById("case-participants")?.focus()}>{detail.shipment.recipient_linked ? "Xem người tham gia" : "Mở mục mời người nhận"}<ArrowRight size={15}/></button>}</p>
  </section>;
}

function ReportPanel({report, session, shipmentId, busy, onAction, onChanged, shipment}: {shipment: Shipment; report?: Report; session: Session; shipmentId: string; busy: boolean;
  onAction: (label: string, fn: () => Promise<void>) => Promise<void>; onChanged: () => Promise<void>}) {
  const [editing, setEditing] = useState<{version: number; observations: Report["document"]["observations"]; notes: string} | null>(null);
  if (!report) return <section className="panel empty-state"><FileText size={35}/><h3>Biên bản sẽ xuất hiện ở đây</h3><p>Khóa ảnh giao nhận và bắt đầu đối chiếu để tạo bản nháp có bằng chứng.</p></section>;
  const confirmed = report.confirmations.some(a => a.user_id === session.user.id);
  const document = report.document;
  const {reference_asset_id: reference, query_asset_id: query} = document.match;
  const regions = document.observations.flatMap(o => o.state === "VISIBLE_CHANGE" && o.query_polygon && o.evidence_asset_ids.includes(query) ? [o.query_polygon] : []);
  const reportUrl = `/api/v1/shipments/${shipmentId}/reports/${report.version}`;
  return <section className="report-panel">
    <div className="report-toolbar"><a className="button secondary" href={reportUrl + "/pdf"}><ArrowDownToLine size={16}/>Tải biên bản PDF</a></div>
    <article className="report-paper">
    <header className="report-title"><div className="report-running-head"><span>ParcelLedger</span><span>Hồ sơ giao nhận</span></div>
      <h2>Biên bản tình trạng kiện hàng</h2><p><span>Phiên bản {report.version}</span> · Lập lúc {reportTime(document.created_at)} (GMT+7)</p></header>
    <dl className="report-fields">{[["Mã kiện / mã đơn", document.reference], ["Tên hàng", document.title],
      ["Bên gửi", report.sender_name], ["Người nhận theo đơn", shipment.recipient_name]].map(([label, value]) =>
      <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {shipment.reference.startsWith("DEMO-") && shipment.notes.startsWith("Dữ liệu mô phỏng") && <p className="report-caption">{shipment.notes}</p>}
    <h3>Ảnh đối chiếu</h3>
    <div className="report-photos">{([["Ảnh A · Trước khi gửi", reference], ["Ảnh B · Lúc nhận kiện", query]] as const).map(([label,id], index) =>
      <figure key={label}><figcaption>{label}</figcaption>
        {id ? <a className="evidence-image" href={imageUrl(id) + "?variant=original"} target="_blank" rel="noreferrer">
          <img src={imageUrl(id)} alt={label + " · Xem ảnh gốc"}/>{index === 1 && <Overlay color="monochrome" polygons={regions}/>}
        </a> : <p className="report-caption">Chưa tìm được ảnh gửi tương ứng.</p>}</figure>)}</div>
    {regions.length > 0 && <p className="report-caption">Đường đen viền trắng trên ảnh B đánh dấu vùng cần kiểm tra.</p>}
    <h3>Ghi nhận tình trạng</h3>{!document.observations.length && <p>Phiên bản này chưa có nhận xét.</p>}
    {(editing?.observations || document.observations).map((o, index) => <div className="report-observation" key={o.id}>
      <strong>{document.observations.length > 1 && `${index + 1}. `}{o.state === "INCONCLUSIVE" ? "Chưa đủ bằng chứng" : o.state === "VISIBLE_CHANGE" ? "Có vùng cần kiểm tra" : "Chưa thấy thay đổi vượt ngưỡng"}</strong>
      {editing ? <textarea aria-label={`Nhận xét ${index + 1}`} value={o.text} maxLength={3000} onChange={e => setEditing({...editing, observations: editing.observations.map((x, i) => i === index ? {...x, text: e.target.value} : x)})}/> : <p>{o.text}</p>}
      <div className="report-sources"><span>{o.author_type === "HUMAN" ? "Người kiểm tra" : "Bộ đối chiếu ảnh"}</span>
        {o.evidence_asset_ids.map(id => <a key={id} href={imageUrl(id) + "?variant=original"} target="_blank" rel="noreferrer">{id === query ? "Ảnh B" : id === reference ? "Ảnh A" : `Ảnh ${id}`}</a>)}</div></div>)}
    <h3>Ghi chú của hai bên</h3>{editing ? <textarea aria-label="Ghi chú của hai bên" maxLength={5000} value={editing.notes} onChange={e => setEditing({...editing, notes: e.target.value})}/> : <p className="report-notes">{report.document.user_notes || "Chưa có ghi chú bổ sung."}</p>}
    <div className="button-row">{editing ? <><button className="button primary" disabled={busy} onClick={() => onAction("Lưu phiên bản mới…", async () => {await post(`/v1/shipments/${shipmentId}/reports`, {expected_version: editing.version, observations: editing.observations, user_notes: editing.notes}); setEditing(null); await onChanged();})}>Lưu phiên bản mới</button><button className="button secondary" onClick={() => setEditing(null)}>Hủy chỉnh sửa</button></> : <button className="button secondary" onClick={() => setEditing({version: report.version, observations: structuredClone(document.observations), notes: document.user_notes})}>Chỉnh sửa nhận xét</button>}</div>
    <p className="scope-note">{document.scope}</p>
    <div className="confirmation-box"><CheckCheck size={24}/><div><h3>{confirmed ? "Bạn đã kiểm tra phiên bản này" : "Kiểm tra nội dung biên bản"}</h3><p>Bạn có thể đánh dấu đã kiểm tra hoặc ký trực tiếp bên dưới.</p><span className="fine">{report.confirmations.length}/2 bên đã xác nhận bản {report.version}</span></div><button className="button secondary" disabled={busy || confirmed || !!editing || !shipment.can_confirm} onClick={() => onAction("Xác nhận nội dung…", async () => {await post(`/v1/shipments/${shipmentId}/reports/confirm`, {expected_version: report.version, report_hash: report.canonical_hash}); await onChanged();})}>{confirmed ? <><Check size={16}/>Đã kiểm tra</> : "Xác nhận đã kiểm tra"}</button></div>
    <ReportSignatures key={report.canonical_hash} report={report} ownRole={shipment.current_role} canSign={shipment.can_confirm} busy={busy || !!editing} onSign={mark => onAction("Đang lưu chữ ký…", async () => {
      await post(`/v1/shipments/${shipmentId}/reports/confirm`, {expected_version: report.version, report_hash: report.canonical_hash, signature: mark});
      await onChanged();
    })}/>
    <details className="report-trace"><summary>Đối soát phiên bản</summary>
      <p>SHA-256 của hồ sơ JSON</p><code>{report.canonical_hash}</code><a href={reportUrl + "/export"}>Tải hồ sơ JSON</a></details>
    </article></section>;
}
