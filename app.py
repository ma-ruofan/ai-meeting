"""Streamlit entry point. Run with: uv run streamlit run app.py"""

import html
import shutil
from importlib.util import find_spec

import streamlit as st

from meeting_assistant.agent import ask_meeting
from meeting_assistant.audio import import_audio, transcribe
from meeting_assistant.config import ROOT, Settings
from meeting_assistant.export import markdown_minutes
from meeting_assistant.live import STATUS, LiveManager
from meeting_assistant.llm import ChatClient
from meeting_assistant.models import AppError, timestamp
from meeting_assistant.services import generate_minutes, import_sample
from meeting_assistant.storage import Repository

st.set_page_config(page_title="Meetnote · AI 会议助理", page_icon="◉", layout="wide")
st.markdown(
    """<style>
.block-container {max-width:1220px;padding-top:2.6rem;padding-bottom:4rem;}
h1 {letter-spacing:-.045em!important;font-weight:750!important;}
h2,h3 {letter-spacing:-.025em!important;}
[data-testid="stSidebar"] {background:#edf2ef;border-right:1px solid #dce6e0;}
div[data-testid="stMetric"] {background:#fff;padding:16px 20px;border-radius:14px;border:1px solid #e0e8e3;}
.eyebrow {font-size:12px;letter-spacing:.16em;font-weight:700;color:#18786a;margin-bottom:12px;}
.subtle {color:#768782;font-size:14px;line-height:1.8;}
.hero-card {padding:28px;background:#e4eee8;border-radius:18px;color:#294b40;line-height:2;}
.evidence {padding:18px 22px;border-left:3px solid #18786a;background:#edf4ef;border-radius:0 12px 12px 0;}
button {border-radius:9px!important;}
</style>""",
    unsafe_allow_html=True,
)


def error_message(exc):
    st.error(str(exc) if isinstance(exc, AppError) else "操作未完成，请检查文件或配置后重试。")


try:
    settings = Settings.load()
    repo = Repository(settings.data_dir)
except Exception:
    st.error("配置或数据目录不可用，请检查 .env 中的数字、目录权限及磁盘空间。")
    st.stop()

if "pending_meeting" in st.session_state:
    st.session_state["meeting_picker"] = st.session_state.pop("pending_meeting")
    st.session_state.pop("evidence", None)
    st.session_state.pop("page", None)
if flash := st.session_state.pop("flash", None):
    st.success(flash)


def go_meeting(ident, message=None):
    st.session_state["pending_meeting"] = ident
    if message:
        st.session_state["flash"] = message
    st.rerun()


@st.cache_resource
def live_manager(data_dir):
    return LiveManager(Repository(data_dir))


live = live_manager(settings.data_dir)


def public_dialogue(snapshot):
    st.subheader("小K · 公开问答记录")
    st.caption("参会者身份未区分；小K的回答属于 AI 发言，不能自动视为会议决定。")
    for item in snapshot.get("public_dialogue", []):
        with st.container(border=True):
            st.caption(f"{timestamp(item['question_time'])} · 参会者提问")
            st.text(item["question"])
            st.caption("小K · " + STATUS.get(item["status"], item["status"]))
            if item["answer"]:
                st.text(item["answer"])
            if item["answer_start"] is not None:
                st.caption("播报开始：" + timestamp(item["answer_start"]))
            if item["evidence_ids"]:
                st.caption("回答依据：" + "、".join(item["evidence_ids"]))
                with st.expander("查看回答生成时的原文"):
                    for segment in item.get("evidence_snapshot", []):
                        st.caption(segment["segment_id"] + " · " + timestamp(segment["start"]))
                        st.text(segment["text"])
            if item["error"]:
                st.warning(item["error"])
    if not snapshot.get("public_dialogue"):
        st.caption("尚无公开问答。")


@st.fragment(run_every=2)
def live_panel(ident):
    st.subheader("小K · 现场会议")
    st.info(f"{live.state} · 已收音 {timestamp(live.seconds)}")
    st.caption(
        "最长10分钟。查询和播报期间暂停转录，期间声音仍保存在录音中；请等小K说完再发言。关闭网页不会停止收音，请点击结束。"
    )
    if live.active:
        if st.button("结束收音并保存", type="primary", disabled=live.stop.is_set()):
            live.request_stop()
            st.rerun(scope="fragment")
    else:
        if live.error:
            st.error(live.error)
        if st.button("进入纪要工作区", type="primary"):
            st.rerun()
    snapshot = repo.snapshot(ident)
    public_dialogue(snapshot)
    st.subheader("正在记录的参会者发言")
    for segment in snapshot["segments"][-12:]:
        st.caption(f"{segment['segment_id']} · {timestamp(segment['start'])}")
        st.text(segment["text"])


