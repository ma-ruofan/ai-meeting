import { test, expect } from "@playwright/test";

test("private chat isolation, shared records, end cleanup and export", async ({
  page,
  browser,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /专注讨论/ })).toBeVisible();
  await page.screenshot({ path: "docs/screenshots/home.png", fullPage: true });
  await page.getByRole("button", { name: "打开演示会议" }).click();
  await expect(
    page.getByRole("heading", { name: "产品讨论 · 演示会议" }),
  ).toBeVisible();
  await expect(page.locator(".utterance")).toHaveCount(5);
  await page.getByRole("button", { name: "邀请加入" }).click();
  const code = (await page.locator(".invite-code").textContent())!;
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  const guest = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const guestPage = await guest.newPage();
  await guestPage.goto("/");
  await guestPage.getByRole("button", { name: "使用会议码加入" }).click();
  await guestPage.getByRole("textbox", { name: "会议码" }).fill(code);
  await guestPage.getByRole("textbox", { name: "你的昵称" }).fill("手机参会者");
  await guestPage.getByRole("checkbox").check();
  await guestPage.getByRole("button", { name: "同意并加入" }).click();
  await expect(guestPage.locator(".utterance")).toHaveCount(5);
  await expect(
    guestPage.getByRole("button", { name: "开始录音", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("textbox", { name: "向小K提问" }).fill("谁负责测试？");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.locator(".answer-card")).toHaveCount(1);
  await expect(guestPage.locator(".answer-card")).toHaveCount(0);
  await guestPage
    .getByRole("textbox", { name: "向小K提问" })
    .fill("仅手机成员可见的问题 SECRET-MOBILE");
  await guestPage.getByRole("button", { name: "发送问题" }).click();
  await expect(guestPage.locator(".private-answer")).toHaveCount(1);
  await expect(page.locator(".private-answer")).not.toContainText(
    "SECRET-MOBILE",
  );
  await expect(page.getByRole("button", { name: "复核并采纳" })).toHaveCount(0);
  await expect(page.locator(".answer-card")).toContainText("模拟回答");
  await page.screenshot({
    path: "docs/screenshots/workspace.png",
    fullPage: true,
  });
  expect(
    await guestPage.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await guestPage.screenshot({
    path: "docs/screenshots/mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "会后纪要", exact: true }).click();
  await page.getByRole("button", { name: "生成纪要", exact: true }).click();
  await expect(page.locator(".overview")).toContainText("模拟纪要");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出纪要", exact: true }).click();
  expect((await download).suggestedFilename()).toContain(".md");
  await page.getByRole("button", { name: /会议记录/ }).click();
  await page.locator(".utterance").first().hover();
  await page.getByRole("button", { name: "编辑", exact: true }).first().click();
  await page
    .getByRole("textbox", { name: "发言内容" })
    .fill("更新：下周一确认最终范围。");
  await page.getByRole("button", { name: "保存修正" }).click();
  await expect(guestPage.locator(".utterance").first()).toContainText("下周一");
  await expect(page.locator(".answer-card .stale")).toBeVisible();
  await guestPage.reload();
  // The local history retains access, but the landing page is intentional on reload.
  await guestPage
    .getByRole("button", { name: "产品讨论 · 演示会议", exact: true })
    .click();
  await expect(guestPage.locator(".utterance").first()).toContainText("下周一");
  await expect(guestPage.locator(".private-answer")).toHaveCount(1);
  await page.getByRole("button", { name: "会后纪要", exact: true }).click();
  await page.getByRole("button", { name: "结束本次会议", exact: true }).click();
  await expect(page.locator(".private-answer")).toHaveCount(0);
  await expect(guestPage.locator(".private-answer")).toHaveCount(0);
  await expect(
    guestPage.getByRole("textbox", { name: "向小K提问" }),
  ).toBeDisabled();
  await guestPage.reload();
  await guestPage
    .getByRole("button", { name: "产品讨论 · 演示会议", exact: true })
    .click();
  await expect(
    guestPage.getByText("私聊已关闭", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "删除会议", exact: true }).click();
  await page.getByRole("button", { name: "确认删除会议" }).click();
  await expect(page.getByRole("heading", { name: /专注讨论/ })).toBeVisible();
  await guest.close();
  expect(errors).toEqual([]);
});
