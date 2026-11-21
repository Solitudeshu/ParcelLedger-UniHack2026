import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {mkdirSync, writeFileSync} from "node:fs";
import {chromium} from "@playwright/test";

// Fixed loopback endpoints and Compose project: never point fixture creation at production.
const base = "http://127.0.0.1:13000";
const output = "artifacts/evaluation";
mkdirSync(output, {recursive: true});
function docker(args) {
  const result = spawnSync("docker", args, {encoding: "utf8", windowsHide: true});
  if (result.status !== 0) throw new Error("DOCKER_INSPECTION_FAILED");
  return result.stdout.trim();
}
const services = {};
for (const name of ["api", "worker", "outbox", "web"]) {
  const id = docker(["compose", "-f", "infra/compose.portable.yaml", "ps", "-q", name]);
  const [container] = JSON.parse(docker(["inspect", id]));
  assert.equal(container.State.Running, true);
  assert.ok(container.Config.User && container.Config.User !== "root" && !container.Config.User.startsWith("0:"));
  assert.equal(container.HostConfig.ReadonlyRootfs, true);
  assert.equal(container.Mounts.filter(m => m.Type === "bind").length, 0);
  services[name] = {image_id:container.Image, user:container.Config.User, read_only_root:true, host_bind_mounts:0};
}
const config = await (await fetch(base+"/api/v1/config")).json();
assert.equal(config.app_env, "local");
assert.equal(config.demo_login_enabled, true);
assert.equal(config.escrow_factory_address, null);
const browser = await chromium.launch({channel:"chrome", headless:true});
const platformResults = [];
try {
  for (const [name, viewport] of [["desktop", {width:1440,height:1000}], ["mobile", {width:390,height:844}]]) {
    const context = await browser.newContext({viewport});
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(base);
    await page.getByRole("button", {name:"Vào vai người gửi"}).click();
    await page.getByRole("heading", {name:"Hồ sơ kiện hàng."}).waitFor();
    await page.getByRole("button", {name:"Trải nghiệm quy trình"}).click();
    await page.getByText("Đã tìm được vùng tương ứng để kiểm tra").waitFor({timeout:60000});
    await page.getByRole("button", {name:"Xem biên bản"}).click();
    await page.getByRole("heading", {name:"Biên bản tình trạng kiện hàng"}).waitFor();
    await page.getByRole("button", {name:"Chỉnh sửa nhận xét"}).click();
    await page.getByLabel("Ghi chú của hai bên", {exact:true}).fill("Kiểm tra bản cài độc lập bằng ảnh procedural; chưa có ảnh hộp thật.");
    await page.getByRole("button", {name:"Lưu phiên bản mới"}).click();
    await page.getByText("Phiên bản 2", {exact:true}).waitFor();
    await page.getByRole("button", {name:"Xác nhận đã kiểm tra", exact:true}).click();
    await page.getByRole("heading", {name:"Bạn đã kiểm tra phiên bản này"}).waitFor();
    const pdfPath = await page.getByRole("link", {name:"Tải biên bản PDF"}).getAttribute("href");
    const pdf = await context.request.get(base+pdfPath);
    assert.equal(pdf.status(), 200);
    const pdfBytes = await pdf.body();
    assert.equal(pdfBytes.subarray(0,5).toString(), "%PDF-");
    const exportPath = await page.getByRole("link", {name:"Tải hồ sơ JSON"}).getAttribute("href");
    const exported = await context.request.get(base+exportPath);
    assert.equal(exported.status(), 200);
    const dimensions = await page.evaluate(() => ({scroll:document.documentElement.scrollWidth, viewport:innerWidth}));
    assert.ok(dimensions.scroll <= dimensions.viewport);
    assert.deepEqual(errors, []);
    await page.screenshot({path:`${output}/portable-${name}.png`,fullPage:true});
    platformResults.push({platform:name, workflow:"LOGIN_UPLOAD_MATCH_REPORT_REVISE_REVIEW_EXPORT", pdf_bytes:pdfBytes.length, javascript_errors:0, horizontal_overflow:false});
    await context.close();
  }
  writeFileSync(`${output}/portable-install.json`, JSON.stringify({status:"PASS", checked_at:new Date().toISOString(),
    scope:"ISOLATED_DOCKER_BASELINE_PROCEDURAL_FIXTURES", services, platforms:platformResults,
    limits:["No live Fuji or trained model in this portable baseline profile.","Fresh volumes verified on same workstation; not remote hosting or physical-box validation."]},null,2));
  console.log(JSON.stringify({status:"PASS", platforms:platformResults.map(r=>r.platform), host_source_mounts:0}));
} finally {await browser.close();}
