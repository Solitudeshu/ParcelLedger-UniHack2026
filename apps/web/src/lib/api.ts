import type { components } from "../../../../packages/api-client/src/schema";
import beforePhoto from "@/assets/truoc-khi-gui.webp";
import afterPhoto from "@/assets/luc-nhan.webp";

type Schemas = components["schemas"];
export type Session = Schemas["SessionView"];
export type Shipment = Schemas["ShipmentView"];
export type InboxPage = Schemas["InboxPage"];
export type Asset = Schemas["AssetView"];
export type Job = Schemas["JobView"];
export type Observation = Schemas["Observation"];
export type Match = Schemas["MatchResult"];
export type ResearchTransformer = {
  status: "EVALUATED" | "ABSTAIN" | "UNAVAILABLE"; mode: "SHADOW_ONLY";
  query_asset_id: string; query_region: [number, number][] | null;
  reference_asset_id: string | null; requested_reference_asset_id: string | null;
  query_polygons: [number, number][][]; changes_product_decision: false;
  model_version?: string; instruction?: string; reason_codes?: string[];
};
export type Capture = {id: string; stage: string; version: number; status: string;
  actor_id: string; manifest_hash: string | null; manifest: Record<string, unknown> | null};
export type Report = {id: string; version: number; canonical_hash: string; created_at: string; sender_name: string;
  document: {reference: string; title: string; created_at: string; observations: Observation[]; match: Match; user_notes: string; scope: string;
    research_transformer?: ResearchTransformer | null;
    research_change?: {status: string; model_version?: string; query_asset_id?: string; reference_asset_id?: string; query_polygons?: [number, number][][]; changed_fraction?: number; changes_product_decision: boolean}|null;
    inference_kind: string; recapture_instructions?: {responsible_party: string; action: string; title: string; instruction: string}[]; research_advice?: {status: string; mode: string; model_version?: string; suggested_reference_asset_id?: string; checkpoint_sha256?: string; changes_product_decision: boolean}|null; [key: string]: unknown};
  confirmations: {user_id: string; role: "MERCHANT" | "BUYER"; kind: string; created_at: string; signature?: RecordedSignature | null}[]};
export type Timeline = {id: string; kind: string; created_at: string; detail: Record<string, unknown>};
export type SignatureInput = Schemas["ReportSignature"];
export type RecordedSignature = SignatureInput & {account_name: string; signed_at: string; report_hash: string; version: number};
export type AppConfig = {app_env: string; demo_login_enabled: boolean; chain_id: number;
  escrow_factory_address: string | null; model_trained: boolean; network_label: string};

export class ApiFailure extends Error {
  constructor(public code: string, message: string, public status: number, public correlationId?: string) {
    super(message);
  }
}

let csrf = "";
export function setSession(session: Session) { csrf = session.csrf_token; }

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = options.method || "GET";
  const headers = new Headers(options.headers);
  if (options.body && typeof options.body === "string") headers.set("Content-Type", "application/json");
  if (method !== "GET") {
    headers.set("X-CSRF-Token", csrf);
    if (!headers.has("Idempotency-Key")) headers.set("Idempotency-Key", crypto.randomUUID());
  }
  let response: Response;
  try { response = await fetch(`/api${path}`, {...options, headers, credentials: "include"}); }
  catch { throw new ApiFailure("OFFLINE", "Mất kết nối. Ảnh đang chọn được giữ trong phiên; hãy thử lại khi có mạng.", 0); }
  const result = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiFailure(result?.error?.code || "REQUEST_FAILED",
      result?.error?.message || "Chưa hoàn tất thao tác. Vui lòng thử lại.", response.status,
      result?.error?.correlation_id);
  }
  return result;
}

export const post = <T,>(path: string, body: unknown = {}, key?: string) => api<T>(path, {
  method: "POST", body: JSON.stringify(body), headers: key ? {"Idempotency-Key": key} : undefined,
});

export async function demoLogin(role: "MERCHANT" | "BUYER") {
  const session = await post<Session>("/v1/dev/session", {role});
  setSession(session);
  return session;
}

