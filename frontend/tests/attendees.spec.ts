import { test, expect } from "@playwright/test";

test("host adds a device-free attendee and manages their voiceprint", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "打开演示会议" }).click();
  await page.getByRole("button", { name: "参会者与声纹", exact: true }).click();
  await page
    .getByRole("button", { name: "添加现场参会者", exact: true })
    .click();
  await page.getByRole("textbox", { name: "参会者姓名" }).fill("现场测试成员");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "确认添加", exact: true }).click();
  const row = page.locator(".member-row").filter({ hasText: "现场测试成员" });
  await expect(row).toContainText("无需设备");
  await row.getByRole("button", { name: "登记声纹", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "登记现场测试成员的声纹" }),
  ).toBeVisible();
  if (process.env.VOICE_SMOKE_FILE) {
    await page
      .locator('input[type="file"]')
      .setInputFiles(process.env.VOICE_SMOKE_FILE);
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "提取并保存声纹" }).click();
    await expect(row).toContainText("声纹已登记 · 本场启用", {
      timeout: 30000,
    });
    await row.getByRole("button", { name: "撤回使用" }).click();
    await expect(row).toContainText("已撤回");
    await row.getByRole("button", { name: "删除声纹", exact: true }).click();
    await expect(row).toContainText("尚未登记声纹");
  } else {
    await page.getByRole("button", { name: "关闭", exact: true }).click();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    row.getByRole("button", { name: "登记声纹", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "删除会议", exact: true }).click();
  await page.getByRole("button", { name: "确认删除会议" }).click();
});
