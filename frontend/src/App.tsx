import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  AudioLines,
  Check,
  CheckCircle2,
  ChevronRight,
  Clipboard,
  FileText,
  Fingerprint,
  LayoutDashboard,
  Link2,
  Loader2,
  MessageSquare,
  Mic,
  Plus,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Square,
  Sparkles,
  Trash2,
  Upload,
  Users,
  Volume2,
  X,
} from "lucide-react";
import {
  api,
  loadSessions,
  roomPath,
  saveSessions,
  socketURL,
  timeLabel,
} from "./api";
import type { Answer, Health, Session, Snapshot, Utterance } from "./api";
import { Recorder, waveBlob } from "./audio";

const demoLines = [
  [
    "林然",
    "今天先确认会议助理第一版的范围：录音转写、声纹识别、多设备同步，还有小K语音问答。",
  ],
  ["陈一", "我负责页面和多设备测试，周五前把手机加入会议的流程走通。"],
  ["许诺", "声纹先用三到五个人测试，认不出来就显示未知，不要强行匹配姓名。"],
  [
    "林然",
    "决定先用同一个会议室做验证，Windows和Mac都需要能运行。大模型的选型暂时不定。",
  ],
  [
    "陈一",
    "我们下次复盘重点看转录延迟、未知说话人误认，以及小K会不会被自己的播报唤醒。",
  ],
];
const voiceLabels: Record<string, string> = {
  idle: "等待录音",
  listening: "等待「小K小K」",
  question: "正在听你的问题",
  thinking: "整理语音回答 · 暂停记录",
  speaking: "播报中 · 暂停记录",
};
const uid = () =>
  crypto.randomUUID?.() ??
  `${Date.now()}-${Math.random().toString(16).slice(2)}`;
function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="modal"
      >
        <header>
          <h2>{title}</h2>
          <button className="icon" aria-label="关闭" onClick={onClose}>
            <X size={19} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
