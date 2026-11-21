import { Layers3, Plus, ShieldCheck } from "lucide-react";
import { Logo } from "./entry-screen";
import { merchantQueues, type InboxFilters } from "./shipment-inbox";

type Props = {
  inbox: InboxFilters;
  viewingCase: boolean;
  networkLabel?: string;
  onCreate: () => void;
  onQueue: (stage: string) => void;
};

export function MerchantSidebar({inbox, viewingCase, networkLabel, onCreate, onQueue}: Props) {
  const current = viewingCase || inbox.source !== "work" ? null : inbox.stage;
  return <aside className="sidebar merchant-menu"><Logo/>
    <nav aria-label="Điều hành cửa hàng">
      <div className="workspace-label">Quản lý cửa hàng</div>
      <button className="nav-item" title="Lập hồ sơ giao nhận" onClick={onCreate}><Plus size={19}/>Lập hồ sơ giao nhận</button>
      <button className="nav-item" title="Toàn bộ hồ sơ" aria-current={current === "" ? "page" : undefined} onClick={() => onQueue("")}>
        <Layers3 size={19}/>Toàn bộ hồ sơ</button>
      <div className="workspace-label">Theo tiến độ</div>
      {merchantQueues.map(({stage, label, icon: Icon}) => <button key={stage} className="nav-item" title={label}
        aria-current={current === stage ? "page" : undefined} onClick={() => onQueue(stage)}><Icon size={19}/>{label}</button>)}
    </nav>
    <div className="sidebar-bottom"><ShieldCheck size={16}/>{networkLabel}</div>
  </aside>;
}
