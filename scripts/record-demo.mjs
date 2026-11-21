// Records the real local application. Fixtures and single-operator test wallets
// are disclosed in the video, never presented as physical/customer evidence.
import {chromium, expect} from "@playwright/test";
import {mkdirSync, writeFileSync} from "node:fs";
import {resolve} from "node:path";
import {setupDisputedCase} from "./chain-fixture.mjs";

mkdirSync("output/demo", {recursive:true});
const fixture = await setupDisputedCase();
const browser = await chromium.launch({channel:"chrome", headless:true});
const context = await browser.newContext({viewport:{width:1440,height:1000},
  recordVideo:{dir:resolve(".local/demo-recording"),size:{width:1440,height:1000}}});
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
let wallet = fixture.owners[0];
await page.exposeBinding("demoTestWallet", async (_source,args) => {
  if (["eth_accounts","eth_requestAccounts"].includes(args.method)) return [wallet.account.address];
  if (args.method === "eth_chainId") return "0x7a69";
  if (args.method === "wallet_switchEthereumChain") return null;
  if (args.method === "eth_signTypedData_v4") return wallet.signTypedData(JSON.parse(String(args.params[1])));
  if (args.method === "eth_sendTransaction" && args.params[0].from.toLowerCase() !== wallet.account.address.toLowerCase()) throw new Error("DEMO_SIGNER_MISMATCH");
  return fixture.env.client.request(args);
});
await page.addInitScript(() => {window.ethereum = {request:args => window.demoTestWallet(args)};});
const started = performance.now();
const captions = [];
async function caption(text, seconds=4) {
  const start = (performance.now()-started)/1000;
  await page.evaluate(text => {
    let node = document.getElementById("recording-caption");
    if (!node) {
      node = document.createElement("aside");
      node.id = "recording-caption";
      Object.assign(node.style,{position:"fixed",left:"8%",right:"8%",bottom:"16px",zIndex:"2147483647",padding:"16px 24px",
        borderRadius:"12px",background:"#10291fee",color:"#ffffff",font:"600 21px/1.5 system-ui",textAlign:"center",pointerEvents:"none",boxShadow:"0 4px 24px #0003"});
      document.body.append(node);
    }
    node.textContent = text;
  },text);
  await page.waitForTimeout(seconds*1000);
  captions.push({start,end:(performance.now()-started)/1000,text});
  await page.locator("#recording-caption").evaluate(node => node.remove());
}
let success = false;
try {
  await page.goto("http://127.0.0.1:3000");
  await expect(page.getByRole("button",{name:"Vào vai người gửi"})).toBeVisible();
  await caption("ParcelLedger — demo local bằng ảnh tự sinh và token thử nghiệm. Chưa phải đánh giá hộp thật hoặc giao dịch Fuji.",6);
  await page.getByRole("button",{name:"Vào vai người gửi"}).click();
  await expect(page.getByRole("heading",{name:"Hồ sơ kiện hàng."})).toBeVisible();
  await caption("Shop lưu ảnh trước gửi; người nhận đối chiếu vùng cần kiểm tra trong cùng hồ sơ.");
  await page.getByRole("button",{name:"Trải nghiệm quy trình"}).click();
  await expect(page.getByText("Đã tìm được vùng tương ứng để kiểm tra")).toBeVisible({timeout:60000});
  await caption("Hệ thống chọn ảnh gửi tương ứng và kiểm độ đủ bằng chứng. Vùng đánh dấu chỉ là khác biệt nhìn thấy.",6);
  await page.getByText("AI đối chiếu nâng cao · thử nghiệm",{exact:true}).click();
  await expect(page.getByAltText("Ảnh nhận với vùng gợi ý từ AI nâng cao")).toBeVisible();
  await page.getByAltText("Ảnh nhận với vùng gợi ý từ AI nâng cao").scrollIntoViewIfNeeded();
  await caption("Transformer đã train 30 epoch trên thay đổi mô phỏng từ Parcel3D. AI chọn cặp ảnh riêng và gợi ý vùng tím; chưa kiểm chứng trên hộp thật.",7);
  await page.getByRole("button",{name:"Xem biên bản"}).click();
  await expect(page.getByRole("heading",{name:"Biên bản tình trạng kiện hàng"})).toBeVisible();
  await caption("Biên bản tiếng Việt dẫn lại ảnh và vùng quan sát. AI không xác định ai gây hỏng hoặc mức bồi hoàn.",5);
  await page.getByRole("button",{name:"Chỉnh sửa nhận xét"}).click();
  await page.getByLabel("Ghi chú của hai bên",{exact:true}).fill("Đã xem ảnh gốc. Ghi nhận vùng cần kiểm tra; chưa xác định nguyên nhân hoặc trách nhiệm.");
  await page.getByRole("button",{name:"Lưu phiên bản mới"}).click();
  await expect(page.getByText("Phiên bản 2",{exact:true})).toBeVisible();
  await page.getByRole("button",{name:"Xác nhận đã kiểm tra",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Bạn đã kiểm tra phiên bản này"})).toBeVisible();
  await caption("Mỗi lần sửa tạo một phiên bản. Xác nhận đọc biên bản bằng tài khoản tách riêng chữ ký ví để thanh toán.",5);
  await page.goto("http://127.0.0.1:3000");
  await page.getByRole("textbox",{name:"Tìm hồ sơ"}).fill(fixture.shipment.reference);
  await page.getByRole("button").filter({hasText:fixture.shipment.reference}).click();
  await page.getByRole("button",{name:"Mở quy trình ký quỹ"}).click();
  await expect(page.getByRole("heading",{name:"Quyết định của hội đồng Safe"})).toBeVisible();
  await caption("Tình huống thứ hai: ký quỹ có tranh chấp trên Anvil local. Safe thật, ngưỡng 2/3; các ví demo do một người vận hành.",7);
  await page.getByLabel("Phần cho người nhận khi phân xử (kcTEST)").fill("4");
  await page.getByLabel("Căn cứ quyết định của người phân xử").fill("Phương án demo do người soạn: phân bổ 4 kcTEST cho người nhận, 6 kcTEST cho shop. AI không đưa ra số tiền này.");
  await page.getByRole("button",{name:"Lưu phương án để hội đồng kiểm tra"}).click();
  await expect(page.getByText("0/2 chữ ký cần thiết",{exact:true})).toBeVisible();
  await page.getByRole("button",{name:"Ký với ví thành viên Safe"}).click();
  await expect(page.getByText("1/2 chữ ký cần thiết",{exact:true})).toBeVisible();
  await caption("Một chữ ký chưa đủ: hợp đồng yêu cầu đủ hai owner khác nhau của Safe.",5);
  wallet = fixture.owners[1];
  await page.getByRole("button",{name:"Ký với ví thành viên Safe"}).click();
  await expect(page.getByText("2/2 chữ ký cần thiết",{exact:true})).toBeVisible();
  await caption("Chữ ký gắn đúng hồ sơ, bằng chứng, số tiền, nonce và thời hạn. Sửa bằng chứng sẽ làm phương án cũ mất hiệu lực.",6);
  await page.getByRole("button",{name:"Gửi quyết định đã đủ chữ ký"}).click();
  await expect(page.getByText("Đã phân bổ",{exact:true})).toBeVisible({timeout:30000});
  const final = await fixture.merchant.call(fixture.base+"/chain");
  expect(final.case.merchantClaimable).toBe("6000000");
  expect(final.case.buyerClaimable).toBe("4000000");
  expect(final.case.accounting_consistent).toBe(true);
  await caption("Hợp đồng đã ghi quyền rút 6/4 kcTEST. Ảnh ở ngoài blockchain; blockchain giữ hash, trạng thái và quyết định đã ký.",7);
  await caption("Bản tiếp theo cần kiểm chứng hộp thật, triển khai Fuji và đo lợi ích tại shop. Không dùng tiền thật trong demo.",5);
  expect(errors).toEqual([]);
  success = true;
} finally {
  const video = page.video();
  await context.close();
  if (success) await video.saveAs(resolve("output/demo/KienChung_local_walkthrough.webm"));
  await browser.close();
}
const stamp = seconds => new Date(Math.round(seconds*1000)).toISOString().slice(11,23);
writeFileSync("output/demo/KienChung_local_walkthrough.vtt","WEBVTT\n\n"+captions.map((c,i)=>`${i+1}\n${stamp(c.start)} --> ${stamp(c.end)}\n${c.text}\n`).join("\n"));
writeFileSync("output/demo/recording.json",JSON.stringify({status:"RECORDED_AND_ASSERTED",recorded_at:new Date().toISOString(),
  scope:"LOCAL_ANVIL_PROCEDURAL_FIXTURES_SINGLE_OPERATOR_WALLETS", chain_id:31337,physical_box_validation:false,fuji:false,
  arbitration_shipment_id:fixture.shipment.id,duration_seconds:(performance.now()-started)/1000,captions},null,2));
console.log(JSON.stringify({status:"RECORDED_AND_ASSERTED",video:"output/demo/KienChung_local_walkthrough.webm",scope:"LOCAL_ONLY"}));
