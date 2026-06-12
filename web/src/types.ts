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
  | "abandoned"
  | "diagnostic";

// One contender value of a 5W1H facet category, with its consensus points.
export interface FacetContender {
  value: string;
  score: number;
}

// One 5W1H category of the live board (the honest reasoning tile).
export interface Facet {
  category: string; // who | what | when | where | why | how
  label: string;
  contenders: FacetContender[];
  focus: boolean; // the slot the current question targets
}

// For kind === "diagnostic": what failed and what was attempted.
export interface Diagnostic {
  reason: string;
  consecutive_failures: number;
  llm_unreachable: boolean;
  restart_attempted: boolean;
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
  facets: Facet[]; // live 5W1H consensus board (honest tile)
  diagnostic: Diagnostic | null;
  // The question this one replaced via the opposition button ("" otherwise).
  flipped_from: string;
}

export interface HistoryEntry {
  kind: "query" | "synthesis" | "context" | "diagnostic" | "edit";
  text: string;
  answer: Answer | null;
  rationale: string;
}

// One woven slot of the live draft, with its confidence band.
export interface BannerPart {
  category: string;
  value: string;
  band: "locked" | "working";
}

// A value the caregiver struck from the proposal (✗-edit).
export interface BannerBan {
  category: string;
  value: string;
}

// The living proposal banner — the evolving draft utterance. `pending`
// renders the glowing "Pending synthesis…"; `ready` lights the
// propose-ready vibrance (board-readiness).
export interface Banner {
  state: "pending" | "draft";
  text: string;
  ready: boolean;
  parts: BannerPart[];
  banned: BannerBan[];
  muted: string[];
}

export interface RoundState {
  session_id: string;
  round_id: string;
  topic_id: string;
  event: RoundEvent;
  banner: Banner;
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
  // Board evolution (seeds / final / restart snapshots) — autopsy data; the
  // review UI does not render it (yet).
  board?: Record<string, unknown>;
  model: string;
  recorded_at: string;
}
