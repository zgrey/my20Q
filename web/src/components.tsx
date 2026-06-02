import { useEffect, useRef, useState } from "preact/hooks";

import type {
  Answer,
  HistoryEntry,
  RecordingStatus,
  RoundEvent,
  RoundState,
  Topic,
} from "./types";

const ANSWER_LABEL: Record<Answer, string> = {
  yes: "Yes",
  no: "No",
  kinda: "Kinda",
  not_sure: "Not sure",
};

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

// ---------------------------------------------------------------- TopicBar

interface TopicBarProps {
  topics: Topic[];
  topicId: string;
  onTopic: (id: string) => void;
  recording: RecordingStatus | null;
  onTogglePause: () => void;
  theme: "dark" | "light";
  onToggleTheme: () => void;
  engine: string;
  busy: boolean;
  onExport: () => void;
  canExport: boolean;
  view: "live" | "review";
  onView: (v: "live" | "review") => void;
  audioOn: boolean;
  onToggleAudio: () => void;
  ttsAvailable: boolean;
  models: string[];
  currentModel: string | null;
  canSelectModel: boolean;
  onSelectModel: (model: string) => void;
  augmented: boolean;
  onToggleAugmented: () => void;
}

/** Persistent header: view tabs, topic, engine badge, save, rec, theme. */
export function TopicBar(props: TopicBarProps) {
  const {
    topics,
    topicId,
    onTopic,
    recording,
    onTogglePause,
    theme,
    onToggleTheme,
    engine,
    busy,
    onExport,
    canExport,
    view,
    onView,
    audioOn,
    onToggleAudio,
    ttsAvailable,
    models,
    currentModel,
    canSelectModel,
    onSelectModel,
    augmented,
    onToggleAugmented,
  } = props;
  const reviewing = view === "review";
  const audioTitle = !audioOn
    ? "Audio off — click to enable readouts and input beeps"
    : ttsAvailable
      ? "Audio on — queries and utterances are read aloud"
      : "Audio on (input beeps) — voice readouts need piper installed";
  const themeLabel =
    theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
  return (
    <header class="topbar">
      <div class="brand">
        my20Q <span>· Caregiver Cockpit</span>
      </div>
      <div class="view-tabs" role="tablist">
        <button
          class={reviewing ? "" : "active"}
          onClick={() => onView("live")}
        >
          Live
        </button>
        <button
          class={reviewing ? "active" : ""}
          onClick={() => onView("review")}
        >
          Review
        </button>
      </div>
      {!reviewing && engine && (
        <span class={`engine-badge ${engine}`} title="Active dialogue engine">
          {engine}
        </span>
      )}
      {!reviewing && canSelectModel && models.length > 0 && (
        <label
          class="model-select"
          title="Model used for questioning & reasoning — applies to the next question"
        >
          <span>Model</span>
          <select
            value={currentModel ?? ""}
            disabled={busy}
            onChange={(e) => onSelectModel((e.target as HTMLSelectElement).value)}
          >
            {models.map((m) => (
              <option value={m}>{m}</option>
            ))}
          </select>
        </label>
      )}
      {!reviewing && (
        <button
          class={`augment-toggle${augmented ? " on" : ""}`}
          onClick={onToggleAugmented}
          title={
            augmented
              ? "Augmented reasoning ON — hierarchical zoom + depth loops + a visible trace (applies to the next round)"
              : "Augmented reasoning OFF — flat single-pass (the opaque baseline). Click to enable; applies to the next round."
          }
        >
          ⌁ Zoom{augmented ? " ON" : " off"}
        </button>
      )}
      <span class="tb-spacer" />
      {!reviewing && (
        <label class="topic">
          <span>Topic</span>
          <select
            value={topicId}
            disabled={busy || topics.length === 0}
            onChange={(e) => onTopic((e.target as HTMLSelectElement).value)}
          >
            {topics.map((t) => (
              <option value={t.id}>{t.label}</option>
            ))}
          </select>
        </label>
      )}
      <button
        class="save-btn"
        onClick={onExport}
        disabled={!canExport}
        title="Save this conversation to a file"
      >
        ⬇<span class="btn-label"> Save</span>
      </button>
      {recording && recording.enabled ? (
        <button
          class={`rec ${recording.paused ? "paused" : "on"}`}
          onClick={onTogglePause}
          title={
            recording.paused
              ? "Recording paused — click to resume"
              : `Recording to the patient dataset — ${recording.rounds} rounds, ` +
                `${formatBytes(recording.bytes)} of ` +
                `${formatBytes(recording.threshold_bytes)}`
          }
        >
          <span class="dot" />
          {recording.paused ? "Paused" : "REC"}
          <span class={`saved ${recording.status}`}>
            {formatBytes(recording.bytes)}
          </span>
        </button>
      ) : (
        <span
          class="rec off"
          title="Recording is off — no real patient profile loaded"
        >
          <span class="dot" />
          Rec off
        </span>
      )}
      <button
        class={`audio-toggle${audioOn && !ttsAvailable ? " degraded" : ""}`}
        onClick={onToggleAudio}
        aria-label={audioTitle}
        title={audioTitle}
      >
        {audioOn ? "🔊" : "🔇"}
      </button>
      <button
        class="theme-toggle"
        onClick={onToggleTheme}
        aria-label={themeLabel}
        title={themeLabel}
      >
        {theme === "dark" ? "☀" : "☾"}
      </button>
    </header>
  );
}