function Avatar({ name, index = 0 }: { name: string; index?: number }) {
  return <span className={`avatar tone-${index % 4}`}>{name.slice(0, 1)}</span>;
}
function Empty({
  icon,
  title,
  text,
}: {
  icon: ReactNode;
  title: string;
  text: string;
}) {
  return (
    <div className="empty">
      <span>{icon}</span>
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>(loadSessions);
  const [active, setActive] = useState<Session | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [tab, setTab] = useState("records");
  const [modal, setModal] = useState<
    | null
    | "create"
    | "join"
    | "invite"
    | "manual"
    | "voice"
    | "attendee"
    | "delete"
    | "settings"
  >(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const [connected, setConnected] = useState(false);
  const [question, setQuestion] = useState("");
  const [privateChat, setPrivateChat] = useState<{
    token: string;
    answers: Answer[];
  } | null>(null);
  const [privatePendingToken, setPrivatePendingToken] = useState<string | null>(
    null,
  );
  const privateScope =
    active &&
    snapshot?.meeting.id === active.meeting_id &&
    snapshot.meeting.status !== "ended"
      ? active.token
      : "";
  const currentPrivateScope = useRef(privateScope);
  currentPrivateScope.current = privateScope;
  const privatePending = !!active && privatePendingToken === active.token;
  const privateAnswers =
    privateChat?.token === privateScope ? privateChat.answers : [];
  useEffect(() => {
    if (!privateScope || !active) {
      setQuestion("");
      setPrivateChat(null);
      return;
    }
    let gone = false;
    const refresh = async () => {
      try {
        const data = await api<{ answers: Answer[] }>(
          roomPath(active) + "/private-chat",
          active,
        );
        if (!gone && currentPrivateScope.current === active.token)
          setPrivateChat({ token: active.token, answers: data.answers });
      } catch (e) {
        if (!gone) setError(e instanceof Error ? e.message : "无法读取私聊");
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 4000);
    return () => {
      gone = true;
      clearInterval(timer);
    };
  }, [privateScope, snapshot?.meeting.revision]);
  const [recording, setRecording] = useState(false);
  const [level, setLevel] = useState(0);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<Utterance | null>(null);
  const [confirming, setConfirming] = useState<Answer | null>(null);
  const [voiceTarget, setVoiceTarget] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const [voiceRecording, setVoiceRecording] = useState(false);
  const [voiceBlob, setVoiceBlob] = useState<Blob | null>(null);
  const [voiceSeconds, setVoiceSeconds] = useState(0);
  const recordRef = useRef<Recorder | null>(null),
    voiceRef = useRef<Recorder | null>(null),
    audioWs = useRef<WebSocket | null>(null);
  const spokenIds = useRef(new Set<string>()),
    audioActive = useRef(false),
    leaving = useRef(false);
  const enrollFrames = useRef<ArrayBuffer[]>([]),
    voiceTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const host = active?.role === "host";
  const path = active ? roomPath(active) : "";
  const getHealth = () =>
    api<Health>("/api/health")
      .then(setHealth)
      .catch(() => setError("无法连接服务，请确认后端已经启动"));
  useEffect(() => {
    getHealth();
    // Warm up the local voice list without speaking or contacting a model service.
    window.speechSynthesis?.getVoices();
  }, []);
  useEffect(() => {
    if (notice) {
      const t = setTimeout(() => setNotice(""), 5000);
      return () => clearTimeout(t);
    }
  }, [notice]);
  useEffect(() => {
    if (!active) {
      setSnapshot(null);
      setConnected(false);
      return;
    }
    let gone = false,
      socket: WebSocket | null = null,
      timer: ReturnType<typeof setTimeout>,
      attempt = 0;
    const accept = (data: Snapshot) =>
      setSnapshot((old) =>
        old &&
        old.meeting.id === data.meeting.id &&
        old.meeting.seq > data.meeting.seq
          ? old
          : data,
      );
    api<Snapshot>(roomPath(active), active)
      .then((data) => {
        if (!gone) accept(data);
      })
      .catch((e) => {
        if (!gone) setError(e.message);
      });
    const connect = () => {
      socket = new WebSocket(socketURL(`/ws/meetings/${active.meeting_id}`));
      socket.onopen = () =>
        socket?.send(JSON.stringify({ token: active.token }));
      socket.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "snapshot") {
          attempt = 0;
          setConnected(true);
          accept(msg.data);
        }
        if (msg.type === "deleted") {
          setNotice("会议已删除");
          removeSession(active.meeting_id);
        }
      };
      socket.onclose = () => {
        if (!gone) {
          setConnected(false);
          timer = setTimeout(connect, Math.min(500 * 2 ** attempt++, 8000));
        }
      };
      socket.onerror = () => socket?.close();
    };
    connect();
    const ping = setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) socket.send("sync");
    }, 15000);
    return () => {
      gone = true;
      clearTimeout(timer);
      clearInterval(ping);
      socket?.close();
    };
  }, [active?.meeting_id, active?.token]);

  async function stopRecording() {
    audioActive.current = false;
    await recordRef.current?.stop();
    recordRef.current = null;
    if (audioWs.current?.readyState === WebSocket.OPEN)
      audioWs.current.send(JSON.stringify({ type: "stop" }));
    setRecording(false);
    setLevel(0);
    window.speechSynthesis?.cancel();
  }
  async function stopVoice() {
    await voiceRef.current?.stop();
    voiceRef.current = null;
    if (voiceTimer.current) clearInterval(voiceTimer.current);
    setVoiceRecording(false);
    if (enrollFrames.current.length)
      setVoiceBlob(waveBlob(enrollFrames.current));
  }
  async function selectSession(session: Session | null) {
    leaving.current = true;
    await stopRecording();
    await stopVoice();
    audioWs.current?.close();
    audioWs.current = null;
    setSnapshot(null);
    setActive(session);
    setPrivateChat(null);
    setQuestion("");
    setError("");
    setTab("records");
    setQuery("");
    setVoiceBlob(null);
    leaving.current = false;
  }
  function removeSession(id: string) {
    setSessions((old) => {
      const next = old.filter((s) => s.meeting_id !== id);
      saveSessions(next);
      return next;
    });
    if (active?.meeting_id === id) void selectSession(null);
  }
  function remember(session: Session) {
    setSessions((old) => {
      const next = [
        session,
        ...old.filter((s) => s.meeting_id !== session.meeting_id),
      ];
      saveSessions(next);
      return next;
    });
    void selectSession(session);
  }
  async function perform(fn: () => Promise<unknown>) {
    setPending(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败，请重试");
    } finally {
      setPending(false);
    }
  }
  async function createMeeting(e: FormEvent<HTMLFormElement>, join = false) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    await perform(async () => {
      const body = join
        ? {
            code: String(data.get("code")).trim(),
            name: data.get("name"),
            consent: true,
          }
        : { title: data.get("title"), name: data.get("name") };
      const session = await api<Session>(
        join ? "/api/join" : "/api/meetings",
        null,
        body,
      );
      remember(session);
      setModal(null);
    });
  }
  async function createDemo() {
    await perform(async () => {
      const s = await api<Session>("/api/meetings", null, {
        title: "产品讨论 · 演示会议",
        name: "我",
      });
      for (let i = 0; i < demoLines.length; i++)
        await api(roomPath(s) + "/utterances", s, {
          text: demoLines[i][1],
          speaker: demoLines[i][0],
          start: i * 25,
          end: i * 25 + 20,
          request_key: uid(),
        });
      remember(s);
      setNotice("已创建文字演示会议。内容为测试样例，并非真实转录。");
    });
  }
  async function ask(e?: FormEvent, quick?: string) {
    e?.preventDefault();
    const q = (quick ?? question).trim();
    if (!active || !q || !privateScope || privatePending) return;
    const session = active;
    setPrivatePendingToken(session.token);
    setError("");
    try {
      await api(roomPath(session) + "/private-chat", session, {
        question: q,
        request_key: uid(),
      });
      const data = await api<{ answers: Answer[] }>(
        roomPath(session) + "/private-chat",
        session,
      );
      if (currentPrivateScope.current === session.token) {
        setPrivateChat({ token: session.token, answers: data.answers });
        setQuestion("");
      }
    } catch (e) {
      if (currentPrivateScope.current === session.token)
        setError(e instanceof Error ? e.message : "私聊失败");
    } finally {
      setPrivatePendingToken((old) => (old === session.token ? null : old));
    }
  }

  async function exportMeeting() {
    if (!active) return;
    await perform(async () => {
      const response = await fetch(path + "/export", {
        headers: { Authorization: `Bearer ${active.token}` },
      });
      if (!response.ok) throw new Error("导出失败");
      const blob = await response.blob(),
        url = URL.createObjectURL(blob),
        a = document.createElement("a");
      a.href = url;
      a.download = `${active.title}.md`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  async function uploadAudio(file: File) {
    if (!active) return;
    await perform(async () => {
      const form = new FormData();
      form.append("file", file);
      form.append("request_key", uid());
      await api(path + "/audio", active, form);
      setNotice("音频已提交，处理状态会自动更新");
    });
  }
  async function startRecording() {
    if (!active) return;
    setError("");
    setPending(true);
    try {
      const ws = new WebSocket(socketURL(`/ws/audio/${active.meeting_id}`));
      audioWs.current = ws;
      await new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(() => {
          ws.close();
          reject(new Error("录音服务连接超时"));
        }, 15000);
        ws.onopen = () => ws.send(JSON.stringify({ token: active.token }));
        ws.onmessage = (e) => {
          const m = JSON.parse(e.data);
          if (m.type === "ready") {
            clearTimeout(timeout);
            if (!m.kws) setNotice("唤醒模型未就绪，可先使用“语音提问”按钮");
            resolve();
          } else if (m.type === "error") {
            clearTimeout(timeout);
            reject(new Error(m.message));
          }
        };
        ws.onerror = () => {
          clearTimeout(timeout);
          reject(new Error("无法连接录音服务"));
        };
        ws.onclose = () => {
          clearTimeout(timeout);
          reject(new Error("录音连接已关闭"));
        };
      });
      ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === "error") setError(m.message);
      };
      ws.onclose = () => {
        audioActive.current = false;
        void recordRef.current?.stop();
        setRecording(false);
        setLevel(0);
        if (!leaving.current)
          setNotice("录音连接已结束，已接收的音频仍会完成处理");
      };
      recordRef.current = new Recorder();
      await recordRef.current.start((frame, l) => {
        setLevel(l);
        if (ws.readyState === WebSocket.OPEN) {
          if (ws.bufferedAmount > 32000 * 5) {
            setError("网络发送积压，已停止录音");
            void stopRecording();
            return;
          }
          ws.send(frame);
        }
      });
      audioActive.current = true;
      setRecording(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "麦克风启动失败");
      await stopRecording();
      audioWs.current?.close();
    } finally {
      setPending(false);
    }
  }
  function playbackDone() {
    setTimeout(() => {
      if (audioWs.current?.readyState === WebSocket.OPEN)
        audioWs.current.send(JSON.stringify({ type: "playback_done" }));
    }, 450);
  }
  function stopPlayback() {
    window.speechSynthesis?.cancel();
    playbackDone();
  }
  useEffect(() => {
    const id = snapshot?.runtime.answer_id;
    if (!id || !host || !audioActive.current || spokenIds.current.has(id))
      return;
    const answer = snapshot.answers.find((a) => a.id === id);
    if (!answer) return;
    spokenIds.current.add(id);
    if (!window.speechSynthesis) {
      setNotice("当前浏览器不支持语音合成，回答已保存为文字");
      playbackDone();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(answer.answer);
    utterance.lang = "zh-CN";
    utterance.rate = 1;
    const voice = speechSynthesis
      .getVoices()
      .find((v) => v.lang.startsWith("zh") && v.localService);
    if (!voice) {
      setNotice(
        "主电脑未提供本地中文音色，回答已保存为文字；安装系统中文语音后可播报",
      );
      playbackDone();
      return;
    }
    utterance.voice = voice;
    utterance.onend = playbackDone;
    utterance.onerror = () => {
      setNotice("语音播放失败，回答已保存为文字");
      playbackDone();
    };
    speechSynthesis.cancel();
    speechSynthesis.speak(utterance);
  }, [snapshot?.runtime.answer_id, host]);
  useEffect(
    () => () => {
      void recordRef.current?.stop();
      void voiceRef.current?.stop();
      audioWs.current?.close();
      window.speechSynthesis?.cancel();
      if (voiceTimer.current) clearInterval(voiceTimer.current);
    },
    [],
  );
  async function beginEnroll() {
    setError("");
    setVoiceBlob(null);
    enrollFrames.current = [];
    setVoiceSeconds(0);
    try {
      voiceRef.current = new Recorder();
      await voiceRef.current.start((frame) => enrollFrames.current.push(frame));
      setVoiceRecording(true);
      let sec = 0;
      voiceTimer.current = setInterval(() => {
        setVoiceSeconds(++sec);
        if (sec >= 20) void stopVoice();
      }, 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "录音失败");
    }
  }
  async function submitEnroll(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!active) return;
    const form = new FormData(e.currentTarget);
    if (voiceBlob) form.set("file", voiceBlob, "enrollment.wav");
    if (
      !(form.get("file") instanceof File) ||
      (form.get("file") as File).size === 0
    ) {
      setError("请先录音或选择音频文件");
      return;
    }
    await perform(async () => {
      await api(
        path +
          (voiceTarget
            ? `/members/${voiceTarget.id}/voiceprint`
            : "/voiceprint"),
        active,
        form,
      );
      setModal(null);
      setVoiceBlob(null);
      setNotice("声纹已登记，仅用于本场会议匹配");
    });
  }
  const me = snapshot?.members.find((m) => m.id === active?.member_id);
  const ownVoice = snapshot?.voiceprints.find(
    (v) => v.member_id === active?.member_id,
  );
  const summaries = snapshot?.summaries ?? [],
    latest = summaries.at(-1);
  const records =
    snapshot?.utterances.filter(
      (u) => !query || u.text.includes(query) || u.speaker.includes(query),
    ) ?? [];
  const jump = (id: string) => {
    const isVoice = snapshot?.answers.some((a) => a.id === id);
    const isSummary = snapshot?.summaries.some((a) => a.id === id);
    setTab(isVoice ? "voice-answers" : isSummary ? "summary" : "records");
    setQuery("");
    setTimeout(() => {
      document
        .getElementById((isVoice ? "a-" : isSummary ? "summary-" : "u-") + id)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
      document
        .getElementById((isVoice ? "a-" : isSummary ? "summary-" : "u-") + id)
        ?.animate([{ background: "#dfeccf" }, { background: "transparent" }], {
          duration: 1800,
        });
    }, 100);
  };

  const renderAnswer = (a: Answer, shared: boolean) => (
    <article
      className={`answer-card ${shared ? "shared-answer" : "private-answer"}`}
      id={shared ? "a-" + a.id : undefined}
      key={a.id}
    >
      <div className="question-bubble">{a.question}</div>
      <div className="answer-meta">
        <Sparkles size={14} />
        <strong>{shared ? "小K · 语音问答" : "小K · 私聊"}</strong>
        <span>{a.mode === "mock" ? "模拟回答" : "AI回答"}</span>
        {a.spoken && <Volume2 size={13} />}
      </div>
      <p className={a.status === "failed" ? "failed" : ""}>{a.answer}</p>
      {a.stale && <span className="stale">原文已变化，请重新提问后复核</span>}
      <div className="citations">
        {a.citations.map((c, i) => (
          <button key={c.id} onClick={() => jump(c.id)}>
            <Link2 size={12} />
            依据 {i + 1}
          </button>
        ))}
      </div>
      <details className="trace">
        <summary>
          工具执行轨迹 · {a.trace.filter((t) => t.tool).length} 次调用
        </summary>
        {a.trace.map((t, i) => (
          <div key={i}>
            <strong>{String(t.tool ?? "执行失败")}</strong>
            <pre>{JSON.stringify(t, null, 2)}</pre>
          </div>
        ))}
      </details>
      {shared &&
        host &&
        a.status !== "failed" &&
        a.citations.length > 0 &&
        !a.stale &&
        (snapshot?.decisions.some((d) => d.answer_id === a.id) ? (
          <span className="adopted">
            <CheckCircle2 size={13} />
            已采纳，见会后纪要
          </span>
        ) : (
          <button className="text-button" onClick={() => setConfirming(a)}>
            <Check size={14} />
            复核并采纳
          </button>
        ))}
    </article>
  );

  return (
    <div className="app">
      <aside className="sidebar">
        <button className="brand" onClick={() => void selectSession(null)}>
          <span className="brandmark">
            k<span>·</span>
          </span>
          <div>
            小K<span>会议工作台</span>
          </div>
        </button>
        <button className="new-meeting" onClick={() => setModal("create")}>
          <Plus size={18} />
          新建会议
        </button>
        <nav>
          <button
            className={!active ? "nav-item selected" : "nav-item"}
            onClick={() => void selectSession(null)}
          >
            <LayoutDashboard size={18} />
            工作台
          </button>
          <button className="nav-item" onClick={() => setModal("join")}>
            <Link2 size={18} />
            加入会议
          </button>
        </nav>
        <div className="side-label">
          最近会议 <span>{sessions.length}</span>
        </div>
        <div className="meeting-nav">
          {sessions.length === 0 ? (
            <p className="side-empty">
              你的下一次好讨论，
              <br />
              从这里开始。
            </p>
          ) : (
            sessions.map((s) => (
              <button
                key={s.meeting_id}
                className={`meeting-item ${active?.meeting_id === s.meeting_id ? "selected" : ""}`}
                onClick={() => void selectSession(s)}
              >
                <MessageSquare size={15} />
                <span>{s.title}</span>
              </button>
            ))
          )}
        </div>
        <div className="side-bottom">
          <div className="local-label">
            <span className="status-dot" />
            本机运行 · 数据存于主机
          </div>
          <button
            className="nav-item"
            onClick={() => {
              getHealth();
              setModal("settings");
            }}
          >
            <Settings2 size={17} />
            模型与运行状态
          </button>
          <div className="profile">
            <Avatar name={me?.name ?? "我"} />
            <div>
              {me?.name ?? "个人工作空间"}
              <small>
                {active ? (host ? "主持人" : "参会者") : "PERSONAL WORKSPACE"}
              </small>
            </div>
            <span className="version">v0.2</span>
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="breadcrumb">
            我的工作空间 <ChevronRight size={14} />
            <strong>{active ? "会议详情" : "工作台"}</strong>
          </div>
          <div className="top-right">
            <span className="mode-label">
              {health?.model_mode === "mock"
                ? "模拟模型 · 尚未接入 LLM"
                : health?.model_mode === "local"
                  ? "本地大模型"
                  : "模型接口"}
            </span>
            <button
              className="icon"
              aria-label="运行状态"
              onClick={() => setModal("settings")}
            >
              <Settings2 size={17} />
            </button>
          </div>
        </header>
        {error && (
          <div className="alert error" role="alert">
            <span>{error}</span>
            <button
              className="icon"
              aria-label="关闭错误"
              onClick={() => setError("")}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {notice && (
          <div className="toast" role="status">
            <CheckCircle2 size={17} />
            {notice}
          </div>
        )}
        {!active ? (
          <div className="home">
            <div className="home-intro">
              <div className="eyebrow">
                <span /> YOUR MEETING, REMEMBERED
              </div>
              <h1>
                专注讨论。
                <br />
                <span>让小K记住重要的事。</span>
              </h1>
              <p>
                从第一句话到最后一个决定。录下讨论，识别发言人，
                <br className="desktop-break" />
                把团队的想法整理成有依据的下一步。
              </p>
              <div className="hero-actions">
                <button className="primary" onClick={() => setModal("create")}>
                  <Plus size={17} />
                  开始一场会议
                </button>
                <button className="secondary" onClick={() => setModal("join")}>
                  使用会议码加入
                  <ArrowRight size={16} />
                </button>
              </div>
              <div className="hero-note">
                <ShieldCheck size={15} />
                一个主机，多个设备。录音前请取得参会者同意。
              </div>
            </div>
            <div className="home-visual">
              <div className="visual-heading">
                <div>
                  <span className="eyebrow">THE LITTLE THINGS THAT MATTER</span>
                  <h3>每个想法，都有来处。</h3>
                </div>
                <span className="pill">界面示意</span>
              </div>
              <div className="sample-line">
                <Avatar name="林" />
                <div>
                  <strong>
                    林然 <time>00:28</time>
                  </strong>
                  <p>
                    这次我们先把核心体验做好，
                    <br />
                    让每一个决定都能找到依据。
                  </p>
                </div>
              </div>
              <div className="sample-line">
                <Avatar name="陈" index={1} />
                <div>
                  <strong>
                    陈一 <time>00:42</time>
                  </strong>
                  <p>我来负责多设备测试，周五前完成。</p>
                </div>
              </div>
              <div className="mini-answer">
                <Sparkles size={18} />
                <div>
                  <strong>从讨论，到下一步</strong>
                  <p>记录发言 · 追溯依据 · 确认行动</p>
                </div>
                <ArrowRight size={20} />
              </div>
              <div className="visual-wave">
                {Array.from({ length: 38 }, (_, i) => (
                  <i key={i} style={{ height: 8 + ((i * 17 + 7) % 31) }} />
                ))}
                <span>留住重要的声音</span>
              </div>
            </div>
            {sessions.length > 0 && (
              <div className="home-recent">
                <h2>最近会议</h2>
                {sessions.slice(0, 6).map((s) => (
                  <button
                    className="recent-card"
                    key={s.meeting_id}
                    onClick={() => void selectSession(s)}
                  >
                    <MessageSquare size={17} />
                    <span>{s.title}</span>
                    <ArrowRight size={15} />
                  </button>
                ))}
              </div>
            )}
            <div className="home-section-title">
              <h2>为一次完整的讨论而设计</h2>
              <span>轻量运行，认真记录</span>
            </div>
            <div className="feature-grid">
              {[
                [
                  <AudioLines />,
                  "听见与认出",
                  "录音转写与声纹匹配，让每段发言找到自己的位置。",
                ],
                [
                  <Users />,
                  "一起看见",
                  "手机与电脑同步查看，短暂断线后恢复已有记录。",
                ],
                [
                  <Sparkles />,
                  "问问小K",
                  "语音唤醒、工具检索、引用回答，让结论有据可查。",
                ],
              ].map(([icon, title, text]) => (
                <div className="feature" key={String(title)}>
                  <span className="feature-icon">{icon}</span>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </div>
              ))}
            </div>
            <div className="demo-banner">
              <div>
                <span className="demo-icon">
                  <FileText size={22} />
                </span>
                <div>
                  <strong>先体验一次完整的会议流程</strong>
                  <p>使用标记清楚的文字样例，试试记录、问答和导出。</p>
                </div>
              </div>
              <button
                className="secondary"
                disabled={pending}
                onClick={() => void createDemo()}
              >
                {pending ? <Loader2 className="spin" size={16} /> : null}
                打开演示会议
                <ArrowRight size={16} />
              </button>
            </div>
          </div>
        ) : !snapshot ? (
          <div className="loading">
            <Loader2 className="spin" />
            正在连接会议…
            <button
              className="secondary"
              onClick={() => removeSession(active.meeting_id)}
            >
              移除此设备上的会议入口
            </button>
          </div>
        ) : (
          <>
            <section className="meeting-heading">
              <div>
                <div className="eyebrow">
                  MEETING SPACE{" "}
                  <span className={`connection ${connected ? "online" : ""}`}>
                    {connected
                      ? "实时同步已连接"
                      : "重连中 · 已保存记录仍可查看"}
                  </span>
                </div>
                <h1>{snapshot.meeting.title}</h1>
                <p>
                  {new Date(snapshot.meeting.created * 1000).toLocaleDateString(
                    "zh-CN",
                    { month: "long", day: "numeric", weekday: "long" },
                  )}
                  <span>·</span>
                  {snapshot.members.length} 位参会者<span>·</span>
                  {snapshot.meeting.status === "ended"
                    ? "会议已结束"
                    : recording
                      ? "正在录音"
                      : "准备就绪"}
                </p>
              </div>
              <div className="heading-actions">
                <button
                  className="secondary"
                  onClick={() => setModal("invite")}
                >
                  <Users size={16} />
                  邀请加入
                </button>
                <button
                  className="secondary"
                  onClick={() => void exportMeeting()}
                >
                  <ArrowDownToLine size={16} />
                  导出纪要
                </button>
                {host && (
                  <button
                    className="icon"
                    aria-label="删除会议"
                    onClick={() => setModal("delete")}
                  >
                    <Trash2 size={17} />
                  </button>
                )}
              </div>
            </section>
            <div className="tabs">
              <div>
                {[
                  ["records", "会议记录", FileText],
                  ["voices", "参会者与声纹", Fingerprint],
                  ["voice-answers", "语音问答", Volume2],
                  ["summary", "会后纪要", Clipboard],
                ].map(([id, label, Icon]) => {
                  const Component = Icon as typeof FileText;
                  return (
                    <button
                      className={tab === id ? "active" : ""}
                      key={String(id)}
                      onClick={() => setTab(String(id))}
                    >
                      <Component size={17} />
                      {String(label)}
                      {id === "records" && (
                        <span>{snapshot.utterances.length}</span>
                      )}
                    </button>
                  );
                })}
              </div>
              <span className="privacy-note">
                <ShieldCheck size={14} />
                仅会议成员可见
              </span>
            </div>
            <div className="workspace">
              <section className="record-panel">
                {tab === "records" ? (
                  <>
                    <div className="panel-toolbar">
                      <h2>
                        讨论的每一步
                        <span className="small-count">
                          {records.length} 段发言
                        </span>
                      </h2>
                      <div>
                        <label className="search">
                          <Search size={15} />
                          <input
                            aria-label="搜索会议记录"
                            placeholder="搜索记录"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                          />
                        </label>
                        {host && (
                          <button
                            className="icon"
                            title="手动添加记录"
                            aria-label="手动添加记录"
                            onClick={() => setModal("manual")}
                          >
                            <Plus size={18} />
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="transcript">
                      {records.length ? (
                        records.map((u, i) => (
                          <article
                            id={"u-" + u.id}
                            className="utterance"
                            key={u.id}
                          >
                            <Avatar name={u.speaker} index={i} />
                            <div className="utterance-body">
                              <header>
                                <strong>{u.speaker}</strong>
                                <time>{timeLabel(u.start)}</time>
                                <span className="source-label">
                                  {u.source === "manual"
                                    ? "手动记录"
                                    : u.source === "edited"
                                      ? "人工修正"
                                      : u.match?.status === "matched"
                                        ? "声纹匹配"
                                        : "转录 · 待确认"}
                                </span>
                                {host && (
                                  <button
                                    className="edit-button"
                                    onClick={() => setEditing(u)}
                                  >
                                    编辑
                                  </button>
                                )}
                              </header>
                              <p>{u.text}</p>
                            </div>
                          </article>
                        ))
                      ) : (
                        <Empty
                          icon={<AudioLines size={28} />}
                          title={
                            query
                              ? "没有找到匹配的记录"
                              : "让讨论从第一句话开始"
                          }
                          text={
                            query
                              ? "换一个关键词试试。"
                              : "在主电脑开始录音，或上传一段音频。文字会同步出现在所有设备上。"
                          }
                        />
                      )}
                    </div>
                    {snapshot.jobs.length > 0 && (
                      <div className="jobs">
                        {snapshot.jobs.map((j) => (
                          <div key={j.id}>
                            <span>
                              {j.status === "succeeded" ? (
                                <CheckCircle2 size={14} />
                              ) : j.status === "failed" ? (
                                <X size={14} />
                              ) : (
                                <Loader2 className="spin" size={14} />
                              )}
                              音频任务 ·{" "}
                              {
                                (
                                  {
                                    queued: "排队中",
                                    running: "正在转录",
                                    succeeded: "处理完成",
                                    failed: "处理失败",
                                  } as Record<string, string>
                                )[j.status]
                              }
                            </span>
                            {j.error && <small>{j.error}</small>}
                            {j.status === "failed" && host && (
                              <button
                                className="text-button"
                                onClick={() =>
                                  void perform(() =>
                                    api(
                                      path + `/jobs/${j.id}/retry`,
                                      active,
                                      {},
                                    ),
                                  )
                                }
                              >
                                重试
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    <footer
                      className={`recorder-bar ${recording ? "is-recording" : ""}`}
                    >
                      <div className="recorder-status">
                        <span className="mic-symbol">
                          <Mic size={18} />
                        </span>
                        <div>
                          <strong>
                            {recording
                              ? voiceLabels[snapshot.runtime.voice_state]
                              : "主设备录音"}
                          </strong>
                          <small>
                            {recording
                              ? "请保持页面打开 · 仅一台设备收音"
                              : host
                                ? "麦克风仅在你点击开始后启用"
                                : "由主持人主设备采集会议音频"}
                          </small>
                        </div>
                        {recording && (
                          <div className="live-meter">
                            {Array.from({ length: 8 }, (_, i) => (
                              <i
                                key={i}
                                style={{
                                  height: Math.min(
                                    28,
                                    5 + level * 800 * (1 + (i % 3) / 2),
                                  ),
                                }}
                              />
                            ))}
                          </div>
                        )}
                      </div>
                      {host && snapshot.meeting.status !== "ended" && (
                        <div className="record-actions">
                          <label
                            className="icon upload-button"
                            title="上传音频"
                          >
                            <Upload size={18} />
                            <input
                              type="file"
                              aria-label="上传音频"
                              accept="audio/*"
                              disabled={recording || pending}
                              onChange={(e) => {
                                if (e.target.files?.[0])
                                  void uploadAudio(e.target.files[0]);
                                e.target.value = "";
                              }}
                            />
                          </label>
                          <button
                            className={recording ? "stop-button" : "primary"}
                            disabled={pending}
                            onClick={() =>
                              void (recording
                                ? stopRecording()
                                : startRecording())
                            }
                          >
                            {recording ? (
                              <Square size={14} />
                            ) : (
                              <Mic size={16} />
                            )}{" "}
                            {recording ? "结束录音" : "开始录音"}
                          </button>
                        </div>
                      )}
                    </footer>
                  </>
                ) : tab === "voice-answers" ? (
                  <div className="summary-content shared-answers">
                    <h2>小K语音问答</h2>
                    <p className="muted">
                      语音唤醒或“语音提问”产生的问答对全体参会者可见，自动保存在会议纪要中。AI建议仍需复核才能成为决定。
                    </p>
                    {snapshot.answers.length ? (
                      snapshot.answers.map((a) => renderAnswer(a, true))
                    ) : (
                      <Empty
                        icon={<Volume2 size={26} />}
                        title="还没有共享语音问答"
                        text="主设备开始录音后，说“小K小K”并提问。"
                      />
                    )}
                  </div>
                ) : tab === "voices" ? (
                  <div className="voices-content">
                    <div className="section-heading">
                      <div>
                        <h2>熟悉的声音，各有名字</h2>
                        <p>
                          仅匹配本场已登记且授权的声纹。分数不足时保留未知。
                        </p>
                      </div>
                      <Fingerprint size={30} />
                    </div>
                    <div className="voice-note">
                      <ShieldCheck size={18} />
                      <p>
                        声纹只保存在主电脑，用于本场发言标注。没有设备的人可由主持人添加，并在本人同意后代为登记、撤回或删除声纹。
                      </p>
                    </div>
                    {host && (
                      <button
                        className="secondary"
                        disabled={
                          pending ||
                          snapshot.meeting.status === "ended" ||
                          snapshot.members.length >= 12
                        }
                        onClick={() => setModal("attendee")}
                      >
                        <Plus size={16} />
                        添加现场参会者
                      </button>
                    )}
                    {snapshot.members.map((m, i) => {
                      const voice = snapshot.voiceprints.find(
                        (v) => v.member_id === m.id,
                      );
                      return (
                        <div className="member-row" key={m.id}>
                          <Avatar name={m.name} index={i} />
                          <div>
                            <strong>
                              {m.name}
                              {m.id === active.member_id && <span>（我）</span>}
                            </strong>
                            <small>
                              {m.role === "host"
                                ? "主持人"
                                : m.role === "attendee"
                                  ? "现场参会者 · 无需设备"
                                  : "参会者"}{" "}
                              ·{" "}
                              {voice
                                ? voice.enabled
                                  ? "声纹已登记 · 本场启用"
                                  : "声纹已登记 · 已撤回"
                                : "尚未登记声纹"}
                            </small>
                          </div>
                          {m.id === active.member_id ||
                          (host && m.role === "attendee") ? (
                            <div className="member-actions">
                              <button
                                className="secondary"
                                disabled={
                                  pending ||
                                  snapshot.runtime.busy ||
                                  snapshot.runtime.recording ||
                                  snapshot.meeting.status === "ended"
                                }
                                onClick={() => {
                                  setVoiceTarget(
                                    m.id === active.member_id ? null : m,
                                  );
                                  setVoiceBlob(null);
                                  setVoiceSeconds(0);
                                  setModal("voice");
                                }}
                              >
                                {voice ? "重新登记" : "登记声纹"}
                              </button>
                              {host && m.role === "attendee" && voice && (
                                <>
                                  {voice.enabled && (
                                    <button
                                      className="text-button"
                                      disabled={
                                        pending || snapshot.runtime.busy
                                      }
                                      onClick={() =>
                                        void perform(() =>
                                          api(
                                            path +
                                              `/members/${m.id}/voiceprint`,
                                            active,
                                            { enabled: false },
                                            "PATCH",
                                          ),
                                        )
                                      }
                                    >
                                      撤回使用
                                    </button>
                                  )}
                                  <button
                                    className="text-button danger"
                                    disabled={pending || snapshot.runtime.busy}
                                    onClick={() =>
                                      void perform(async () => {
                                        await api(
                                          path + `/members/${m.id}/voiceprint`,
                                          active,
                                          undefined,
                                          "DELETE",
                                        );
                                        setNotice(`${m.name}的声纹档案已删除`);
                                      })
                                    }
                                  >
                                    删除声纹
                                  </button>
                                </>
                              )}
                            </div>
                          ) : voice?.enabled ? (
                            <span className="pill green">
                              <Check size={13} />
                              已启用
                            </span>
                          ) : (
                            <span className="pill">待登记</span>
                          )}
                        </div>
                      );
                    })}
                    {ownVoice && (
                      <div className="voice-manage">
                        <button
                          className="secondary"
                          onClick={() =>
                            void perform(() =>
                              api(
                                path + "/voiceprint",
                                active,
                                { enabled: !ownVoice.enabled },
                                "PATCH",
                              ),
                            )
                          }
                        >
                          {ownVoice.enabled ? "撤回本场使用" : "允许本场使用"}
                        </button>
                        <button
                          className="text-button danger"
                          onClick={() =>
                            void perform(async () => {
                              await api(
                                path + "/voiceprint",
                                active,
                                undefined,
                                "DELETE",
                              );
                              setNotice("你的声纹档案已删除");
                            })
                          }
                        >
                          删除我的声纹
                        </button>
                      </div>
                    )}
                    <p className="muted footnote">
                      当前阈值尚未经过你和朋友的样本校准。短句、远场及重叠发言可能识别失败；人工修改不会自动更新声纹。
                    </p>
                  </div>
                ) : (
                  <div className="summary-content">
                    <div className="section-heading">
                      <div>
                        <h2>把讨论，变成下一步</h2>
                        <p>AI草稿与已确认的决定分开保存。</p>
                      </div>
                      {host && (
                        <button
                          className="primary"
                          disabled={pending || snapshot.runtime.busy}
                          onClick={() =>
                            void perform(() =>
                              api(path + "/summary", active, {}),
                            )
                          }
                        >
                          <Sparkles size={16} />
                          {latest ? "重新生成" : "生成纪要"}
                        </button>
                      )}
                    </div>
                    {latest ? (
                      <>
                        <div className="summary-badge">
                          <span className="pill">
                            {latest.mode === "mock"
                              ? "模拟纪要"
                              : "AI草稿 · 待复核"}
                          </span>
                          {latest.stale && (
                            <span className="pill amber">
                              原文已修改，请重新生成
                            </span>
                          )}
                        </div>
                        <p className="overview" id={"summary-" + latest.id}>
                          {latest.data.overview}
                        </p>
                        <h3>讨论要点</h3>
                        <ul className="highlights">
                          {latest.data.highlights.map((x, i) => (
                            <li key={i}>{x}</li>
                          ))}
                        </ul>
                        <div className="citations">
                          {latest.data.citations.map((id, i) => (
                            <button key={id} onClick={() => jump(id)}>
                              <Link2 size={12} />
                              原文 {i + 1}
                            </button>
                          ))}
                        </div>
                        {latest.data.proposed_actions.length > 0 && (
                          <>
                            <h3>AI建议 · 待确认</h3>
                            <ul>
                              {latest.data.proposed_actions.map((x, i) => (
                                <li key={i}>{x}</li>
                              ))}
                            </ul>
                          </>
                        )}
                      </>
                    ) : (
                      <Empty
                        icon={<Clipboard size={26} />}
                        title="讨论之后，留下一份清晰的纪要"
                        text="有了会议记录，就可以生成摘要和待办建议。"
                      />
                    )}
                    <div className="shared-answers">
                      <h3>小K语音问答 · 纪要附录</h3>
                      <p className="muted">
                        自动保存并随纪要导出；私聊不包含在内。
                      </p>
                      {snapshot.answers.map((a) => renderAnswer(a, true))}
                    </div>
                    <div className="decisions">
                      <h3>
                        <CheckCircle2 size={19} />
                        已确认的决定与待办
                        <span>{snapshot.decisions.length}</span>
                      </h3>
                      {snapshot.decisions.length ? (
                        snapshot.decisions.map((d) => (
                          <div className="decision" key={d.id}>
                            <Check size={17} />
                            <p>
                              {d.text}
                              <small>
                                {d.kind === "action" ? "待办" : "决定"} ·
                                主持人确认采纳 · 来源保留
                              </small>
                            </p>
                          </div>
                        ))
                      ) : (
                        <p className="muted">
                          还没有已确认的事项。可在小K回答下点击“采纳”，复核后确认。
                        </p>
                      )}
                    </div>
                    {host && snapshot.meeting.status !== "ended" && (
                      <button
                        className="secondary end-meeting"
                        disabled={recording || pending}
                        onClick={() =>
                          void perform(() => api(path + "/end", active, {}))
                        }
                      >
                        结束本次会议
                      </button>
                    )}
                  </div>
                )}
              </section>
              <aside className="assistant-panel">
                <header className="assistant-heading">
                  <span className="k-orb">
                    <Sparkles size={21} />
                  </span>
                  <div>
                    <h2>与小K私聊</h2>
                    <p>仅本人可见 · 不写入纪要</p>
                  </div>
                  <span className="status-dot" />
                </header>
                <div className="assistant-mode">
                  <span>PRIVATE CHAT</span>
                  <span>
                    {snapshot.model_mode === "mock" ? "模拟模式" : "真实模型"}
                  </span>
                </div>
                <div className="answers">
                  {!privateScope ? (
                    <div className="assistant-welcome">
                      <h3>私聊已关闭</h3>
                      <p>
                        会议结束后，私聊内容已自动删除。共享语音问答仍保留在会议纪要中。
                      </p>
                    </div>
                  ) : privateAnswers.length === 0 ? (
                    <div className="assistant-welcome">
                      <div className="assistant-art">
                        <span />
                        <Sparkles size={33} />
                        <i />
                        <b />
                      </div>
                      <h3>有问题，随时问我。</h3>
                      <p>
                        我能读取会议记录、纪要和共享语音问答，
                        <br />
                        私聊仅你可见，会议结束后自动删除。
                      </p>
                      <div className="suggestions">
                        {[
                          "我们刚才做了哪些决定？",
                          "谁负责测试，什么时候完成？",
                          "还有哪些问题没有确定？",
                        ].map((q) => (
                          <button
                            key={q}
                            disabled={privatePending || !privateScope}
                            onClick={() => void ask(undefined, q)}
                          >
                            {q}
                            <ArrowRight size={14} />
                          </button>
                        ))}
                      </div>
                      <small>
                        模拟模式仅演示工具执行与原文引用，
                        <br />
                        不代表真实模型的理解能力。
                      </small>
                    </div>
                  ) : (
                    privateAnswers.map((a) => renderAnswer(a, false))
                  )}
                </div>
                <div className="ask-footer">
                  {privatePending && (
                    <div className="thinking">
                      <Loader2 size={14} className="spin" />
                      正在执行，请稍候…
                    </div>
                  )}
                  {host && recording && (
                    <div className="voice-controls">
                      {tab !== "records" && (
                        <button onClick={() => void stopRecording()}>
                          <Square size={13} />
                          结束录音
                        </button>
                      )}
                      {snapshot.runtime.voice_state === "speaking" ? (
                        <button onClick={stopPlayback}>
                          <Square size={13} />
                          停止播报
                        </button>
                      ) : (
                        <button
                          disabled={
                            snapshot.runtime.voice_state !== "listening"
                          }
                          onClick={() =>
                            audioWs.current?.send(
                              JSON.stringify({ type: "wake" }),
                            )
                          }
                        >
                          <Mic size={14} />
                          语音提问
                        </button>
                      )}
                      <span>语音问答会共享并记入纪要</span>
                    </div>
                  )}
                  <form className="ask-box" onSubmit={(e) => void ask(e)}>
                    <textarea
                      aria-label="向小K提问"
                      placeholder={
                        privateScope
                          ? "私聊小K，会议结束后自动删除…"
                          : "会议已结束"
                      }
                      disabled={!privateScope || privatePending}
                      rows={2}
                      value={question}
                      onChange={(e) => setQuestion(e.target.value)}
                      onKeyDown={(e) => {
                        if (
                          e.key === "Enter" &&
                          !e.shiftKey &&
                          !e.nativeEvent.isComposing
                        ) {
                          e.preventDefault();
                          void ask();
                        }
                      }}
                    />
                    <div>
                      <span>答案附原文引用</span>
                      <button
                        aria-label="发送问题"
                        disabled={
                          !question.trim() || privatePending || !privateScope
                        }
                      >
                        <Send size={16} />
                      </button>
                    </div>
                  </form>
                  <p>私聊不共享、不导出，会议结束后自动删除。</p>
                </div>
              </aside>
            </div>
          </>
        )}
      </main>
      {(modal === "create" || modal === "join") && (
        <Modal
          title={modal === "create" ? "开始一场新会议" : "加入一场会议"}
          onClose={() => setModal(null)}
        >
          <form onSubmit={(e) => void createMeeting(e, modal === "join")}>
            <p className="modal-description">
              {modal === "create"
                ? "给讨论起个名字。创建后可邀请朋友加入。"
                : "输入主持人分享的会议码，一起查看讨论记录。"}
            </p>
            {modal === "create" ? (
              <label>
                会议名称
                <input
                  autoFocus
                  name="title"
                  required
                  maxLength={100}
                  placeholder="例如：项目方案讨论"
                />
              </label>
            ) : (
              <label>
                会议码
                <input
                  autoFocus
                  name="code"
                  required
                  minLength={8}
                  maxLength={8}
                  placeholder="8位会议码"
                  style={{ textTransform: "uppercase" }}
                />
              </label>
            )}
            <label>
              你的昵称
              <input
                name="name"
                required
                maxLength={30}
                placeholder="其他参会者会看到这个名字"
              />
            </label>
            <label className="checkbox">
              <input type="checkbox" required />
              我知晓会议将记录音频和文字，已取得或将取得参会者同意；声纹需另行授权。
            </label>
            <button className="primary full" disabled={pending}>
              {pending ? <Loader2 size={17} className="spin" /> : null}
              {modal === "create" ? "创建会议" : "同意并加入"}
              <ArrowRight size={17} />
            </button>
          </form>
        </Modal>
      )}
      {modal === "invite" && snapshot && (
        <Modal title="邀请朋友，一起讨论" onClose={() => setModal(null)}>
          <p className="modal-description">
            朋友打开主电脑的局域网访问地址，选择“加入会议”，输入下方会议码。
          </p>
          <div className="invite-code">{snapshot.meeting.code}</div>
          <button
            className="secondary full"
            onClick={() => {
              navigator.clipboard
                ?.writeText(snapshot.meeting.code)
                .then(() => setNotice("会议码已复制"))
                .catch(() => setNotice("请手动复制上方会议码"));
            }}
          >
            <Clipboard size={16} />
            复制会议码
          </button>
          <p className="footnote muted">
            邀请有效至{" "}
            {new Date(snapshot.meeting.invite_expires * 1000).toLocaleString()}
            。主电脑使用 localhost 录音；朋友的设备仅查看时可使用局域网地址。
          </p>
        </Modal>
      )}
      {modal === "manual" && (
        <Modal title="添加一段会议记录" onClose={() => setModal(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              void perform(async () => {
                await api(path + "/utterances", active, {
                  speaker: f.get("speaker"),
                  text: f.get("text"),
                  start: Number(f.get("start")),
                  end: Number(f.get("start")),
                  request_key: uid(),
                });
                setModal(null);
              });
            }}
          >
            <p className="modal-description">
              用于补充文字或测试流程，记录会明确标记为手动输入。
            </p>
            <label>
              发言人
              <input
                name="speaker"
                required
                defaultValue={me?.name ?? "未标注"}
                maxLength={40}
              />
            </label>
            <label>
              时间（秒）
              <input
                name="start"
                type="number"
                required
                min={0}
                defaultValue={Math.ceil(snapshot?.utterances.at(-1)?.end ?? 0)}
              />
            </label>
            <label>
              发言内容
              <textarea name="text" rows={4} required maxLength={4000} />
            </label>
            <button className="primary full" disabled={pending}>
              保存并同步
            </button>
          </form>
        </Modal>
      )}
      {editing && (
        <Modal title="修正会议记录" onClose={() => setEditing(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              void perform(async () => {
                await api(
                  path + "/utterances/" + editing.id,
                  active,
                  {
                    text: f.get("text"),
                    speaker: f.get("speaker"),
                    version: editing.version,
                  },
                  "PATCH",
                );
                setEditing(null);
              });
            }}
          >
            <label>
              发言人
              <input
                name="speaker"
                defaultValue={editing.speaker}
                required
                maxLength={40}
              />
            </label>
            <label>
              发言内容
              <textarea
                name="text"
                rows={5}
                defaultValue={editing.text}
                required
                maxLength={4000}
              />
            </label>
            <p className="footnote muted">
              修改后，原有AI回答和纪要会提示依据已变化。
            </p>
            <button className="primary full" disabled={pending}>
              保存修正
            </button>
          </form>
        </Modal>
      )}
      {confirming && (
        <Modal title="复核并采纳AI建议" onClose={() => setConfirming(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              void perform(async () => {
                await api(
                  path + "/answers/" + confirming.id + "/confirm",
                  active,
                  { text: f.get("text"), kind: f.get("kind") },
                );
                setConfirming(null);
                setNotice("已保存为主持人确认的事项");
              });
            }}
          >
            <p className="modal-description">
              请核对原文，并把下面内容修改为团队明确同意的事项。保留AI来源。
            </p>
            <label>
              类型
              <select name="kind">
                <option value="decision">会议决定</option>
                <option value="action">待办事项</option>
              </select>
            </label>
            <label>
              确认内容
              <textarea
                name="text"
                required
                rows={5}
                defaultValue={confirming.answer}
                maxLength={4000}
              />
            </label>
            <button className="primary full" disabled={pending}>
              <Check size={17} />
              确认采纳
            </button>
          </form>
        </Modal>
      )}
      {modal === "attendee" && (
        <Modal title="添加现场参会者" onClose={() => setModal(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const form = new FormData(e.currentTarget);
              void perform(async () => {
                await api(path + "/members", active, {
                  name: form.get("name"),
                  consent: form.get("consent") === "true",
                });
                setModal(null);
                setNotice("参会者已添加，可以在名单中登记其声纹");
              });
            }}
          >
            <p className="modal-description">
              无需手机或登录。填写姓名后即可进入本场名单；声纹需要另行登记。每场最多12人，包含主持人和通过设备加入的人。
            </p>
            <label>
              参会者姓名
              <input
                name="name"
                required
                maxLength={30}
                placeholder="例如：张三"
              />
            </label>
            <label className="checkbox">
              <input type="checkbox" name="consent" value="true" required />
              我已告知本人会议将记录音频和文字，并取得同意。
            </label>
            <button className="primary full" disabled={pending}>
              确认添加
            </button>
          </form>
        </Modal>
      )}
      {modal === "voice" && (
        <Modal
          title={voiceTarget ? `登记${voiceTarget.name}的声纹` : "登记我的声纹"}
          onClose={() => {
            void stopVoice();
            setModal(null);
          }}
        >
          <form onSubmit={(e) => void submitEnroll(e)}>
            <p className="modal-description">
              请让被登记者本人在安静环境朗读10～20秒，其他人保持安静。重录会替换该参会者原有声纹；登记音频不长期保留。
            </p>
            <div className="read-prompt">
              “大家好，我来介绍一下今天的讨论计划。我们先回顾上周的进展，再确认下一步需要完成的工作。遇到不确定的问题，可以一起交流。”
            </div>
            <div className="enroll-control">
              <button
                type="button"
                className={voiceRecording ? "stop-button" : "secondary"}
                onClick={() =>
                  void (voiceRecording ? stopVoice() : beginEnroll())
                }
              >
                {voiceRecording ? <Square size={15} /> : <Mic size={16} />}{" "}
                {voiceRecording
                  ? `结束录音 · ${voiceSeconds}s`
                  : voiceBlob
                    ? "重新录制"
                    : voiceTarget
                      ? "录制参会者的声音"
                      : "录制我的声音"}
              </button>
              {voiceBlob && (
                <span className="green-text">
                  <CheckCircle2 size={15} />
                  已录制
                </span>
              )}
            </div>
            <label>
              或者上传1～3段被登记者本人的音频文件
              <input
                type="file"
                name="file"
                multiple
                accept="audio/*"
                onChange={() => setVoiceBlob(null)}
              />
            </label>
            <label className="checkbox">
              <input type="checkbox" name="consent" value="true" required />
              {voiceTarget
                ? `我已取得${voiceTarget.name}本人同意，将其声纹保存在主电脑并用于本场会议匹配；可按本人要求撤回或删除。`
                : "我同意将自己的声纹保存在主电脑，并用于本场会议匹配。我可以随时撤回或删除。"}
            </label>
            <button
              className="primary full"
              disabled={pending || voiceRecording}
            >
              {pending ? (
                <Loader2 size={16} className="spin" />
              ) : (
                <Fingerprint size={17} />
              )}
              提取并保存声纹
            </button>
          </form>
        </Modal>
      )}
      {modal === "delete" && (
        <Modal title="删除这场会议？" onClose={() => setModal(null)}>
          <p className="modal-description">
            将删除会议记录、音频、声纹档案和AI回答，其他设备也会失去访问。此操作无法撤销。
          </p>
          <div className="modal-actions">
            <button className="secondary" onClick={() => setModal(null)}>
              取消
            </button>
            <button
              className="stop-button"
              disabled={pending || recording}
              onClick={() =>
                void perform(async () => {
                  await api(path, active, undefined, "DELETE");
                  if (active) removeSession(active.meeting_id);
                  setModal(null);
                })
              }
            >
              确认删除会议
            </button>
          </div>
        </Modal>
      )}
      {modal === "settings" && (
        <Modal title="模型与运行状态" onClose={() => setModal(null)}>
          <p className="modal-description">
            文本大模型暂未选定，接口可替换。本页面不接收或显示API密钥。
          </p>
          <div className="status-list">
            <div>
              <span>文本模型</span>
              <strong>
                {health?.model_mode === "mock"
                  ? "模拟模式 · 不调用真实LLM"
                  : (health?.model_mode ?? "未连接")}
              </strong>
            </div>
            {(["asr", "speaker", "kws"] as const).map((k, i) => (
              <div key={k}>
                <span>{["语音转录", "声纹识别", "关键词唤醒"][i]}</span>
                <strong className={health?.speech[k] ? "green-text" : ""}>
                  {health?.speech[k] ? "模型文件已就绪" : "待安装模型"}
                </strong>
              </div>
            ))}
            <div>
              <span>语音播报</span>
              <strong>浏览器中文语音 · 需真机测试</strong>
            </div>
            <div>
              <span>运行版本</span>
              <strong>{health?.version ?? "—"}</strong>
            </div>
          </div>
          <p className="footnote muted">
            文件就绪不代表识别效果已经验收。声纹和唤醒需要用你与朋友的真实声音验证。
          </p>
          <button className="secondary full" onClick={() => void getHealth()}>
            刷新状态
          </button>
        </Modal>
      )}
    </div>
  );
}
