// Cockpit types — mirror the FastAPI schemas in src/my20q/api/schemas.py.

export type Answer = "yes" | "no" | "kinda" | "not_sure";

export interface Topic {
  id: string;
  label: string;
  emergency: boolean;
}

export interface EmergencyScreen {
  title: string;
  body: string;
  actions: { id: string; label: string }[];
}

export type EventKind =
  | "query"
  | "synthesis"
  | "emergency"
  | "synthesized"
  | "abandoned";

// One candidate need in the live belief, with its current weight (0..1).
export interface Hypothesis {
  need: string;
  weight: number;
}

export interface RoundEvent {
  kind: EventKind;
  text: string;
  rationale: string;
  preface: string; // short spoken lead-in read aloud just before the query
  query_index: number;
  engine: "reasoning" | "fallback";
  emergency_screen: EmergencyScreen | null;
  pictogram: string | null;
  hypotheses: Hypothesis[]; // live belief over candidate needs (honest tile)
}

export interface HistoryEntry {
  kind: "query" | "synthesis" | "context";
  text: string;
  answer: Answer | null;
  rationale: string;
}

export interface RoundState {
  session_id: string;
  round_id: string;
  topic_id: string;
  event: RoundEvent;
  history: HistoryEntry[];
  outcome: string | null;
  final_utterance: string;
  query_count: number;
  engine: string;
}

export interface RecordingStatus {
  enabled: boolean;
  paused: boolean;
  bytes: number;
  threshold_bytes: number;
  status: "ok" | "warning" | "over" | "disabled";
  rounds: number;
}

// Whether local (piper) speech is available — drives the audio toggle.
export interface TTSStatus {
  available: boolean;
  voice: string | null;
  reason: string;
}

// Local Ollama models available for human-trial selection.
export interface ModelsStatus {
  models: string[];
  current: string | null;
  can_select: boolean;
}

// One recorded session on disk (the review dashboard's server picker).
export interface RecordingFile {
  session_id: string;
  rounds: number;
  bytes: number;
  modified: string;
}

// One round as saved/recorded — the build_round_record schema. The same
// shape comes from an uploaded .jsonl line or GET /api/recordings/{sid}.
export interface RoundRecord {
  session_id: string;
  round_id: string;
  topic_id: string;
  engine: string;
  outcome: string | null;
  final_utterance: string;
  query_count: number;
  job_b: number;
  queries: HistoryEntry[];
  emotional_state: Record<string, number>;
  model: string;
  recorded_at: string;
}
