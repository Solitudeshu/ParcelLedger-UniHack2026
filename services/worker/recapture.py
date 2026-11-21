"""Evidence-driven capture instructions; deterministic policy, not a trained agent."""


def instructions(match):
    if match["status"] == "MATCHED_SUFFICIENT":
        return []
    reasons = set(match["reason_codes"])
    actions = []
    if "MISSING_REFERENCE" in reasons:
        actions.append(("MERCHANT", "ADD_DISPATCH_VIEW", "Thiếu ảnh trước khi gửi", "Người gửi bổ sung ảnh lưu từ lúc đóng gói của đúng mặt hộp này. Ảnh chụp sau giao nhận không thay thế được bằng chứng trước khi gửi."))
    if reasons & {"WRONG_REGION", "LOW_TEXTURE", "NONPLANAR_GEOMETRY", "INSUFFICIENT_OVERLAP"}:
        actions.append(("BUYER", "WIDER_CONTEXT", "Cần nhận diện đúng mặt hộp", "Chụp rộng hơn để thấy vùng cần kiểm tra cùng băng keo, nhãn hoặc cạnh hộp. Giữ vùng này trong cùng một mặt, tránh góc quá xiên."))
    if "BLUR" in reasons or "LOW_DETAIL" in reasons:
        actions.append(("BUYER", "SHARP_DETAIL", "Cần ảnh rõ chi tiết", "Lau ống kính, giữ máy yên và chạm lấy nét vào vùng cần xem. Chụp gần vừa đủ, kèm một ảnh tổng thể để nhận diện vị trí."))
    if "GLARE" in reasons:
        actions.append(("BUYER", "REMOVE_GLARE", "Ánh phản chiếu che chi tiết", "Tắt đèn flash hoặc đổi vị trí nguồn sáng để băng keo không phản chiếu vào máy. Giữ cùng mặt hộp trong khung hình."))
    if "LOW_LIGHT" in reasons:
        actions.append(("BUYER", "MORE_LIGHT", "Cần ánh sáng đều hơn", "Đặt hộp ở nơi sáng đều, tránh bóng tay che vùng cần xem rồi chụp lại cùng mặt."))
    if not actions:
        actions.append(("BUYER", "WIDER_CONTEXT", "Cần ảnh bổ sung", "Chụp rõ vùng cần kiểm tra cùng các chi tiết nhận diện trên mặt hộp. Hai bên kiểm tra ảnh gốc nếu vẫn chưa đối chiếu được."))
    return [{"responsible_party": party, "action": action, "title": title, "instruction": instruction,
             "reference_asset_id": match.get("reference_asset_id"), "query_region": match.get("query_region"),
             "policy_version": "evidence-recapture-v1"} for party, action, title, instruction in actions[:3]]
