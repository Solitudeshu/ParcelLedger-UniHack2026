"use client";

import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Box, Camera, CheckCheck, ClipboardCheck, LoaderCircle, RefreshCw, ScanLine, Search, Truck } from "lucide-react";
import { api, formatTime, imageUrl, InboxPage, statusLabels } from "@/lib/api";

export type InboxFilters = {source: "work" | "sample"; stage: string; query: string; page: number; sort: "recent" | "oldest"};
export const initialInbox: InboxFilters = {source: "work", stage: "", query: "", page: 1, sort: "recent"};
const pageSize = 8;
const stageNames: Record<string, string> = {...statusLabels, DISPATCHED: "Chờ ảnh lúc nhận", REVIEW: "Chờ xác nhận", CONFIRMED: "Đã đủ hai xác nhận"};
const queueStages = [
  {key: "DISPATCHED", icon: Truck},
  {key: "REVIEW", icon: ClipboardCheck},
  {key: "CONFIRMED", icon: CheckCheck},
];
export const merchantQueues = [
  {stage: "PREPARING", label: "Chuẩn bị gửi", icon: Camera, task: "Bổ sung ảnh trước khi gửi và khóa bộ ảnh để bàn giao."},
  {stage: "DISPATCHED", label: "Chờ ảnh nhận", icon: Truck, task: "Kiểm tra người nhận đã tham gia hồ sơ và theo dõi ảnh lúc nhận kiện."},
  {stage: "RECEIVED", label: "Chờ đối chiếu", icon: ScanLine, task: "Mở hồ sơ để chọn ảnh lúc nhận và đối chiếu với ảnh trước khi gửi."},
  {stage: "REVIEW", label: "Chờ xác nhận", icon: ClipboardCheck, task: "Kiểm tra biên bản và xem bên nào còn cần xác nhận."},
  {stage: "CONFIRMED", label: "Đã xác nhận", icon: CheckCheck, task: "Tra cứu hoặc tải biên bản mà hai bên đã kiểm tra."},
];

type Props = {
  filters: InboxFilters; onFilter: (filters: InboxFilters) => void;
  buyerView: boolean; onOpen: (id: string, report: boolean) => void;
};

export function ShowcaseParcels({buyerView, onOpen}: Pick<Props, "buyerView" | "onOpen">) {
  const [records, setRecords] = useState<InboxPage["items"]>([]);
  useEffect(() => {
    const controller = new AbortController();
    setRecords([]);
    const role = buyerView ? "BUYER" : "MERCHANT";
    api<InboxPage>(`/v1/shipment-inbox?source=sample&q=DEMO-PL-&role=${role}&page_size=4`, {signal: controller.signal})
      .then(page => {if (!controller.signal.aborted) setRecords(page.items);})
      .catch(() => { /* The regular inbox remains available. */ });
    return () => controller.abort();
  }, [buyerView]);
  if (!records.length) return null;
  return <section className="showcase-parcels"><h2>Hồ sơ giao nhận minh họa</h2>
    <div>{records.map(item => <button key={item.id} onClick={() => onOpen(item.id, false)}>
      {item.cover_asset_id && <img src={imageUrl(item.cover_asset_id) + "?variant=thumbnail"} alt=""/>}
      <span><strong>{item.title}</strong><small>{item.photo_count} ảnh · {stageNames[item.work_stage] ?? item.work_stage}</small></span><ArrowRight size={16}/>
    </button>)}</div></section>;
}