meetings = repo.list_meetings()
lookup = {m["id"]: m for m in meetings}
with st.sidebar:
    st.markdown("## ◉ Meetnote")
    st.caption("把讨论整理成可以回查的成果")
    st.divider()
    selected = st.selectbox(
        "工作空间",
        ["", *lookup],
        format_func=lambda x: "＋ 新建会议" if not x else lookup[x]["title"],
        key="meeting_picker",
    )
    st.caption(f"已保存 {len(meetings)} 场会议 · 数据保存在本机")
    st.divider()
    st.markdown("**模型连接**")
    st.caption("云端 API · 开发模式" if settings.provider == "cloud" else "llama.cpp · 本地模式")
    if issue := settings.llm_error():
        st.warning("尚未配置模型")
        st.caption(issue)
    else:
        st.caption(settings.model)
        st.caption("配置已填写，实际连通性以调用结果为准。")
    with st.expander("配置与运行说明"):
        st.write("将 .env.example 复制为 .env，在本机填写接口地址、模型和密钥，保存后刷新页面。")
        st.code("uv run streamlit run app.py", language="bash")
        st.caption(
            "语音依赖：" + ("已安装" if find_spec("faster_whisper") else "未安装，可先体验文字样例")
        )
        st.caption("FFmpeg：" + ("可用" if shutil.which(settings.ffmpeg) else "未找到"))
        st.caption("ffprobe：" + ("可用" if shutil.which(settings.ffprobe) else "未找到"))
        st.caption("CPU / CUDA 设置、Windows 部署与限制详见 README。")
    st.divider()
    st.caption("v0.1 · 单用户开发版\n\n引用可回查，结论仍需复核。")


def show_evidence(snapshot, ident, key):
    by_id = {s["segment_id"]: s for s in snapshot["segments"]}
    if ident not in by_id:
        st.caption("引用不属于该快照")
        return
    segment = by_id[ident]
    if st.button(f"↗ {ident} · {timestamp(segment['start'])}", key=key):
        st.session_state["evidence"] = {
            "meeting_id": snapshot["meeting_id"],
            "revision": snapshot["revision"],
            "segment": segment,
            "audio_path": snapshot["audio_path"],
        }
        st.rerun()


def model_permission(key):
    if settings.provider == "cloud":
        return st.checkbox("本次允许将会议文字发送到已配置的云端 API", key=key)
    st.caption("本次由本机 llama.cpp 处理，不自动回退到云端。")
    return True


if live.active:
    if selected != live.meeting_id:
        st.warning("现场会议正在收音。请先结束收音，再操作其他会议。")
    live_panel(live.meeting_id)
    st.stop()


