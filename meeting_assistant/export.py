import html
import re

from .models import timestamp


def escape(text):
    text = html.escape(str(text)).replace("\n", " ")
    # Meeting content is plain text, not executable Markdown or remote images.
    return re.sub(r"([\\`*_[\]{}|])", r"\\\1", text)


def markdown_minutes(meeting, snapshot, analysis=None):
    lines = [
        f"# {escape(meeting['title'])}",
        "",
        f"转录版本：{snapshot['revision']} · 时长：{timestamp(meeting['duration'])}",
        "",
    ]
    if snapshot["source_kind"] == "sample":
        lines += ["> 开发用虚构文字样例，未经过真实音频转录。", ""]
    if analysis:
        meta = analysis["metadata"]
        lines += [
            f"模型：{escape(meta['provider'])} / {escape(meta['model'])}",
            f"生成时间：{escape(analysis['created_at'])}",
            "",
        ]
        if snapshot["revision"] != meeting["transcript_revision"]:
            lines += ["> 本纪要基于旧转录，下面的证据及全文对应当时的快照。", ""]
        result = analysis["result"]
        lines += ["## 摘要", "", escape(result["summary"]), "", "## 决定", ""]

        def refs(ids):
            return " ".join(f"[{ident}](#{ident.lower()})" for ident in ids)

        lines += [
            f"- {escape(item['content'])} {refs(item['evidence_ids'])}"
            for item in result["decisions"]
        ] or ["暂无明确决定。"]
        lines += ["", "## 行动项", "", "| 任务 | 负责人 | 截止时间 | 证据 |", "|---|---|---|---|"]
        for item in result["action_items"]:
            lines.append(
                f"| {escape(item['task'])} | {escape(item['owner'] or '未明确')} | "
                f"{escape(item['deadline_text'] or '未明确')} | {refs(item['evidence_ids'])} |"
            )
    lines += ["", "## 完整转录", ""]
    for segment in snapshot["segments"]:
        lines += [
            f'<a id="{segment["segment_id"].lower()}"></a>',
            f"**{segment['segment_id']} · {timestamp(segment['start'])}–{timestamp(segment['end'])}**",
            "",
            escape(segment["text"]),
            "",
        ]
    lines += ["---", "AI 结果需人工复核；引用存在不等于内容推断一定正确。", ""]
    return "\n".join(lines)
