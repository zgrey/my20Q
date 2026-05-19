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

export interface RoundEvent {
  kind: EventKind;
  text: string;
  rationale: string;
  query_index: number;
  engine: "reasoning" | "fallback";
  emergency_screen: EmergencyScreen | null;
  pictogram: string | null;
}

export interface HistoryEntry {
  kind: "query" | "synthesis" | "context";
  text: string;
  answer: Answer | null;
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
