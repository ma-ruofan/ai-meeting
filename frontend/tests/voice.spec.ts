import { chromium, test, expect } from "@playwright/test";

test("browser PCM microphone reaches real ASR and voice question flow", async () => {
  test.skip(
    !process.env.VOICE_SMOKE_FILE,
    "Requires an authorized local WAV and installed speech models",
  );
  test.setTimeout(60000);
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROME_PATH
      ? { executablePath: process.env.CHROME_PATH }
      : {}),
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
      `--use-file-for-fake-audio-capture=${process.env.VOICE_SMOKE_FILE}`,
    ],
  });
  const context = await browser.newContext({ permissions: ["microphone"] });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await page.goto("http://127.0.0.1:8765");
    await page.getByRole("button", { name: "打开演示会议" }).click();
    await expect(page.locator(".utterance")).toHaveCount(5);
    await page.getByRole("button", { name: "开始录音", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "结束录音", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "语音问答", exact: true }).click();
    await expect(page.locator(".shared-answer").first()).toBeVisible({
      timeout: 30000,
    });
    await expect(page.locator(".shared-answer").first()).toContainText(
      "模拟回答",
    );
    await page.getByRole("button", { name: "结束录音", exact: true }).click();
    await expect
      .poll(async () =>
        page.evaluate(async () => {
          const s = JSON.parse(localStorage.getItem("xiaok.sessions.v2")!)[0];
          const response = await fetch("/api/meetings/" + s.meeting_id, {
            headers: { Authorization: "Bearer " + s.token },
          });
          return (await response.json()).runtime.recording;
        }),
      )
      .toBe(false);
    await page
      .getByRole("textbox", { name: "向小K提问" })
      .fill("刚才的小K语音问答说了什么？");
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.locator(".private-answer")).toContainText("小K语音问答");
    const contextCheck = await page.evaluate(async () => {
      const s = JSON.parse(localStorage.getItem("xiaok.sessions.v2")!)[0];
      const headers = { Authorization: "Bearer " + s.token };
      const base = "/api/meetings/" + s.meeting_id;
      const shared = await (await fetch(base, { headers })).json();
      const own = await (
        await fetch(base + "/private-chat", { headers })
      ).json();
      return own.answers[0].citations.some((c: { id: string }) =>
        shared.answers.some((a: { id: string }) => a.id === c.id),
      );
    });
    expect(contextCheck).toBe(true);
    const exported = await page.evaluate(async () => {
      const s = JSON.parse(localStorage.getItem("xiaok.sessions.v2")!)[0];
      return (
        await fetch("/api/meetings/" + s.meeting_id + "/export", {
          headers: { Authorization: "Bearer " + s.token },
        })
      ).text();
    });
    expect(exported).toContain("小K语音问答（共享记录");
    expect(exported).not.toContain("刚才的小K语音问答说了什么？");
    await page.getByRole("button", { name: "会后纪要", exact: true }).click();
    await page
      .getByRole("button", { name: "结束本次会议", exact: true })
      .click();
    await expect(page.locator(".private-answer")).toHaveCount(0);
    await expect(page.locator(".shared-answer").first()).toBeVisible();
    await page.getByRole("button", { name: "删除会议", exact: true }).click();
    await page.getByRole("button", { name: "确认删除会议" }).click();
    await expect(page.getByRole("heading", { name: /专注讨论/ })).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await browser.close();
  }
});