export function ShipmentInbox({filters, onFilter, buyerView, onOpen}: Props) {
  const [refresh, setRefresh] = useState(0);
  const [result, setResult] = useState<{key: string; data?: InboxPage; error?: string}>();
  const params = new URLSearchParams({role: buyerView ? "BUYER" : "MERCHANT", source: filters.source, stage: filters.stage, sort: filters.sort, q: filters.query.trim(), page: String(filters.page), page_size: String(pageSize)});
  const url = "/v1/shipment-inbox?" + params;
  const requestKey = url + "#" + refresh;
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const data = await api<InboxPage>(url, {signal: controller.signal});
        if (!controller.signal.aborted) setResult({key: requestKey, data});
      } catch (err) {
        if (!controller.signal.aborted) setResult({key: requestKey, error: err instanceof Error ? err.message : "Chưa tải được danh sách hồ sơ."});
      }
    }, filters.query ? 180 : 0);
    return () => {clearTimeout(timer); controller.abort();};
  }, [url, requestKey, filters.query]);

  function filter(change: Partial<InboxFilters>) {onFilter({...filters, page: 1, ...change});}
  const current = result?.key === requestKey ? result : undefined;
  const data = current?.data;
  const total = filters.stage ? data?.counts[filters.stage] ?? 0 : Object.values(data?.counts ?? {}).reduce((sum, n) => sum + n, 0);
  const pages = Math.ceil(total / pageSize);
  return <>
    {!buyerView && <div className="queue-cards" role="group" aria-label="Lọc theo tiến độ giao nhận">
      {queueStages.map(({key, icon: Icon}) => <button key={key} aria-pressed={filters.stage === key} onClick={() => filter({stage: filters.stage === key ? "" : key})}>
        <span className="queue-icon"><Icon size={19}/></span><span>{stageNames[key]}</span><strong>{data ? data.counts[key] ?? 0 : "…"}</strong>
      </button>)}
    </div>}
    <section className="panel list-panel inbox-list" aria-label="Danh sách hồ sơ">
      <div className="section-top"><h2>{filters.source === "sample" ? "Danh sách kiện mẫu" : buyerView ? "Kiện hàng được chia sẻ" : "Danh sách kiện hàng"}</h2>
        <button className="icon-button" aria-label="Làm mới danh sách" onClick={() => setRefresh(n => n + 1)}><RefreshCw size={16}/></button></div>
      <div className="inbox-tools"><label className="search-field"><Search size={17}/><input aria-label="Tìm hồ sơ" placeholder={buyerView ? "Tìm kiện của bạn…" : "Tìm mã kiện, tên hàng, người nhận…"} maxLength={200} value={filters.query} onChange={e => filter({query: e.target.value})}/></label>
        <select aria-label="Trạng thái hồ sơ" value={filters.stage} onChange={e => filter({stage: e.target.value})}>
          <option value="">Tất cả trạng thái</option>{Object.entries(stageNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>{!buyerView && <select aria-label="Thứ tự hồ sơ" value={filters.sort} onChange={e => filter({sort: e.target.value === "oldest" ? "oldest" : "recent"})}>
          <option value="recent">Cập nhật gần nhất</option><option value="oldest">Lâu chưa cập nhật</option>
        </select>}</div>
      {!current ? <div className="empty-state" role="status"><LoaderCircle className="spin"/>Đang tải hồ sơ…</div> :
        current.error ? <div className="empty-state"><p role="alert">{current.error}</p><button className="button secondary" onClick={() => setRefresh(n => n + 1)}>Thử lại</button></div> :
        data?.items.length ? <>
          {!buyerView && <div className="inbox-columns"><span>Kiện hàng</span><span>Người nhận</span><span>Trạng thái</span><span>Cập nhật</span><span/></div>}
          {data.items.map(item => {
            const report = item.work_stage === "REVIEW" || item.work_stage === "CONFIRMED";
            if (buyerView) {
              const action = report ? "Xem biên bản" : item.work_stage === "DISPATCHED" ? "Thêm ảnh lúc nhận" : item.work_stage === "RECEIVED" ? "Đối chiếu ảnh" : "Xem kiện hàng";
              return <button key={item.id} className="recipient-parcel" onClick={() => onOpen(item.id, report)}>
                <span className="recipient-icon">{item.cover_asset_id ? <img className="parcel-thumb" loading="lazy" src={imageUrl(item.cover_asset_id) + "?variant=thumbnail"} alt=""/> : <Box size={21}/>}</span><span className="recipient-order"><strong>{item.title}</strong><small>{item.reference} · {stageNames[item.work_stage] ?? item.work_stage}{item.photo_count > 0 && ` · ${item.photo_count} ảnh`}</small></span>
                <span className="recipient-action">{action}<ArrowRight size={15}/></span>
              </button>;
            }
            return <button key={item.id} className="inbox-row" onClick={() => onOpen(item.id, report)}>
              <span className="inbox-order">{item.cover_asset_id && <img className="parcel-thumb" loading="lazy" src={imageUrl(item.cover_asset_id) + "?variant=thumbnail"} alt=""/>}<span><strong>{item.title}</strong><small>{item.reference}{item.photo_count > 0 && ` · ${item.photo_count} ảnh`}</small></span></span>
              <span>{item.recipient_name}</span><span className={report && item.work_stage === "REVIEW" ? "badge amber" : "badge green"}>{stageNames[item.work_stage] ?? item.work_stage}</span>
              <time dateTime={item.updated_at}>{formatTime(item.updated_at)}</time><span className="inbox-open">{report ? "Mở biên bản" : "Mở hồ sơ"}<ArrowRight size={14}/></span>
            </button>;
          })}
          <div className="inbox-paging"><span>{(data.page - 1) * pageSize + 1}–{(data.page - 1) * pageSize + data.items.length} trong {total} hồ sơ</span>
            {pages > 1 && <div><button aria-label="Trang trước" className="icon-button" disabled={data.page <= 1} onClick={() => filter({page: data.page - 1})}><ArrowLeft size={17}/></button>
              <span>Trang {data.page}/{pages}</span><button aria-label="Trang sau" className="icon-button" disabled={data.page >= pages} onClick={() => filter({page: data.page + 1})}><ArrowRight size={17}/></button></div>}</div>
        </> : <div className="empty-state"><Box size={28}/><h3>{filters.query || filters.stage ? "Không có kiện hàng khớp bộ lọc" : "Chưa có kiện hàng trong danh sách"}</h3>
          {filters.query || filters.stage ? <button className="button secondary" onClick={() => filter({stage: "", query: ""})}>Xóa bộ lọc</button> :
            <p>{filters.source === "sample" ? "Chọn một tình huống mẫu để thử đối chiếu." : buyerView ? "Mở liên kết mời từ shop để nhận hồ sơ đầu tiên." : "Chọn “Tạo hồ sơ mới” để ghi nhận kiện đầu tiên của cửa hàng."}</p>}</div>}
    </section>
  </>;
}
