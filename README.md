# ParcelLedger · UniHack 2026

ParcelLedger cho người gửi và người nhận lập một biên bản chung từ ảnh chụp kiện hàng. Người gửi lưu ảnh trước khi giao. Người nhận bổ sung ảnh khi nhận và xem vùng cần kiểm tra. Hai bên có thể ký đúng phiên bản biên bản bằng họ tên hoặc nét vẽ, rồi tải PDF và JSON. Với kiện có ký quỹ, hai ví còn ký điều khoản, mã băm biên bản và phương án chia tiền trên Avalanche Fuji.

## Xem sản phẩm

| Tư liệu | Nội dung |
|---|---|
| [Demo sản phẩm](output/demo/ParcelLedger_UniHack_Demo_truc_tiep.mp4) · [phụ đề tiếng Việt](output/demo/ParcelLedger_UniHack_Demo_truc_tiep.vi.srt) | Khoảng 1 phút 33 giây, từ hồ sơ giao nhận đến PDF có hai chữ ký. |
| [Demo Avalanche Fuji](output/demo/KienChung_Avalanche_Fuji_60s.mp4) | Ca thử ký quỹ riêng trên Fuji, khoảng 60 giây. |
| [Biên bản mẫu có hai chữ ký](output/pdf/Bien_ban_mau_co_chu_ky.pdf) · [slide thuyết trình](output/pitch/KienChung_UniHack2026.html) | PDF minh họa luồng ký bằng tài khoản. Tải slide HTML và mở trong trình duyệt để dùng các trang tương tác. |

## Kiểm chứng hai ca Fuji

