import json
from time import perf_counter

from pydantic import ValidationError

from .models import AppError, Minutes, canonical, validate_evidence
from .prompts import MINUTES_SYSTEM, MINUTES_VERSION


def generate_minutes(repo, client, meeting_id):
    with repo.processing():
        snapshot = repo.snapshot(meeting_id)
        metadata = {**client.settings.public_model(), "prompt_version": MINUTES_VERSION}
        ident = repo.begin_analysis(snapshot, metadata)
        start = perf_counter()
        attempts = 0
        messages = [
            {"role": "system", "content": MINUTES_SYSTEM},
            {"role": "user", "content": canonical({"segments": snapshot["segments"]})},
        ]
        try:
            for attempt in range(2):
                attempts += 1
                reply = client.chat(messages, schema=Minutes.model_json_schema())
                try:
                    from .llm import parse_json

                    minutes = Minutes.model_validate(parse_json(reply.content))
                    validate_evidence(minutes, snapshot["segments"])
                except (ValueError, ValidationError, AppError):
                    if attempt:
                        raise AppError(
                            "模型结果经一次纠错仍未通过结构或引用校验，未保存为成功结果。"
                        )
                    messages.append(
                        {
                            "role": "user",
                            "content": "上次结果未通过校验。重新依据原始转录生成，检查字段、null 与引用 ID；仅输出 JSON。",
                        }
                    )
                    continue
                repo.finish_analysis(
                    ident, minutes.model_dump(), attempts=attempts, elapsed=perf_counter() - start
                )
                return ident
        except Exception as exc:
            message = str(exc) if isinstance(exc, AppError) else "纪要处理失败，请检查配置后重试。"
            repo.finish_analysis(
                ident, error=message, attempts=attempts, elapsed=perf_counter() - start
            )
            raise AppError(message) from exc


def import_sample(repo, sample_path):
    data = json.loads(sample_path.read_text(encoding="utf-8"))
    ident = repo.create_meeting(data["title"], data["duration"], "sample")
    repo.replace_transcript(ident, data["segments"])
    return ident
