import type { Metadata } from "next";
import "./globals.css";
import "./workflow.css";
import "./report.css";

export const metadata: Metadata = {
  title: "ParcelLedger · Đối chiếu ảnh giao nhận",
  description: "Đối chiếu ảnh giao nhận, cùng kiểm tra biên bản và thống nhất cách xử lý kiện hàng.",
};

export default function Layout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="vi"><body>{children}</body></html>;
}