Ngày 21/11/2026, nhóm đã triển khai hợp đồng trên **Avalanche Fuji C-Chain, chain ID 43113**. [Factory](https://build.avax.network/explorer/fuji/c-chain/address/0x7014eb6500e3d7774b3a19424019ea3e035f29c9) tạo một hợp đồng ký quỹ cho mỗi case. Mỗi hợp đồng nhận 10 kcTEST từ người gửi. Bốn giao dịch rút đã chuyển đủ số token dưới đây.

| Ca thử | Người gửi đã rút | Người nhận đã rút | Số dư quỹ cuối | Giao dịch phân bổ |
|---|---:|---:|---:|---|
| [Hai bên đồng thuận](https://build.avax.network/explorer/fuji/c-chain/address/0x97dAbFf00BdB800f48C7f290A77a8Add586b922a) | 7 kcTEST | 3 kcTEST | 0 | [settle](https://build.avax.network/explorer/fuji/c-chain/tx/0x791ddea8fa84a8b5a248e07bd7154b12c985d97ac1e58d3ea65287a8fb25edf7) |
| [Tranh chấp](https://build.avax.network/explorer/fuji/c-chain/address/0x2e1C7D26a6358d287fFDF36590bf982B96ffb5C1) | 6 kcTEST | 4 kcTEST | 0 | [resolveDispute](https://build.avax.network/explorer/fuji/c-chain/tx/0xdbf754dfbec2d1a3700b093541573df5e5b6348fab5503c596d338ac0e82b9c0) |

Hồ sơ đồng thuận mang mã **CHAIN-1795258710366**, phiên bản biên bản **1**. Tải [JSON gốc](artifacts/evaluation/fuji-evidence/report-v1.json), tính SHA-256 trên nguyên byte và đối chiếu với mã trong [PDF của cùng hồ sơ](artifacts/evaluation/fuji-evidence/report-v1.pdf) và sự kiện `ReportAccepted` tại block 61421942. [Giao dịch ghi nhận biên bản](https://build.avax.network/explorer/fuji/c-chain/tx/0x32b0e646fe314a8f9dc56d2187619d039174913bb86b2828d40c15fe55395770)

```powershell
(Get-FileHash artifacts/evaluation/fuji-evidence/report-v1.json -Algorithm SHA256).Hash.ToLowerInvariant()
# afd125ad4ab4602d19961301002e4d45995f8f5a7eb71be46b6b1f2c1d404908
```

Xem [manifest triển khai](artifacts/deployments/fuji.json), [20 giao dịch nghiệp vụ](artifacts/evaluation/fuji-chain-integration.json), [receipt, sự kiện và số tiền từng lần rút](artifacts/evaluation/fuji-evidence/rpc.json), [kết quả đối chiếu biên bản](artifacts/evaluation/fuji-evidence/report-check.json) và [10 trang explorer đã kiểm tra](artifacts/evaluation/fuji-evidence/explorer.json). Các trang explorer và tệp bằng chứng có thể xem ngay, kể cả khi chưa chạy app.

Test AVAX trả phí mạng, kcTEST là token ký quỹ thử nghiệm. Safe yêu cầu hai chữ ký khác nhau trong ba chủ ví. Phép mô phỏng với một chữ ký trả `SignatureInvalid`; giao dịch dùng hai chữ ký đã thành công. Trong demo, một người vận hành ba khóa Safe. Ảnh minh họa là dữ liệu mẫu. Chế độ `GEOMETRY_BASELINE` gợi ý khác biệt nhìn thấy để hai bên kiểm tra. Phương án phân bổ do các bên hoặc người phân xử ký.

## Chạy ứng dụng

Dùng Docker Desktop ở chế độ Linux containers và Docker Compose từ 2.24.4. Các lệnh PowerShell sau chạy tại gốc repo. Web và API mở trên máy của bạn. Manifest công khai đi kèm cho phép app đọc đúng hợp đồng mà không cần ví, tiền hoặc triển khai lại.

```powershell
function Invoke-ParcelCompose {
    docker compose -p kienchung-fuji -f infra/compose.yaml -f infra/compose.fuji-demo.yaml @args
    if ($LASTEXITCODE) { throw "Docker Compose failed" }
}
Invoke-ParcelCompose build api web minio
Invoke-ParcelCompose up -d --no-build api worker outbox reconciler
Invoke-ParcelCompose exec -T api python -m scripts.bootstrap
Invoke-ParcelCompose up -d --no-build web
Invoke-RestMethod http://127.0.0.1:28000/health/ready
Invoke-RestMethod http://127.0.0.1:23000/api/v1/config
```

Mở [ParcelLedger](http://127.0.0.1:23000) và chọn vai người gửi. Trên máy mới, tạo bốn hồ sơ ảnh mẫu bằng `Invoke-ParcelCompose exec -T api python -m scripts.seed_showcase`. Để ký biên bản thường, mở cùng hồ sơ ở vai người nhận trong phiên trình duyệt riêng. Trên máy demo đã có volume dữ liệu, tìm mã `CHAIN-1795258710366` để xem lại ca Fuji. Bản clone mới có database riêng nên lịch sử hồ sơ đó được cung cấp qua bộ bằng chứng ở trên.

## Mã nguồn và kiểm thử

- `apps/web` cung cấp giao diện Next.js cho hai vai. `services/api` quản lý hồ sơ, ảnh, phân quyền và xuất biên bản. `services/worker` xử lý ảnh, tạo báo cáo và đồng bộ sự kiện on-chain.
- `contracts/src` chứa DemoToken, EscrowFactory và ShipmentEscrow. `packages/chain` chứa ABI. `infra` chứa migrations và Docker Compose.
- `ml` và `notebooks` chứa mã nghiên cứu đối chiếu ảnh. [Đánh giá đối chiếu ảnh](artifacts/evaluation/correspondence-final-test-v1.json) và [đánh giá trên Tampar](artifacts/evaluation/tampar-matching-v1.json) ghi điều kiện và giới hạn của từng thí nghiệm. Profile demo dùng baseline và có thể chạy độc lập với các checkpoint nghiên cứu.

Với Node.js 24 và npm, chạy lần lượt:

```powershell
npm ci
npm run test:schemas
npm run typecheck
npm run build
node scripts/foundry.mjs forge test
Invoke-ParcelCompose exec -T api python -m pytest -q tests/python/test_report_pdf.py tests/python/test_workflow.py tests/python/test_reconciler.py
```

Trên Linux/macOS, cài Foundry để lệnh `forge` có trong PATH. Trên Windows x64, wrapper dùng Forge cài qua npm. Bộ kiểm thử API sử dụng database kiểm thử riêng. Việc xem demo và kiểm chứng hai ca đã lưu chỉ cần các thao tác đọc.
