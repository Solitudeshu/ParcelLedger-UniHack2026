"use client";

import {useEffect, useState} from "react";
import {Clock3, RefreshCw} from "lucide-react";
import {api} from "@/lib/api";

type Timing = {sample_count: number; median: number|null; p95: number|null};
type Metrics = {case_count: number; event_count: number; timings: Record<string,Timing>;
  measurement_scopes: string[]; outbox_pending: number; outbox_dead_letters: number};
const labels: Record<string,string> = {analysis_seconds:"Đối chiếu đến bản nháp", first_review_seconds:"Bản nháp đến lần kiểm tra đầu", draft_from_creation_seconds:"Tạo hồ sơ đến bản nháp"};
function duration(seconds: number|null) {
  if (seconds === null) return "Chưa có số đo";
  if (seconds < 60) return `${seconds.toFixed(1)} giây`;
  if (seconds < 3600) return `${(seconds/60).toFixed(1)} phút`;
  return `${(seconds/3600).toFixed(1)} giờ`;
}

export function WorkflowMetrics() {
  const [data, setData] = useState<Metrics|null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  async function refresh() {
    setLoading(true);
    try {setData(await api<Metrics>("/v1/telemetry/workflow")); setError("");}
    catch {setError("Chưa đọc được số liệu vận hành. Bạn vẫn có thể xử lý hồ sơ.");}
    finally {setLoading(false);}
  }
  useEffect(()=>{void refresh();},[]);
  return <details className="panel workflow-metrics"><summary><Clock3 size={17}/>Thời gian xử lý hồ sơ</summary>
    <div className="section-top"><p>Số đo trong tổ chức của bạn, từ lịch sử thao tác đã ghi nhận.</p><button className="button ghost" disabled={loading} onClick={refresh}><RefreshCw size={14}/>Cập nhật số liệu</button></div>
    {error && <p role="alert">{error}</p>}
    {data && <>
      {data.measurement_scopes.includes("LOCAL_TEST") && <p className="demo-strip">Có dữ liệu thử nghiệm cục bộ. Các số đo này chưa đại diện cho người dùng thực tế.</p>}
      <div className="metric-grid">{Object.entries(labels).map(([key,label])=>{const value=data.timings[key];return <div className="metric-item" key={key}><span>{label}</span><strong>{duration(value?.median ?? null)}</strong><small>Trung vị · {value?.sample_count ?? 0} hồ sơ · P95 {duration(value?.p95 ?? null)}</small></div>;})}</div>
      <p className="fine">{data.case_count} hồ sơ có lịch sử · {data.outbox_pending} sự kiện chờ đồng bộ. Thời gian giữa các mốc bao gồm cả lúc người dùng tạm dừng.</p>
      {data.outbox_dead_letters > 0 && <p role="alert">Có {data.outbox_dead_letters} sự kiện chưa đồng bộ sau nhiều lần thử. Số liệu có thể thiếu; cần kiểm tra dịch vụ.</p>}
    </>}
  </details>;
}
