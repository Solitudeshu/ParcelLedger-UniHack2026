import {test, expect} from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {mnemonicToAccount} from "viem/accounts";

test("wallet binding and cancellation through the actual API", async({page},testInfo)=>{
  // Dedicated Anvil test account. Never uses a personal browser profile/wallet.
  const account = mnemonicToAccount("test test test test test test test test test test test junk",{addressIndex:2});
  let reject = false;
  await page.exposeBinding("testWalletRequest",async(_source,args:{method:string;params?:unknown[]})=>{
    if (["eth_accounts","eth_requestAccounts"].includes(args.method)) return [account.address];
    if (args.method === "eth_chainId") return "0x7a69";
    if (args.method === "wallet_switchEthereumChain") return null;
    if (args.method === "eth_signTypedData_v4") {
      if (reject) return {testRejection:true};
      return account.signTypedData(JSON.parse(String(args.params![1])));
    }
    throw new Error("Unsupported test wallet method "+args.method);
  });
  await page.addInitScript(()=>{
    const host = window as unknown as {testWalletRequest:(args:unknown)=>Promise<unknown>;ethereum:unknown};
    host.ethereum={request:async(args:unknown)=>{
      const result=await host.testWalletRequest(args);
      if (result && typeof result === "object" && "testRejection" in result) throw Object.assign(new Error("User rejected request"),{code:4001});
      return result;
    }};
  });
  await page.goto("/");
  await page.getByRole("button",{name:"Vào vai người gửi"}).click();
  await page.getByRole("button", {name: "Kiện mẫu", exact: true}).click();
  await page.getByRole("button",{name:"Trải nghiệm quy trình"}).click();
  await expect(page.getByRole("heading",{name:"Đèn gốm · Shop Mộc"})).toBeVisible();
  await page.getByRole("button",{name:"Mở quy trình ký quỹ"}).click();
  const connect=page.getByRole("button",{name:/Kết nối.*ví/});
  await expect(connect).toBeEnabled();
  await connect.click();
  await expect(page.getByText("Ví:",{exact:false}).last()).toContainText(account.address.toLowerCase().slice(0,10));
  reject=true;
  await connect.click();
  await expect(page.getByRole("alert").filter({hasText:"Bạn đã hủy yêu cầu trong ví"})).toBeVisible();
  const result=await new AxeBuilder({page}).withTags(["wcag2a","wcag2aa","wcag21aa"]).analyze();
  expect(result.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))).toEqual([]);
  const width=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth}));
  expect(width.scroll).toBeLessThanOrEqual(width.width);
  await page.screenshot({path:`artifacts/evaluation/${testInfo.project.name}-wallet.png`,fullPage:true});
});