// ----------------------------------------------------------- Conversation

function HistoryRow({ entry }: { entry: HistoryEntry }) {
  if (entry.kind === "context") {
    return (
      <div class="turn context">
        <span class="tag">caregiver context</span>
        <span class="ctx-text">{entry.text}</span>
      </div>
    );
  }
  const isSynthesis = entry.kind === "synthesis";
  return (
    <div class={`turn question${isSynthesis ? " was-synthesis" : ""}`}>
      <div class="q">{isSynthesis ? `Proposed: ${entry.text}` : entry.text}</div>
      {entry.answer && (
        <span class={`chip ${entry.answer}`}>{ANSWER_LABEL[entry.answer]}</span>
      )}
    </div>
  );
}

/** Tile 1 — the round transcript, the live query, and terminal cards. */
export function ConversationTile({
  round,
  busy,
  phase,
}: {
  round: RoundState | null;
  busy: boolean;
  phase: string | null;
}) {
  const streamRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  });

  let body;
  if (!round) {
    body = <p class="empty">{busy ? "Connecting to the server…" : "No round."}</p>;
  } else {
    const ev = round.event;
    const terminal = round.outcome !== null;
    const lastEntry = round.history[round.history.length - 1];
    const awaitingAnswer =
      !terminal && (ev.kind === "query" || ev.kind === "synthesis");
    body = (
      <>
        {round.history.map((h, i) => (
          <HistoryRow entry={h} key={i} />
        ))}
        {!terminal && !busy && ev.kind === "query" && (
          <div class="turn question pending">
            {ev.preface && <div class="preface">{ev.preface}</div>}
            <div class="q">{ev.text}</div>
            <div class="awaiting">awaiting answer</div>
          </div>
        )}
        {!terminal && !busy && ev.kind === "synthesis" && (
          <div class="synthesis proposed">
            <div class="label">Proposed message — confirm with the patient</div>
            <div class="utterance">“{ev.text}”</div>
            <div class="confirm-note">Yes confirms it · any other answer keeps going.</div>
          </div>
        )}
        {busy && !terminal && (
          <div class="thinking" aria-live="polite">
            <span class="thinking-dots">
              <i />
              <i />
              <i />
            </span>
            <span class="thinking-text">
              {phase === "re-asking"
                ? "Re-checking the question…"
                : "Thinking — working out the next question…"}
            </span>
          </div>
        )}
        {!busy && awaitingAnswer && lastEntry?.kind === "context" && (
          <div class="ctx-hint">
            Context added — the question above was refreshed to use it.
          </div>
        )}
        {round.outcome === "synthesized" && (
          <div class="synthesis">
            <div class="label">Confirmed utterance</div>
            <div class="utterance">“{round.final_utterance}”</div>
            <div class="confirm-note">Ready to be spoken.</div>
          </div>
        )}
        {round.outcome === "abandoned" && (
          <div class="notice">
            This round ended without a confirmed message. Start a new round.
          </div>
        )}
        {round.outcome === "emergency" && ev.emergency_screen && (
          <div class="emergency-card">
            <div class="label">🆘 {ev.emergency_screen.title}</div>
            <p>{ev.emergency_screen.body}</p>
            <ul>
              {ev.emergency_screen.actions.map((a) => (
                <li key={a.id}>{a.label}</li>
              ))}
            </ul>
          </div>
        )}
      </>
    );
  }

  return (
    <section class="tile conversation">
      <h2>Conversation</h2>
      <div class="stream" ref={streamRef}>
        {body}
      </div>
    </section>
  );
}

// -------------------------------------------------------------- Pictogram

