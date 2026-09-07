"""Run real-provider development evaluations. Reports never fabricate semantic scores."""

import argparse
import json
import platform
from collections import Counter
from datetime import datetime, timezone

from meeting_assistant.agent import ask_meeting
from meeting_assistant.config import ROOT, Settings
from meeting_assistant.llm import ChatClient
from meeting_assistant.models import AppError
from meeting_assistant.services import generate_minutes, import_sample
from meeting_assistant.storage import Repository


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate development fixtures without model requests",
    )
    parser.add_argument(
        "--allow-cloud",
        action="store_true",
        help="Allow sending the fictional fixtures to configured API",
    )
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    data = json.loads((ROOT / "eval/questions.json").read_text(encoding="utf-8"))
    sample = json.loads((ROOT / "samples/product-review.json").read_text(encoding="utf-8"))
    ids = {s["segment_id"] for s in sample["segments"]}
    for case in data["questions"]:
        if not set(case["expected_evidence"]).issubset(ids):
            raise SystemExit("Invalid expected evidence")
    if not 1 <= args.limit <= len(data["questions"]):
        raise SystemExit("--limit 必须在 1～20 之间。")
    if args.dry_run:
        print(f"Validated {len(data['questions'])} development questions; no model called.")
        return
    cfg = Settings.load()
    if error := cfg.llm_error():
        raise SystemExit(error)
    if cfg.provider == "cloud" and not args.allow_cloud:
        raise SystemExit("云端评测需显式添加 --allow-cloud，将产生模型请求并可能计费。")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = cfg.data_dir / "eval" / stamp
    repo = Repository(folder)
    meeting = import_sample(repo, ROOT / "samples/product-review.json")
    client = ChatClient(cfg)
    try:
        generate_minutes(repo, client, meeting)
    except AppError as exc:
        print(f"Minutes failed: {exc}")
    results = []
    for i, case in enumerate(data["questions"][: args.limit]):
        try:
            ask_meeting(repo, client, meeting, case["question"])
        except AppError:
            pass
        run = repo.agent_runs(meeting)[0]
        cited = set((run["result"] or {}).get("evidence_ids", []))
        expected = set(case["expected_evidence"])
        results.append(
            {
                **case,
                "run": run,
                "expected_evidence_overlap": bool(cited & expected) if expected else None,
                "human_supported": None,
                "human_task_success": None,
                "review_notes": "",
            }
        )
        print(f"{i + 1}/{args.limit}: {run['status']}")
    report = {
        "split": data["split"],
        "notice": data["notice"],
        "provider": cfg.provider,
        "model": cfg.model,
        "platform": platform.platform(),
        "protocol": cfg.agent_protocol,
        "analysis_runs": repo.analyses(meeting),
        "questions": results,
        "status_counts": dict(Counter(x["run"]["status"] for x in results)),
        "semantic_success_rate": None,
    }
    (folder / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved {folder / 'report.json'}")
    print("succeeded 只代表程序结构与引用校验通过。语义正确性需人工填写，不能当作任务成功率。")


if __name__ == "__main__":
    main()
