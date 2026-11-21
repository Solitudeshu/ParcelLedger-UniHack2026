"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { ArrowLeft, ArrowRight, Box, Camera, Check, FileCheck2, ImageIcon, LogIn, PackageOpen, ScanLine, Store } from "lucide-react";
import beforePhoto from "@/assets/truoc-khi-gui.webp";
import afterPhoto from "@/assets/luc-nhan.webp";

export function Logo() {
  return <div className="brand" aria-label="ParcelLedger"><span className="brand-mark"><Box size={24}/></span><span>Parcel<span className="brand-light">Ledger</span></span></div>;
}

function ParcelPreview() {
  const [after, setAfter] = useState(true);
  return <figure className="parcel-preview">
    <div className="preview-toolbar">
      <span><ScanLine size={18}/> Đối chiếu kiện hàng</span>
      <div className="photo-switch" role="group" aria-label="Thời điểm chụp">
        <button aria-pressed={!after} onClick={() => setAfter(false)}>Lúc gửi</button>
        <button aria-pressed={after} onClick={() => setAfter(true)}>Lúc nhận</button>
      </div>
    </div>
    <div className="preview-photo">
      <img src={after ? afterPhoto.src : beforePhoto.src} width="1280" height="960" alt={after ? "Mặt trước thùng carton lúc nhận có vết móp dưới dấu hai mũi tên." : "Cùng mặt trước thùng carton lúc gửi, bề mặt còn phẳng."}/>
      {after && <span className="dent-outline" aria-hidden="true"/>}
    </div>
    <figcaption><span className="preview-result" aria-live="polite">{after ? <ScanLine size={19}/> : <Check size={19}/>}
      {after ? "Vết móp ở mặt trước" : "Mặt hộp trước khi gửi"}</span><small>Ảnh mô phỏng do AI tạo</small></figcaption>
  </figure>;
}

type EntryProps = {
  demoEnabled: boolean; joining: boolean; busy: string; error: string;
  onRole: (role: "MERCHANT" | "BUYER") => void; onDemo: () => void;
  onLogin: (email: string, password: string, buyer: boolean) => void;
};

export function EntryScreen({demoEnabled, joining, busy, error, onRole, onDemo, onLogin}: EntryProps) {
  const [showLogin, setShowLogin] = useState(!demoEnabled || joining);
  const [buyer, setBuyer] = useState(joining);
  function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    onLogin(String(values.get("email")), String(values.get("password")), buyer);
  }
  return <main className="entry-shell">
    <header className="entry-header"><Logo/>{demoEnabled && !joining && <button className={showLogin ? "return-button" : "button secondary"} aria-expanded={showLogin} disabled={!!busy} onClick={() => setShowLogin(!showLogin)}>{showLogin ? <><ArrowLeft aria-hidden="true" size={20} strokeWidth={2.5}/>Về trang đầu</> : <><LogIn size={16}/>Đăng nhập</>}</button>}</header>
    <div className="entry-layout">
      <section className="entry-copy">
        {showLogin ? <>
          <h1>{joining ? "Mở hồ sơ được mời" : "Đăng nhập"}</h1>
          <p>{joining ? "Đăng nhập bằng tài khoản người nhận để xem ảnh của shop và thêm ảnh lúc nhận." : "Mở hồ sơ kiện hàng của bạn để thêm ảnh hoặc kiểm tra biên bản."}</p>
          {joining && demoEnabled && <button className="button primary" disabled={!!busy} onClick={() => onRole("BUYER")}><PackageOpen size={18}/>Vào vai người nhận</button>}
          <form onSubmit={login} className="entry-form">
            {!joining && <label>Không gian làm việc<select value={buyer ? "buyer" : "merchant"} onChange={e => setBuyer(e.target.value === "buyer")}><option value="merchant">Quản lý cửa hàng</option><option value="buyer">Kiện hàng tôi nhận</option></select></label>}
            <label>Email<input name="email" type="email" autoComplete="username" placeholder="ban@cuahang.vn" required/></label>
            <label>Mật khẩu<input name="password" type="password" autoComplete="current-password" required/></label>
            <button className="button primary full" disabled={!!busy}>Đăng nhập<ArrowRight size={17}/></button>
          </form>
        </> : <>
          <h1>Xử lý khiếu nại <em>giao hàng</em></h1>
          <p>Khi khách báo kiện bị móp, tra mã đơn để xem ảnh lúc gửi và lúc nhận. Cửa hàng và khách cùng kiểm tra, xác nhận và lưu biên bản.</p>
          <div className="entry-actions"><button className="button primary sample-button" disabled={!!busy} onClick={() => {setBuyer(false); setShowLogin(true);}}>Đăng nhập cửa hàng<ArrowRight size={18}/></button>
            <button className="button secondary" disabled={!!busy} onClick={() => {setBuyer(true); setShowLogin(true);}}>Đăng nhập người nhận</button></div>
          <button className="button ghost" disabled={!!busy} onClick={onDemo}>Thử với kiện hàng mẫu<ArrowRight size={16}/></button>
          <div className="role-picker"><h2>Dùng thử bằng tài khoản mẫu</h2><div>
            <button disabled={!!busy} onClick={() => onRole("MERCHANT")}><Store size={19}/><span>Vào vai người gửi</span><ArrowRight size={15}/></button>
            <button disabled={!!busy} onClick={() => onRole("BUYER")}><PackageOpen size={19}/><span>Vào vai người nhận</span><ArrowRight size={15}/></button>
          </div></div>
        </>}
        {busy && <p className="entry-status" role="status">{busy}</p>}
        {error && <div role="alert" className="alert error">{error}</div>}
      </section>
      <ParcelPreview/>
    </div>
    <section className="handoff-guide" aria-label="Các bước giao nhận">
      <div><Camera/><span><strong>Shop chụp ảnh</strong><small>Lưu tình trạng hộp trước khi gửi.</small></span></div>
      <div><ImageIcon/><span><strong>Người nhận đối chiếu</strong><small>Chụp cùng mặt hộp để tìm điểm khác biệt.</small></span></div>
      <div><FileCheck2/><span><strong>Hai bên xác nhận</strong><small>Kiểm tra ảnh và thống nhất nội dung biên bản.</small></span></div>
    </section>
  </main>;
}