const GLYPH: Record<string, string> = {
  query: "❓",
  synthesis: "🗣️",
  synthesized: "🗣️",
  emergency: "🆘",
  abandoned: "…",
};

/** Tile 2 — the AAC pictogram retrieved for the current query. */
export function PictogramTile({ event }: { event: RoundEvent | null }) {
  const kind = event?.kind ?? "query";
  const pictogram = event?.pictogram ?? null;
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => setFailed(null), [pictogram]);

  const showImage = pictogram !== null && failed !== pictogram;
  return (
    <section class="tile pictogram">
      <h2>Pictogram</h2>
      <div class="picto-box">
        {showImage ? (
          <img
            class="picto-img"
            src={`/assets/arasaac/${pictogram}.png`}
            alt={pictogram ?? ""}
            onError={() => setFailed(pictogram)}
          />
        ) : (
          <div class="glyph">{GLYPH[kind] ?? "🖼️"}</div>
        )}
      </div>
      <p class="caption">
        {pictogram
          ? `AAC concept: ${pictogram}`
          : "No matching AAC pictogram for this query."}
      </p>
    </section>
  );
}

// --------------------------------------------------------------- Reasoning

// Opposed-emotion pairs for the sliders. id = "<left>_<right>"; the
// slider value runs -1 (left) .. +1 (right). The backend reads the id
// to label each pole — see agent/prompts.py.
const EMOTION_PAIRS: { id: string; left: string; right: string }[] = [
  { id: "sad_happy", left: "Sad", right: "Happy" },
  { id: "anxious_calm", left: "Anxious", right: "Calm" },
  { id: "tired_energetic", left: "Tired", right: "Energetic" },
  { id: "lonely_connected", left: "Lonely", right: "Connected" },
  { id: "uncomfortable_comfortable", left: "Uncomfortable", right: "Comfortable" },
  { id: "frustrated_content", left: "Frustrated", right: "Content" },
  { id: "confused_clear", left: "Confused", right: "Clear" },
  { id: "scared_safe", left: "Scared", right: "Safe" },
  { id: "unwell_well", left: "Unwell", right: "Well" },
  { id: "withdrawn_engaged", left: "Withdrawn", right: "Engaged" },
];