if not selected:
    st.markdown(
        '<div class="eyebrow">MEETING WORKSPACE / 会议工作空间</div>', unsafe_allow_html=True
    )
    left, right = st.columns([1.5, 1], gap="large")
    with left:
        st.title("让每次讨论，都有清晰的下一步。")
        st.write("从会议录音到纪要与待办，让每条结论都能回到原文。")
        st.caption("上传音频开始处理，或先用虚构文字样例体验工作空间。")
    with right:
        st.markdown(
            '<div class="hero-card"><div class="eyebrow">从讨论到行动</div>'
            "01　导入会议，保留原始记录<br>02　整理纪要，定位原文证据<br>"
            "03　带着问题，查询明确依据</div>",
            unsafe_allow_html=True,
        )
    st.write("")
    upload_col, sample_col = st.columns([1.5, 1], gap="large")
    with upload_col, st.container(border=True):
        st.subheader("新建一场会议")
        with st.form("new_meeting"):
            title = st.text_input(
                "会议标题", placeholder="例如：产品周会 · 首版范围评审", max_chars=150
            )
            uploaded = st.file_uploader("会议音频", type=["wav", "mp3"])
            st.caption("WAV / MP3 · 最长 10 分钟 · 最大 100 MB")
            submitted = st.form_submit_button("导入会议 →", type="primary")
        if submitted:
            if not uploaded:
                st.warning("请先选择一个音频文件。")
            else:
                try:
                    with st.spinner("检查并准备音频…"):
                        ident = import_audio(
                            repo, settings, title, uploaded.name, uploaded.getvalue()
                        )
                    go_meeting(ident, "音频已导入，可以开始转录。")
                except Exception as exc:
                    error_message(exc)
    with sample_col, st.container(border=True):
        st.subheader("先体验一份样例")
        st.write("产品评审 · 会议助理首版")
        st.caption(
            "8 段人工编写的虚构讨论，包含明确待办与尚未确认的预算。无需 API 即可查看、编辑和导出文字。"
        )
        st.info("这是文字开发样例，没有配套录音，也不是模型生成结果。")
        if st.button("打开文字样例", width="stretch"):
            try:
                ident = import_sample(repo, ROOT / "samples" / "product-review.json")
                go_meeting(ident, "已载入虚构文字样例；纪要与问答仍需连接真实模型。")
            except Exception as exc:
                error_message(exc)
    with st.container(border=True):
        st.subheader("小K · 开始现场会议")
        st.write("同一会议室，通过运行程序这台电脑的麦克风收音、扬声器回答。")
        live_title = st.text_input("现场会议标题", value="小K现场会议", max_chars=150)
        st.caption("使用系统默认输入和输出设备；请先在系统声音设置中选择会议麦克风和扬声器。")
        st.caption("说“小K小K”后接问题，以短暂停顿结束。唤醒依赖中文转录，有延迟，也可能漏识别。")
        consent = st.checkbox("参会者已知悉录音，并同意将小K问答写入公开纪要")
        cloud_allowed = model_permission("live-cloud-permission")
        if settings.llm_error():
            st.info("模型尚未配置：可以测试收音与唤醒，问答将显示配置错误。")
        voice_ready = bool(find_spec("sounddevice") and find_spec("faster_whisper"))
        if not voice_ready:
            st.code("uv sync --extra voice --group dev", language="bash")
        if st.button(
            "开始收音 · 唤醒小K",
            type="primary",
            disabled=not consent or not cloud_allowed or not voice_ready,
        ):
            try:
                ident = live.start(settings, live_title)
                go_meeting(ident)
            except Exception as exc:
                error_message(exc)
    st.stop()

meeting = repo.meeting(selected)
segments = repo.segments(selected)
analysis = repo.latest_analysis(selected)
st.markdown('<div class="eyebrow">MEETING / 会议工作区</div>', unsafe_allow_html=True)
st.title(meeting["title"])
st.caption("已保存在本机 · " + meeting["created_at"].replace("T", " ")[:16] + " UTC")
if meeting["source_kind"] == "sample":
    st.info("文字开发样例：内容和时间戳为人工编写，没有配套音频。")
if meeting["source_kind"] == "live":
    session = repo.live_session(selected)
    st.caption("现场会议 · " + (session["status"] if session else "已保存"))
    if session and session["error"]:
        st.warning(session["error"])
    public_dialogue(repo.snapshot(selected))
    st.download_button(
        "下载完整公开记录（含小K问答）",
        markdown_minutes(meeting, repo.snapshot(selected)),
        file_name="public-meeting-record.md",
        mime="text/markdown",
    )

a, b, c, d = st.columns(4)
a.metric(
    "样例时间轴" if meeting["source_kind"] == "sample" else "会议时长",
    timestamp(meeting["duration"]),
)
b.metric("转录片段", len(segments))
c.metric("转录版本", f"v{meeting['transcript_revision']}")
d.metric(
    "纪要状态",
    "待生成"
    if not analysis
    else "待更新"
    if analysis["revision"] != meeting["transcript_revision"]
    else "已生成",
)
st.write("")

evidence = st.session_state.get("evidence")
if evidence and evidence["meeting_id"] == selected:
    with st.container(border=True):
        seg = evidence["segment"]
        st.markdown(
            f"**原文证据 · {seg['segment_id']} · v{evidence['revision']} · "
            f"{timestamp(seg['start'])}–{timestamp(seg['end'])}**"
        )
        st.text(seg["text"])
        if evidence["revision"] != meeting["transcript_revision"]:
            st.caption("这是生成时的历史原文；当前转录已有更新。")
        audio = repo.path(evidence["audio_path"])
        if audio and audio.exists():
            st.audio(
                str(audio),
                start_time=int(seg["start"]),
                end_time=max(int(seg["end"]), int(seg["start"]) + 1),
                autoplay=True,
            )
        else:
            st.caption("该文字样例没有配套音频。")

page = st.segmented_control(
    "工作区视图",
    ["转录与校正", "会议纪要", "问答 Agent"],
    default="转录与校正",
    key="page",
    label_visibility="collapsed",
)

