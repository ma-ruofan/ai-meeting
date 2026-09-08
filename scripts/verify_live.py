"""Exercise a running server with a provided WAV/AIFF, not the computer microphone."""

import argparse
import asyncio
from pathlib import Path

import httpx
import numpy as np
import websockets
from meeting_app.speech import decode_audio


async def verify(audio_path, port):
    import json

    base = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(timeout=60) as client:
        session = (
            await client.post(
                base + "/api/meetings",
                json={"title": "语音链路自动验证", "name": "测试者"},
            )
        ).json()
        mid = session["meeting_id"]
        headers = {"Authorization": "Bearer " + session["token"]}
        path = base + "/api/meetings/" + mid
        try:
            await client.post(
                path + "/utterances",
                headers=headers,
                json={
                    "text": "我们决定周五完成测试。",
                    "speaker": "测试者",
                    "start": 0,
                    "end": 3,
                    "request_key": "test-evidence",
                },
            )
            signal = decode_audio(Path(audio_path).read_bytes())
            pcm = (
                (np.concatenate([signal, np.zeros(3 * 16000)]) * 32767)
                .astype("<i2")
                .tobytes()
            )
            async with websockets.connect(
                f"ws://127.0.0.1:{port}/ws/audio/{mid}"
            ) as ws:
                await ws.send(json.dumps({"token": session["token"]}))
                ready = json.loads(await ws.recv())
                assert ready.get("type") == "ready", ready
                assert ready["kws"], "Wake detector must be real for this test"
                for i in range(0, len(pcm), 3200):
                    await ws.send(pcm[i : i + 3200])
                    await asyncio.sleep(0.1)
                for _ in range(100):
                    state = (await client.get(path, headers=headers)).json()
                    if state["answers"]:
                        break
                    await asyncio.sleep(0.1)
                assert state["answers"], state["runtime"]
                answer = state["answers"][-1]
                assert answer["spoken"] is True
                assert state["runtime"]["voice_state"] == "speaking", state["runtime"]
                previous = len(state["utterances"])
                # Non-silent playback audio must never re-enter human facts or trigger another question.
                for i in range(0, len(pcm), 3200):
                    await ws.send(pcm[i : i + 3200])
                await asyncio.sleep(0.5)
                after = (await client.get(path, headers=headers)).json()
                assert (
                    len(after["answers"]) == 1 and len(after["utterances"]) == previous
                )
                await ws.send(json.dumps({"type": "playback_done"}))
                await ws.send(json.dumps({"type": "stop"}))
                try:
                    while True:
                        await ws.recv()
                except websockets.ConnectionClosedOK:
                    pass
            print(
                {
                    "wake_detected": True,
                    "question": answer["question"],
                    "mode": answer["mode"],
                    "voice_answer_saved": True,
                    "playback_excluded_from_transcript": True,
                }
            )
        finally:
            result = await client.delete(path, headers=headers)
            assert result.status_code == 200, result.text


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    asyncio.run(verify(args.audio, args.port))
