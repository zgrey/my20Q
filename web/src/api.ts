// Fetch client for the cockpit API. Paths are relative ("/api/...") so the
// Vite dev proxy (and, in production, the backend serving the built app)
// keep everything same-origin.

import type {
  Answer,
  ModelsStatus,
  RecordingFile,
  RecordingStatus,
  RoundRecord,
  RoundState,
  Topic,
  TTSStatus,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, init);
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`${resp.status}: ${detail}`);
  }
  return (await resp.json()) as T;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  topics: () => request<Topic[]>("/topics"),
  createSession: () => post<{ session_id: string }>("/sessions"),
  startRound: (sid: string, topicId: string, seedContext = "") =>
    post<RoundState>(`/sessions/${sid}/rounds`, {
      topic_id: topicId,
      seed_context: seedContext,
    }),
  answer: (sid: string, rid: string, answer: Answer) =>
    post<RoundState>(`/sessions/${sid}/rounds/${rid}/answer`, { answer }),
  addContext: (sid: string, rid: string, text: string) =>
    post<RoundState>(`/sessions/${sid}/rounds/${rid}/context`, { text }),
  undo: (sid: string, rid: string) =>
    post<RoundState>(`/sessions/${sid}/rounds/${rid}/undo`),
  retry: (sid: string, rid: string) =>
    post<RoundState>(`/sessions/${sid}/rounds/${rid}/retry`),
  // The opposition button: re-render the pending question in its opposite
  // connotation (an action, not an answer).
  flip: (sid: string, rid: string) =>
    post<RoundState>(`/sessions/${sid}/rounds/${rid}/flip`),
  // ✓ on the banner: conclude the round with the current draft.
  accept: (sid: string, rid: string) =>
    post<RoundState>(`/sessions/${sid}/rounds/${rid}/accept`),
  // ✗ on the banner: a real-time edit against the live draft (ban / mute).
  edit: (sid: string, rid: string, text: string) =>
    post<RoundState>(`/sessions/${sid}/rounds/${rid}/edit`, { text }),
  eventsUrl: (sid: string, rid: string) =>
    `${BASE}/sessions/${sid}/rounds/${rid}/events`,
  exportUrl: (sid: string, format: "jsonl" | "md" = "jsonl") =>
    `${BASE}/sessions/${sid}/export?format=${format}`,
  recording: () => request<RecordingStatus>("/recording"),
  setPaused: (paused: boolean) =>
    post<RecordingStatus>("/recording/pause", { paused }),
  setEmotion: (sid: string, values: Record<string, number>) =>
    post<{ ok: boolean }>(`/sessions/${sid}/emotion`, { values }),
  recordings: () => request<RecordingFile[]>("/recordings"),
  recordingRecords: (sid: string) =>
    request<RoundRecord[]>(`/recordings/${encodeURIComponent(sid)}`),
  ttsStatus: () => request<TTSStatus>("/tts/status"),
  models: () => request<ModelsStatus>("/models"),
  selectModel: (model: string) => post<ModelsStatus>("/model", { model }),
};

/** Turn a fetch failure into a caregiver-readable message. */
export function friendlyError(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
    return "Cannot reach the my20Q server. Start it with:  python -m my20q.api";
  }
  return msg;
}
