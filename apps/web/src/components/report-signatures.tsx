"use client";

import { useRef, useState } from "react";
import { PenLine } from "lucide-react";
import { reportTime, type Report, type SignatureInput } from "@/lib/api";

type Point = [number, number];

function lines(strokes: Point[][]) {
  return strokes.map((line, i) => <polyline key={i} points={line.map(([x,y]) => `${x * .03},${y * .01}`).join(" ")}
    fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>);
}

function SignatureForm({version, busy, onSave, onCancel}: {
  version: number; busy: boolean; onSave: (mark: SignatureInput) => void; onCancel: () => void;
}) {
  const [method, setMethod] = useState<SignatureInput["method"]>("DRAWN");
  const [name, setName] = useState("");
  const [strokes, setStrokes] = useState<Point[][]>([]);
  const [draft, setDraft] = useState<Point[]>([]);
  const drawing = useRef<{id: number; points: Point[]} | null>(null);
  function point(e: React.PointerEvent<SVGSVGElement>): Point {
    const rect = e.currentTarget.getBoundingClientRect();
    const normalize = (value: number) => Math.round(Math.min(1, Math.max(0, value)) * 10000);
    return [normalize((e.clientX - rect.left) / rect.width), normalize((e.clientY - rect.top) / rect.height)];
  }
  function extend(e: React.PointerEvent<SVGSVGElement>) {
    const active = drawing.current;
    if (!active || active.id !== e.pointerId) return;
    const next = point(e), last = active.points.at(-1)!;
    if (next[0] !== last[0] || next[1] !== last[1]) {
      if (active.points.length >= 512) active.points = active.points.filter((_, i) => i % 2 === 0);
      active.points.push(next);
    }
    setDraft([...active.points]);
  }
  function cancelStroke() {drawing.current = null; setDraft([]);}
  return <form className="signature-form" onSubmit={e => {e.preventDefault();
    if (name.trim() && (method === "TYPED" || strokes.length)) onSave({method, name: name.trim(), strokes: method === "DRAWN" ? strokes : []});
  }}>
    <label>Họ tên người ký<input autoFocus autoComplete="name" maxLength={120} value={name} required
      onChange={e => setName(e.target.value)}/></label>
    <fieldset disabled={busy}><legend>Cách ký</legend>
      <label><input type="radio" checked={method === "DRAWN"} onChange={() => setMethod("DRAWN")}/>Vẽ chữ ký</label>
      <label><input type="radio" checked={method === "TYPED"} onChange={() => setMethod("TYPED")}/>Ký bằng họ tên</label>
    </fieldset>
    {method === "DRAWN" ? <><p>Ký trong ô bằng chuột hoặc ngón tay.</p>
      <svg className="sign-pad" viewBox="0 0 300 100" aria-label="Ô vẽ chữ ký"
        onPointerDown={e => {if (!busy && !drawing.current && e.button === 0 && strokes.length < 32) {
          const points = [point(e)]; drawing.current = {id: e.pointerId, points}; setDraft(points); e.currentTarget.setPointerCapture(e.pointerId);
        }}} onPointerMove={extend} onPointerUp={e => {
          if (drawing.current?.id !== e.pointerId) return;
          extend(e); const line = drawing.current.points;
          if (line.length >= 2) setStrokes(previous => [...previous, line]); cancelStroke();
        }} onPointerCancel={cancelStroke} onLostPointerCapture={cancelStroke}>{lines([...strokes, draft])}</svg>
      <button type="button" className="button ghost" disabled={busy || !strokes.length} onClick={() => setStrokes([])}>Xóa nét ký</button>
    </> : <div className="typed-sign">{name.trim() || "Họ tên sẽ xuất hiện ở đây"}</div>}
    <p>Khi bấm ký, bạn xác nhận nội dung phiên bản {version}. Họ tên, chữ ký và thời điểm ký sẽ xuất hiện trên biên bản của hai bên.</p>
    <div className="button-row"><button className="button primary" disabled={busy || !name.trim() || (method === "DRAWN" && !strokes.length)}>
      Ký phiên bản {version}</button><button type="button" className="button secondary" disabled={busy} onClick={onCancel}>Hủy</button></div>
  </form>;
}

export function ReportSignatures({report, ownRole, canSign, busy, onSign}: {report: Report; ownRole: "MERCHANT"|"BUYER";
  canSign: boolean; busy: boolean; onSign: (mark: SignatureInput) => void}) {
  const [signing, setSigning] = useState(false);
  const signed = report.confirmations.some(c => c.role === ownRole && c.signature);
  return <section className="signatures"><h3>Chữ ký biên bản</h3>
    <p>Bên gửi và bên nhận ký để xác nhận tình trạng kiện ghi trong phiên bản {report.version}. Chữ ký sẽ hiện trên bản PDF.</p>
    <div className="signature-parties">{(["MERCHANT","BUYER"] as const).map(role => {
      const review = report.confirmations.find(c => c.role === role), mark = review?.signature;
      return <div key={role}><strong>{role === "MERCHANT" ? "Bên gửi" : "Bên nhận"}</strong>
        {mark ? <>{mark.method === "DRAWN" && <svg role="img" aria-label="Chữ ký tay" viewBox="0 0 300 100">{lines((mark.strokes ?? []) as Point[][])}</svg>}
          <b>{mark.name}</b><small>{mark.method === "DRAWN" ? "Đã ký tay" : "Đã ký bằng họ tên"}<br/>{reportTime(mark.signed_at)} (GMT+7)</small></> :
          <><span>Chưa ký</span>{review && <small>Đã kiểm tra nội dung</small>}</>}
      </div>;
    })}</div>
    {canSign && !signed && (signing ? <SignatureForm version={report.version} busy={busy} onCancel={() => setSigning(false)} onSave={onSign}/> :
      <button className="button primary" disabled={busy} onClick={() => setSigning(true)}><PenLine size={16}/>Ký biên bản</button>)}
  </section>;
}