function EmotionSliders({
  values,
  onChange,
  onReset,
}: {
  values: Record<string, number>;
  onChange: (id: string, value: number) => void;
  onReset: () => void;
}) {
  // "Touched" = any slider deviates from neutral. We don't compare floats
  // exactly because the <input type="range"> step can leave float residue
  // (e.g. 0.5000000001) after a drag back toward centre.
  const touched = EMOTION_PAIRS.some(
    (p) => Math.abs(values[p.id] ?? 0) > 0.001,
  );
  return (
    <div class="emotion">
      <div class="emotion-header">
        <h3>Emotional reading</h3>
        <button
          class="reset-emotion"
          onClick={onReset}
          disabled={!touched}
          title="Reset every slider to the neutral centre"
        >
          Reset
        </button>
      </div>
      <div class="sliders">
        {EMOTION_PAIRS.map((pair) => (
          <label class="slider-row" key={pair.id}>
            <span class="slabel">{pair.left}</span>
            <input
              type="range"
              min="-1"
              max="1"
              step="0.5"
              value={values[pair.id] ?? 0}
              onChange={(e) =>
                onChange(pair.id, parseFloat((e.target as HTMLInputElement).value))
              }
            />
            <span class="slabel">{pair.right}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

const PHASE_LABEL: Record<string, string> = {
  connected: "live",
  thinking: "thinking",
  "re-asking": "re-asking",
};

/** Tile 3 — live reasoning narration + the caregiver's emotional sliders.
 *
 * `sse` reflects the EventSource connection state — coloured pulse next to
 * the heading. `phase` is the latest server-side reasoner phase delivered
 * over that channel (visible proof that the SSE round-trip is working). */
export function ReasoningTile({
  event,
  busy,
  phase,
  sse,
  emotion,
  onEmotion,
  onResetEmotion,
}: {
  event: RoundEvent | null;
  busy: boolean;
  phase: string | null;
  sse: "connecting" | "open" | "closed";
  emotion: Record<string, number>;
  onEmotion: (id: string, value: number) => void;
  onResetEmotion: () => void;
}) {
  let text: string;
  let live = false;
  if (busy) {
    live = true;
    text =
      phase === "re-asking"
        ? "Re-asking — the last query needed to be a clean yes/no."
        : "Thinking…";
  } else if (!event) {
    text = "Waiting to start.";
  } else if (event.kind === "synthesized") {
    text = "Round complete — the utterance is confirmed.";
  } else if (event.kind === "abandoned") {
    text = "Round ended without a confirmed message.";
  } else if (event.kind === "emergency") {
    text = "Emergency topic — questioning is bypassed.";
  } else {
    text = event.rationale || "—";
    live = true;
  }
  // Surface only meaningful phases — "connected" is implied by the dot.
  const phaseLabel =
    phase && phase !== "connected" ? PHASE_LABEL[phase] ?? phase : null;
  const sseTitle =
    sse === "open"
      ? "Live progress channel connected"
      : sse === "connecting"
        ? "Connecting to the live progress channel…"
        : "Live progress channel disconnected — events may be delayed";
  const hypotheses = event?.hypotheses ?? [];
  const trace = event?.reasoning_trace ?? [];
  const breadcrumb = event?.breadcrumb ?? [];
  return (
    <section class="tile reasoning">
      <h2>
        Live reasoning
        <span class={`pulse sse-${sse}`} title={sseTitle} />
        {live && phaseLabel && <span class="phase-tag">{phaseLabel}</span>}
      </h2>
      <p class="reason-text">{text}</p>
      {trace.length > 0 && (
        <div class="trace">
          {trace.map((t, i) => (
            <div class={`trace-line ${t.kind}`} key={i}>
              <span class="trace-tag">
                {t.kind === "thinking" ? "model thinking" : "strategy"}
              </span>
              <span class="trace-text">{t.text}</span>
            </div>
          ))}
        </div>
      )}
      {breadcrumb.length > 0 && (
        <div class="breadcrumb" title="Narrowing path (need → object → modifier)">
          {breadcrumb.map((b, i) => (
            <span class="crumb" key={i}>
              {i > 0 && <span class="crumb-arrow">→</span>}
              {b}
            </span>
          ))}
        </div>
      )}
      {hypotheses.length > 0 && (
        <div class="belief">
          <div class="belief-head">What the need might be</div>
          <ul class="belief-list">
            {hypotheses.map((h, i) => {
              const pct = Math.round(h.weight * 100);
              return (
                <li class={`belief-row${i === 0 ? " lead" : ""}`} key={h.need}>
                  <span class="belief-need">{h.need}</span>
                  <span class="belief-bar">
                    <span class="belief-fill" style={`width:${pct}%`} />
                  </span>
                  <span class="belief-pct">{pct}%</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <EmotionSliders
        values={emotion}
        onChange={onEmotion}
        onReset={onResetEmotion}
      />
    </section>
  );
}

// ------------------------------------------------------------------- Input

interface InputProps {
  canAnswer: boolean;
  canUndo: boolean;
  terminal: boolean;
  busy: boolean;
  onAnswer: (a: Answer) => void;
  onUndo: () => void;
  onSend: (text: string) => void;
  onNewRound: () => void;
}

const ANSWER_BUTTONS: { a: Answer; label: string; key: string }[] = [
  { a: "yes", label: "Yes", key: "Y" },
  { a: "no", label: "No", key: "N" },
  { a: "kinda", label: "Kinda", key: "K" },
  { a: "not_sure", label: "Not sure", key: "S" },
];

/** Tile 4 — quick answers, undo, and the caregiver context field. */
export function InputTile(props: InputProps) {
  const { canAnswer, canUndo, terminal, busy, onAnswer, onUndo, onSend, onNewRound } =
    props;
  const [text, setText] = useState("");

  const submit = () => {
    const v = text.trim();
    if (v && !busy) {
      onSend(v);
      setText("");
    }
  };

  return (
    <section class="tile input">
      <div class="answers-grid">
        {ANSWER_BUTTONS.map((b) => (
          <button
            class={`answer ${b.a}`}
            disabled={!canAnswer}
            onClick={() => onAnswer(b.a)}
          >
            {b.label}
            <kbd>{b.key}</kbd>
          </button>
        ))}
      </div>
      <div class="secondary-row">
        <button class="answer undo" disabled={!canUndo} onClick={onUndo}>
          Undo<kbd>U</kbd>
        </button>
        <button class="answer quit" disabled={busy} onClick={onNewRound}>
          {terminal ? "New round" : "Quit"}
          <kbd>Q</kbd>
        </button>
      </div>
      <div class="context-row">
        <input
          type="text"
          placeholder="Type context to steer the questioning…"
          value={text}
          disabled={busy}
          onInput={(e) => setText((e.target as HTMLInputElement).value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <button class="send" onClick={submit} disabled={busy || !text.trim()}>
          Send
        </button>
      </div>
    </section>
  );
}