export async function uploadFile(shipmentId: string, captureId: string, file: File,
                                 parentId?: string, source = "UPLOADED_ARCHIVE") {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  const sha256 = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
  const intent = await post<{asset_id: string; upload_url: string}>(`/v1/shipments/${shipmentId}/uploads`, {
    capture_session_id: captureId, filename: file.name, mime_type: file.type,
    byte_size: file.size, sha256, source, parent_overview_id: parentId || null,
  });
  const ticket = await post<{transport: "S3_POST"|"API_PUT"|"ALREADY_UPLOADED"; url?: string; fields?: Record<string,string>}>(`/v1/assets/${intent.asset_id}/upload-ticket`);
  if (ticket.transport === "S3_POST") {
    const form = new FormData();
    Object.entries(ticket.fields || {}).forEach(([key, value]) => form.append(key, value));
    form.append("file", file);
    let response: Response;
    try { response = await fetch(ticket.url!, {method: "POST", body: form, credentials: "omit"}); }
    catch { throw new ApiFailure("UPLOAD_CONNECTION", "Kết nối kho ảnh gián đoạn. Hãy thử tải lại ảnh.", 0); }
    if (!response.ok) throw new ApiFailure("UPLOAD_REJECTED", "Phiên tải ảnh đã hết hạn hoặc bị từ chối. Hãy thử lại.", response.status);
  } else if (ticket.transport === "API_PUT") {
    await api(ticket.url!, {method: "PUT", body: file, headers: {"Content-Type": "application/octet-stream"}});
  }
  return post<Asset>(`/v1/assets/${intent.asset_id}/complete`);
}

export async function createDemo(progress: (message: string) => void, scenario = "changed.png") {
  const useParcelPhotos = scenario === "parcel-dent";
  progress("Tạo hồ sơ thử nghiệm…");
  await demoLogin("MERCHANT");
  const shipment = await post<Shipment>("/v1/shipments", {
    reference: `DEMO-${Date.now().toString().slice(-8)}`, title: "Đèn gốm · Shop Mộc",
    recipient_name: "Minh", reserve_amount: "10000000",
    notes: useParcelPhotos ? "Dữ liệu mô phỏng do AI tạo để minh họa một kiện hàng bị móp." : "Dữ liệu mô phỏng do dự án tự tạo để kiểm tra quy trình. Không dùng để đánh giá độ chính xác AI.",
  });
  async function capture(stage: string, filename: string, photoUrl?: string) {
    const session = await post<Capture>(`/v1/shipments/${shipment.id}/captures`, {stage});
    const response = await fetch(photoUrl ?? `/api/v1/dev/fixtures/${filename}`);
    if (!response.ok) throw new Error("Không tải được ảnh thử nghiệm.");
    const file = new File([await response.blob()], filename, {type: photoUrl ? "image/webp" : "image/png"});
    const asset = await uploadFile(shipment.id, session.id, file);
    await post(`/v1/shipments/${shipment.id}/captures/${session.id}/seal`, {expected_version: session.version});
    return asset;
  }
  progress("Tải và khóa bộ ảnh của shop…");
  if (useParcelPhotos) await capture("DISPATCH", "luc-gui.webp", beforePhoto.src);
  else await capture("DISPATCH", "dispatch.png");
  const invitation = await post<{token: string}>(`/v1/shipments/${shipment.id}/invitations`);
  const session = await demoLogin("BUYER");
  await post("/v1/invitations/claim", {token: invitation.token});
  progress("Tải ảnh người nhận và gửi phân tích…");
  const query = useParcelPhotos ? await capture("RECEIPT", "luc-nhan.webp", afterPhoto.src) : await capture("RECEIPT", scenario);
  await post(`/v1/shipments/${shipment.id}/analyses`, {query_asset_id: query.id});
  return {shipmentId: shipment.id, session};
}

export const imageUrl = (id: string) => `/api/v1/assets/${id}/content`;
export const shortHash = (value: string) => `${value.slice(0, 10)}…${value.slice(-6)}`;
export const reportTime = (value: string) => new Intl.DateTimeFormat("vi-VN", {
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Ho_Chi_Minh",
}).format(new Date(value));
export const formatTime = (value: string) => new Intl.DateTimeFormat("vi-VN", {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
}).format(new Date(value));

export const statusLabels: Record<string, string> = {
  PREPARING: "Chuẩn bị gửi", DISPATCHED: "Đã khóa ảnh gửi", RECEIVED: "Đã nhận ảnh",
  ANALYZING: "Đang đối chiếu", REVIEW: "Chờ kiểm tra", RESOLVED: "Đã xử lý", DISPUTED: "Cần phân xử",
};
export const reasonLabels: Record<string, string> = {
  WRONG_REGION: "Chưa tìm được cùng vùng", LOW_TEXTURE: "Quá ít chi tiết nhận dạng", BLUR: "Ảnh chưa rõ nét",
  GLARE: "Vùng ảnh bị chói", LOW_LIGHT: "Ảnh quá tối", LOW_DETAIL: "Cần ảnh gần và rõ hơn",
  INSUFFICIENT_OVERLAP: "Hai ảnh chưa bao phủ cùng vùng", NONPLANAR_GEOMETRY: "Góc chụp chưa phù hợp",
  MISSING_REFERENCE: "Thiếu ảnh trước khi gửi", OCCLUSION: "Vùng kiểm tra bị che",
};
