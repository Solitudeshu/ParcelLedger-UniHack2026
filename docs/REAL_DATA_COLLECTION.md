# Thu ảnh và gán nhãn hộp thật

Hiện dự án chưa nhận được bộ hộp vật lý. Không dùng ảnh tự sinh, ảnh tạo bằng AI hay nhãn dự đoán để thay bằng chứng này.

## Thu ảnh

Pilot 30 hộp để kiểm tra quy trình; mục tiêu cuối trong proposal là 120 hộp (72/24/24). Mỗi hộp có mã khác nhau, không dùng hai lần cùng một hộp như hai mẫu độc lập. Che thông tin người gửi/nhận **trước khi chụp**. Chỉ dùng hộp của nhóm hoặc đã được chủ sở hữu cho phép; không làm hỏng hàng có giá trị.

Với mỗi hộp, chụp sáu mặt và hai cận cảnh lúc gửi; lúc nhận chụp lại với góc/ánh sáng khác. Giữ các trường hợp nguyên trạng, bóng đổ, chói, bàn tay che và thay đổi có kiểm soát. Chụp bằng ít nhất hai điện thoại nếu có. Ghi nhật ký điều kiện riêng, không đưa đáp án “changed/unchanged” vào tên ảnh cho người gán nhãn.

```text
.local/real-data/photos/
  rights.json
  box-0001/dispatch/001.jpg ...
  box-0001/receipt/001.jpg ...
  box-0002/dispatch/...
  box-0002/receipt/...
```

`rights.json` lưu xác nhận của nhóm, ví dụ (chỉ điền khi đúng):

```json
{"source":"TEAM_OWNED","permission":"RESEARCH_AND_COMMERCIAL","declared_by":"mã người phụ trách","notes":"Nguồn hộp, quyền chụp, phạm vi sử dụng và vị trí lưu xác nhận"}
```

Đây là khai báo nguồn, không phải sự xác minh pháp lý tự động. Để trống khi chưa có quyền; công cụ sẽ không tạo manifest.

## Khóa trước khi gán nhãn

Từ root repo, chạy trong Docker đang có dependencies:

```powershell
docker compose -f infra/compose.yaml -f infra/compose.ml.yaml exec -T api python -m scripts.real_data build --root .local/real-data/photos --output .local/real-data/manifest-v1.json --dataset-id unihack26-real-boxes-v1
```

Tối thiểu 5 hộp để có ba split. Công cụ kiểm decode, EXIF, kích thước, checksum, split theo hộp và trùng byte giữa split. Tất cả nhãn vẫn UNKNOWN. Không ghi đè manifest; thêm ảnh tạo một version mới và phải chốt protocol lại trước train. Rà near-duplicate, cùng hộp vô tình đổi ID và tính độc lập của camera/phông nền bằng mắt; kiểm hash không thay thế bước này.

## Hai lượt độc lập

Mở `assets/annotation/index.html` bằng Chrome. Chọn mã reviewer, manifest và thư mục `photos`. Công cụ đọc ảnh tại trình duyệt, kiểm SHA-256 trước khi hiển thị, không gửi ảnh lên dịch vụ nào. Chọn cùng vùng/khác vùng, mức đủ, thay đổi, lý do và vẽ polygon. Xuất JSON thường xuyên; đóng trang sẽ mất phần chưa xuất. Mỗi reviewer bắt đầu bằng manifest trống trong cửa sổ mới; không đưa kết quả người A cho người B trước khi hoàn tất.

```powershell
docker compose -f infra/compose.yaml -f infra/compose.ml.yaml exec -T api python -m scripts.real_data review --manifest .local/real-data/manifest-v1.json --reviewer-a .local/real-data/reviewer-a.json --reviewer-b .local/real-data/reviewer-b.json --output .local/real-data/review-v1.json
```

Công cụ giữ nguyên danh tính ảnh/split từ manifest, chỉ nhận trường nhãn; kiểm reviewer khác nhau, đủ cặp và luật nhãn. Bất đồng, kể cả polygon khác nhau, yêu cầu đối chiếu và ghi adjudication riêng. Mã reviewer khác nhau không tự chứng minh hai người độc lập. Không chạy train/test khi còn xung đột ở dữ liệu được dùng cho phép đo đó.

## Nghiệm thu mô hình

Chọn model và ngưỡng trên train/validation; khóa checkpoint/config rồi mới mở test. Báo số hộp, số query/cặp, Recall@1/3, unsafe acceptance, coverage, IoU/Dice vùng có thể quan sát, false alarms theo điều kiện và khoảng tin cậy theo hộp. Không suy điểm synthetic thành độ chính xác thực. Cho đến khi có đủ dữ liệu, cả ranker và change head vẫn chỉ chạy song song để đánh giá.

## Xử lý bất đồng và khóa nhãn cuối

