import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { writeFileSync } from "node:fs";

test("WCAG AA login, dashboard and report", async ({page}, testInfo) => {
  await page.goto("/");
  await expect(page.getByRole("button", {name: "Vào vai người gửi"})).toBeVisible();
  const reports = [];
  for (const screen of ["login", "dashboard", "report"]) {
    if (screen === "dashboard") {
      await page.getByRole("button", {name: "Vào vai người gửi"}).click();
      await expect(page.getByRole("heading", {name: "Hồ sơ kiện hàng."})).toBeVisible();
    }
    if (screen === "report") {
      await page.getByRole("button", {name: "Kiện mẫu", exact: true}).click();
      await page.getByRole("button", {name: "Trải nghiệm quy trình"}).click();
      await expect(page.getByText("Đã tìm được vùng tương ứng để kiểm tra")).toBeVisible({timeout: 60_000});
      await page.getByRole("button", {name: "Xem biên bản"}).click();
      await expect(page.getByRole("heading", {name: "Biên bản tình trạng kiện hàng"})).toBeVisible();
    }
    const result = await new AxeBuilder({page}).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    reports.push({screen, violations: result.violations});
  }
  writeFileSync(`artifacts/evaluation/${testInfo.project.name}-accessibility.json`, JSON.stringify(reports, null, 2));
  expect(reports.flatMap(r => r.violations.map(v => ({screen: r.screen, id: v.id,
    nodes: v.nodes.map(n => ({target: n.target, summary: n.failureSummary}))})))).toEqual([]);
});
