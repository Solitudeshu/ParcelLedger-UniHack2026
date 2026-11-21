import {test,expect} from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {setupDisputedCase} from "../../scripts/chain-fixture.mjs";

test("two Safe owners review and resolve a real local dispute through the UI",async({page},testInfo)=>{
  test.setTimeout(120000);
  const fixture=await setupDisputedCase();
  let wallet=fixture.owners[0];
  await page.exposeBinding("testArbitrationWallet",async(_source,args:{method:string;params?:unknown[]})=>{
    if(["eth_accounts","eth_requestAccounts"].includes(args.method))return [wallet.account.address];
    if(args.method==="eth_chainId")return "0x7a69";
    if(args.method==="wallet_switchEthereumChain")return null;
    if(args.method==="eth_signTypedData_v4")return wallet.signTypedData(JSON.parse(String(args.params![1])));
    if(args.method==="eth_sendTransaction") {
      const transaction=args.params![0] as {from:string};
      expect(transaction.from.toLowerCase()).toBe(wallet.account.address.toLowerCase());
    }
    return fixture.env.client.request(args);
  });
  await page.addInitScript(()=>{
    const host=window as unknown as {testArbitrationWallet:(args:unknown)=>Promise<unknown>;ethereum:unknown};
    host.ethereum={request:(args:unknown)=>host.testArbitrationWallet(args)};
  });
  await page.goto("/");
  await page.getByRole("button",{name:"Vào vai người gửi"}).click();
  await page.getByText("Thời gian xử lý hồ sơ",{exact:true}).click();
  await expect(page.getByText("Đối chiếu đến bản nháp",{exact:true})).toBeVisible();
  await page.getByRole("textbox",{name:"Tìm hồ sơ"}).fill(fixture.shipment.reference);
  await page.getByRole("button").filter({hasText:fixture.shipment.reference}).click();
  await page.getByRole("button",{name:"Mở quy trình ký quỹ"}).click();
  await expect(page.getByRole("heading",{name:"Quyết định của hội đồng Safe"})).toBeVisible();
  await page.getByLabel("Phần cho người nhận khi phân xử (kcTEST)").fill("4");
  await page.getByLabel("Căn cứ quyết định của người phân xử").fill("Kiểm thử giao diện: đã xem bằng chứng và thống nhất phân bổ thử nghiệm cho hai bên.");
  await page.getByRole("button",{name:"Lưu phương án để hội đồng kiểm tra"}).click();
  await expect(page.getByText("0/2 chữ ký cần thiết",{exact:true})).toBeVisible();
  await page.getByRole("button",{name:"Ký với ví thành viên Safe"}).click();
  await expect(page.getByText("1/2 chữ ký cần thiết",{exact:true})).toBeVisible();
  await expect(page.getByRole("button",{name:"Gửi quyết định đã đủ chữ ký"})).toHaveCount(0);
  wallet=fixture.owners[1];
  await page.getByRole("button",{name:"Ký với ví thành viên Safe"}).click();
  await expect(page.getByText("2/2 chữ ký cần thiết",{exact:true})).toBeVisible();
  const accessibility=await new AxeBuilder({page}).withTags(["wcag2a","wcag2aa","wcag21aa"]).analyze();
  expect(accessibility.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))).toEqual([]);
  await page.screenshot({path:`artifacts/evaluation/${testInfo.project.name}-arbitration.png`,fullPage:true});
  await page.getByRole("button",{name:"Gửi quyết định đã đủ chữ ký"}).click();
  await expect(page.getByText("Đã phân bổ",{exact:true})).toBeVisible({timeout:30000});
  const final=await fixture.merchant.call(fixture.base+"/chain");
  expect(final.case.merchantClaimable).toBe("6000000");
  expect(final.case.buyerClaimable).toBe("4000000");
  expect(final.case.accounting_consistent).toBe(true);
  const width=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth}));
  expect(width.scroll).toBeLessThanOrEqual(width.width);
});