Tạo `decisions-v1.json` theo cấu trúc dưới đây. `resolutions` phải chứa đúng tất cả pair ID trong `conflicts` và `unresolved`, không được bỏ sót hoặc sửa các cặp đã thống nhất. Các trường nhãn lấy từ lượt người đã xem lại ảnh; không lấy từ dự đoán AI. Khi hai người đồng ý hết, dùng danh sách rỗng. Với `CHANGED`, bắt buộc có polygon quan sát được. Cặp đủ bằng chứng nhưng còn `UNKNOWN` không được tính là đã hoàn tất.

```json
{
  "manifest_sha256": "hash của manifest gốc",
  "adjudicator_id": "mã người đối chiếu bất đồng",
  "resolutions": [{
    "pair_id": "ID cần giải quyết",
    "correspondence": "MATCH",
    "sufficiency": "INSUFFICIENT",
    "observable_change": "UNKNOWN",
    "change_polygons": [], "ignore_polygons": [],
    "reason_codes": ["BLUR"],
    "reason": "Ảnh nhận bị nhòe, không đủ chi tiết để xác nhận vùng thay đổi."
  }]
}
```

```powershell
docker compose -f infra/compose.yaml -f infra/compose.ml.yaml exec -T api python -m scripts.adjudicate_data --manifest .local/real-data/manifest-v1.json --reviewer-a .local/real-data/reviewer-a.json --reviewer-b .local/real-data/reviewer-b.json --decisions .local/real-data/decisions-v1.json --output .local/real-data/labels-v1.json
```

File đầu ra giữ cả hai lượt gốc, hash mỗi lượt, người và lý do xử lý bất đồng. Chỉ có mã người khác nhau không chứng minh độc lập. Quyền dữ liệu và rà near-duplicate vẫn cần được người phụ trách kiểm riêng. Không sửa file kết luận để đổi trạng thái gate.

## Chạy đánh giá có thể tái lập

Ba lệnh tách riêng để bước suy luận không đọc nhãn. `freeze` ghi hash mã nguồn, preprocessing, checkpoint, model card và calibration đang dùng. `predict` kiểm lại hash ảnh gốc/quyền nguồn, dùng đúng tập candidate đã khóa, không tải dữ liệu Kaggle về máy. Lệnh chấm chỉ đọc dự đoán đã có. Mọi file đầu ra tạo mới bằng chế độ không ghi đè.

```powershell
docker compose -f infra/compose.yaml -f infra/compose.ml.yaml exec -T worker python -m scripts.evaluate_real freeze --manifest .local/real-data/manifest-v1.json --model baseline --split test --output .local/real-data/baseline-protocol-v1.json
docker compose -f infra/compose.yaml -f infra/compose.ml.yaml exec -T worker python -m scripts.evaluate_real predict --manifest .local/real-data/manifest-v1.json --protocol .local/real-data/baseline-protocol-v1.json --root .local/real-data/photos --output .local/real-data/baseline-predictions-v1.json
docker compose -f infra/compose.yaml -f infra/compose.ml.yaml exec -T worker python -m scripts.evaluate_real score --manifest .local/real-data/manifest-v1.json --protocol .local/real-data/baseline-protocol-v1.json --labels .local/real-data/labels-v1.json --predictions .local/real-data/baseline-predictions-v1.json --output .local/real-data/baseline-evaluation-v1.json
```

Lặp quy trình với `--model ranker` và `--model change`, dùng tên file riêng. `ranker` đo chọn ảnh/độ đủ; `change` dùng lựa chọn của baseline và đo change head trên cặp đó. Chưa phải ablation ranker + change tích hợp thành quyết định sản phẩm. Muốn chọn ngưỡng mới phải dùng validation và tạo protocol version mới trước khi mở test.

Mỗi số đo có tử số, mẫu số; mẫu số bằng không trả `null`. Báo lỗi chấp nhận trên số đã nhận và trên tất cả query cùng coverage. IoU/Dice/change recall chỉ trên cặp được chọn mà nhãn người cho là đúng và đủ; phải đọc cùng ranking/coverage, không dùng riêng làm độ chính xác toàn hệ thống. Từ chối một cặp đủ bằng chứng được tính là bỏ sót thay đổi. Vùng ignore bị loại khỏi cả dự đoán và nhãn, vùng dương bị ignore toàn bộ không trở thành kết quả hoàn hảo. Raster tối đa 512px; vết rất nhỏ cần protocol độ phân giải riêng. Bootstrap 1.000 lần theo hộp, không theo từng ảnh; mẫu nhỏ hoặc không quan sát lỗi không chứng minh rủi ro bằng không.

Các fixture tự sinh chỉ chứng minh công cụ chạy và chặn lỗi protocol. Chưa có kết quả hộp thật. Hash cấu hình không tự chứng minh rằng con người chưa xem test labels trước đó; nhóm cần ghi nhật ký thử nghiệm riêng.