if page == "转录与校正":
    left, right = st.columns([1, 2], gap="large")
    with left:
        st.subheader("会议记录")
        audio = repo.path(meeting["audio_path"])
        if audio and audio.exists():
            st.audio(str(audio))
        if audio and audio.exists() and meeting["source_kind"] != "live":
            st.caption(
                f"转录配置：{settings.asr_model} / {settings.asr_device} / {settings.asr_compute_type}"
            )
            st.caption("首次转录可能下载语音模型。完成后子进程退出释放资源。")
            allowed = True
            if segments:
                allowed = st.checkbox(
                    "重新转录将替换当前文字及校正内容；历史纪要快照保留", key=f"replace-{selected}"
                )
            if st.button(
                "重新转录" if segments else "开始转录", type="primary", disabled=not allowed
            ):
                try:
                    with st.status("转录处理中…", expanded=True) as status:
                        transcribe(repo, settings, selected, lambda text: st.write(text))
                        status.update(label="转录完成", state="complete")
                    go_meeting(selected, "转录已保存。")
                except Exception as exc:
                    error_message(exc)
        elif meeting["source_kind"] == "live":
            st.caption("现场转录已保存；小K查询与播报时暂停转录，完整声音保留在录音中。")
        else:
            st.caption("当前会议只包含文字，可直接体验校正、纪要生成与问答。")
        with st.expander("最近转录记录"):
            runs = repo.stages(selected)
            if not runs:
                st.caption("暂无真实转录记录")
            for run in runs:
                st.write(f"{run['started_at']} · {run['status']}")
                if run["error"]:
                    st.caption(run["error"])
        if segments:
            current_snapshot = repo.snapshot(selected)
            st.download_button(
                "导出当前转录",
                markdown_minutes(meeting, current_snapshot),
                file_name="transcript.md",
                mime="text/markdown",
            )
    with right:
        st.subheader("转录与校正")
        if not segments:
            st.info("点击“开始转录”，完成后会显示可编辑的文字片段。")
        else:
            st.caption("修改文字后点击保存。已生成纪要会标记为过期，历史证据保持可回查。")
            with st.form(f"edit-{selected}-{meeting['transcript_revision']}"):
                edited = st.data_editor(
                    segments,
                    hide_index=True,
                    width="stretch",
                    height=420,
                    disabled=["segment_id", "start", "end"],
                    column_config={
                        "segment_id": "片段",
                        "start": "开始（秒）",
                        "end": "结束（秒）",
                        "text": st.column_config.TextColumn(
                            "转录文字", width="large", required=True
                        ),
                    },
                    key=f"editor-{selected}-{meeting['transcript_revision']}",
                )
                save = st.form_submit_button("保存校正", type="primary")
            if save:
                try:
                    changed = repo.edit_transcript(
                        selected,
                        {s["segment_id"]: s["text"] for s in edited},
                        meeting["transcript_revision"],
                    )
                    go_meeting(
                        selected, "校正已保存，历史纪要仍可查看。" if changed else "文字没有变化。"
                    )
                except Exception as exc:
                    error_message(exc)
            options = {s["segment_id"]: s for s in segments}
            segment_id = st.selectbox(
                "定位片段",
                list(options),
                format_func=lambda x: (
                    f"{x} · {timestamp(options[x]['start'])} · {options[x]['text'][:35]}"
                ),
            )
            show_evidence(repo.snapshot(selected), segment_id, "transcript-evidence")

