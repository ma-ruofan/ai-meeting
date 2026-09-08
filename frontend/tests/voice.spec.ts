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
    await expect(page.locator(".answer-card").first()).toBeVisible({
      timeout: 30000,
    });
    await expect(page.locator(".answer-card").first()).toContainText(
      "模拟回答",
    );
    await page.getByRole("button", { name: "结束录音", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "开始录音", exact: true }),
    ).toBeVisible();
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
    await page.getByRole("button", { name: "删除会议", exact: true }).click();
    await page.getByRole("button", { name: "确认删除会议" }).click();
    await expect(page.getByRole("heading", { name: /专注讨论/ })).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await browser.close();
  }
});
