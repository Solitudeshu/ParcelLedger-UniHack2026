"use client";

import { imageUrl, ResearchTransformer } from "@/lib/api";

export function ResearchTransformerPanel({result}: {result: ResearchTransformer}) {
  return <details className="method-details research-transformer" data-testid="transformer-advisor">
    <summary>AI đối chiếu nâng cao · thử nghiệm</summary>
    <p>AI tìm ảnh gửi tương ứng, rồi gợi ý vùng khác biệt để bạn xem lại. Kết quả này được lưu riêng với nhận xét trong biên bản.</p>
    {result.status === "EVALUATED" && result.reference_asset_id ? <>
      <div className="comparison">
        <div className="comparison-side">
          <div className="comparison-label">ẢNH GỬI AI CHỌN</div>
          <div className="evidence-image"><img src={imageUrl(result.reference_asset_id)} alt="Ảnh gửi do AI nâng cao chọn để đối chiếu"/></div>
        </div>
        <div className="comparison-side">
          <div className="comparison-label">VÙNG CẦN XEM LẠI</div>
          <div className="evidence-image">
            <img src={imageUrl(result.query_asset_id)} alt="Ảnh nhận với vùng gợi ý từ AI nâng cao"/>
            {!!result.query_polygons.length && <svg className="region-overlay purple" viewBox="0 0 10000 10000" preserveAspectRatio="none" aria-hidden="true">
              {result.query_polygons.map((polygon, i) => <polygon key={i} points={polygon.map(p => p.join(",")).join(" ")} vectorEffect="non-scaling-stroke"/>)}
            </svg>}
          </div>
        </div>
      </div>
      <p>{result.query_polygons.length ? "Vùng tím là khác biệt AI gợi ý, cần đối chiếu bằng mắt trên ảnh gốc." : "AI chưa đánh dấu khác biệt. Điều này không xác nhận kiện hàng nguyên vẹn."}</p>
    </> : <p role="status">{result.status === "ABSTAIN"
      ? result.instruction || "Chưa đủ căn cứ để đối chiếu. Hãy chụp thêm đúng mặt hộp với nhãn hoặc băng keo rõ nét."
      : "AI nâng cao hiện chưa trả được kết quả. Bạn vẫn có thể kiểm tra ảnh và biên bản bằng quy trình hiện có."}</p>}
    <p className="fine">Đã train trên dữ liệu mô phỏng; chưa kiểm chứng trên hộp thật. Góc chụp, ánh sáng và vật che có thể gây nhầm. Hai bên kiểm tra trước khi xác nhận.</p>
  </details>;
}