elif page == "会议纪要":
    header, action = st.columns([2, 1])
    with header:
        st.subheader("从讨论到行动")
        st.caption("模型提取摘要、决定与待办。每条决定和待办都附原文引用。")
    with action:
        allowed = model_permission(f"minutes-permission-{selected}")
        if st.button(
            "生成新版本纪要",
            type="primary",
            disabled=bool(settings.llm_error()) or not segments or not allowed,
            width="stretch",
        ):
            try:
                with st.spinner("生成纪要并校验引用…"):
                    generate_minutes(repo, ChatClient(settings), selected)
                st.session_state["flash"] = "纪要已保存。请复核内容与负责人。"
                st.rerun()
            except Exception as exc:
                error_message(exc)
    runs = repo.analyses(selected)
    successes = [run for run in runs if run["status"] == "succeeded"]
    if not successes:
        st.info("还没有纪要。准备好模型配置后，点击上方按钮生成。")
    else:
        by_id = {run["id"]: run for run in successes}
        chosen = st.selectbox(
            "纪要版本",
            list(by_id),
            format_func=lambda x: (
                f"{by_id[x]['created_at']} · 转录 v{by_id[x]['revision']} · {by_id[x]['metadata']['model']}"
            ),
        )
        run = by_id[chosen]
        if run["revision"] != meeting["transcript_revision"]:
            st.warning("转录已更新。这份历史纪要及引用基于旧快照，建议生成新版本。")
        st.caption(
            f"{run['metadata']['provider']} / {run['metadata']['model']} · "
            f"{run['elapsed']:.1f} 秒 · 请求 {run['attempts']} 次"
        )
        st.markdown("#### 会议摘要")
        st.text(run["result"]["summary"])
        st.markdown("#### 关键决定")
        if not run["result"]["decisions"]:
            st.caption("没有提取到明确决定。")
        for i, item in enumerate(run["result"]["decisions"]):
            with st.container(border=True):
                st.text(item["content"])
                columns = st.columns(len(item["evidence_ids"]))
                for j, ident in enumerate(item["evidence_ids"]):
                    with columns[j]:
                        show_evidence(run["snapshot"], ident, f"decision-{chosen}-{i}-{j}")
        st.markdown("#### 行动项")
        if not run["result"]["action_items"]:
            st.caption("没有提取到明确行动项。")
        for i, item in enumerate(run["result"]["action_items"]):
            with st.container(border=True):
                st.text(item["task"])
                st.caption(
                    f"负责人：{item['owner'] or '未明确'}　·　时间：{item['deadline_text'] or '未明确'}"
                )
                columns = st.columns(len(item["evidence_ids"]))
                for j, ident in enumerate(item["evidence_ids"]):
                    with columns[j]:
                        show_evidence(run["snapshot"], ident, f"action-{chosen}-{i}-{j}")
        st.download_button(
            "下载 Markdown 纪要",
            markdown_minutes(meeting, run["snapshot"], run),
            file_name="meeting-minutes.md",
            mime="text/markdown",
            type="primary",
        )
    failed = [run for run in runs if run["status"] != "succeeded"]
    if failed:
        with st.expander("未完成的生成记录"):
            for run in failed[:5]:
                st.write(f"{run['created_at']} · {run['status']}")
                st.caption(run["error"] or "正在处理")

elif page == "问答 Agent":
    st.subheader("带着问题，回到原文")
    st.caption("助手选择会议查询工具，程序执行后返回证据。最多 3 轮模型响应 / 4 次工具调用。")
    st.caption("可以试试：哪些待办还没有负责人？　预算是否已经确认？　首版支持哪些功能？")
    allowed = model_permission(f"agent-permission-{selected}")
    with st.form(f"question-{selected}", clear_on_submit=False):
        question = st.text_input(
            "关于这场会议，你想了解什么？",
            max_chars=2000,
            placeholder="例如：有哪些还没有明确负责人的待办？",
        )
        submitted = st.form_submit_button(
            "查询会议 →",
            type="primary",
            disabled=bool(settings.llm_error()) or not segments or not allowed,
        )
    if submitted:
        try:
            with st.spinner("查询会议证据…"):
                ask_meeting(repo, ChatClient(settings), selected, question)
            st.rerun()
        except Exception as exc:
            error_message(exc)
    runs = repo.agent_runs(selected)
    if not runs:
        st.info("查询后，这里会显示回答、引用，以及实际工具调用过程。")
    for run in runs[:8]:
        with st.container(border=True):
            st.markdown("**你的问题**")
            st.text(run["question"])
            st.caption(
                f"{run['metadata']['provider']} / {run['metadata']['model']} · "
                f"{run['metadata']['protocol']} · {run['status']} · v{run['revision']}"
            )
            if run["revision"] != meeting["transcript_revision"]:
                st.warning("这条回答基于旧转录，引用显示当时快照。")
            if run["result"]:
                st.text(run["result"]["answer"])
                for i, ident in enumerate(run["result"]["evidence_ids"]):
                    show_evidence(run["snapshot"], ident, f"agent-evidence-{run['id']}-{i}")
            elif run["error"]:
                st.error(run["error"])
            with st.expander(f"查看工具调用 · {len(run['trace'])} 次"):
                if not run["trace"]:
                    st.caption("本次没有执行工具。")
                for entry in run["trace"]:
                    st.markdown(f"**{html.escape(entry['tool'])}** · {entry['elapsed_ms']} ms")
                    st.json({"参数": entry["arguments"], "结果": entry["result"]}, expanded=False)

st.divider()
st.caption("AI 结果需人工复核。引用校验确认来源存在，不能保证每项语义判断正确。")
