export type Session = {
  meeting_id: string;
  token: string;
  member_id: string;
  role: "host" | "guest";
  title: string;
};
export type Utterance = {
  id: string;
  text: string;
  speaker: string;
  start: number;
  end: number;
  source: string;
  version: number;
  match?: { status: string; score?: number } | null;
};
export type Answer = {
  id: string;
  question: string;
  answer: string;
  citations: { id: string; version: number }[];
  trace: Record<string, unknown>[];
  mode: string;
  status: string;
  stale: boolean;
  spoken: boolean;
  created: number;
};
export type Snapshot = {
  meeting: {
    id: string;
    title: string;
    code: string;
    status: string;
    seq: number;
    revision: number;
    created: number;
    invite_expires: number;
  };
  members: { id: string; name: string; role: string }[];
  utterances: Utterance[];
  answers: Answer[];
  decisions: { id: string; answer_id: string; text: string; kind: string }[];
  voiceprints: {
    id: string;
    member_id: string;
    name: string;
    enabled: boolean;
  }[];
  summaries: {
    id: string;
    data: {
      overview: string;
      highlights: string[];
      proposed_actions: string[];
      citations: string[];
    };
    mode: string;
    stale: boolean;
  }[];
  jobs: { id: string; status: string; error?: string }[];
  runtime: {
    voice_state: string;
    answer_id: string | null;
    busy: boolean;
    recording: boolean;
  };
  model_mode: string;
};
export type Health = {
  model_mode: string;
  version: string;
  speech: { asr: boolean; speaker: boolean; kws: boolean };
};
export async function api<T>(
  path: string,
  session: Session | null = null,
  body?: unknown,
  method?: string,
): Promise<T> {
  const form = body instanceof FormData;
  const response = await fetch(path, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers: {
      ...(session ? { Authorization: `Bearer ${session.token}` } : {}),
      ...(!form && body !== undefined
        ? { "Content-Type": "application/json" }
        : {}),
    },
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "网络请求失败" }));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : "输入格式不正确，请检查后重试",
    );
  }
  return response.json();
}
export const roomPath = (s: Session) => `/api/meetings/${s.meeting_id}`;
export const socketURL = (path: string) =>
  `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
export function saveSessions(sessions: Session[]) {
  localStorage.setItem("xiaok.sessions.v2", JSON.stringify(sessions));
}
export function loadSessions(): Session[] {
  try {
    return JSON.parse(localStorage.getItem("xiaok.sessions.v2") || "[]");
  } catch {
    return [];
  }
}
export function timeLabel(seconds: number) {
  return `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")}`;
}
