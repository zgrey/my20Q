import { Fragment } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import { api, friendlyError } from "./api";
import { speak, stopSpeaking } from "./tts";
import type { Answer, HistoryEntry, RecordingFile, RoundRecord } from "./types";

const ANSWER_LABEL: Record<Answer, string> = {
  yes: "Yes",
  no: "No",
  kinda: "Kinda",
  not_sure: "Not sure",
};

const OUTCOME_LABEL: Record<string, string> = {
  synthesized: "Confirmed",
  abandoned: "No message",
  emergency: "Emergency",
};

/** Parse a .jsonl save/recording (or a JSON array) into round records. */
function parseRecords(text: string): RoundRecord[] {
  const trimmed = text.trim();
  const recs: unknown[] = trimmed.startsWith("[")
    ? JSON.parse(trimmed)
    : trimmed
        .split(/\r?\n/)
        .filter((l) => l.trim())
        .map((l) => JSON.parse(l));
  const first = recs[0];
  if (!recs.length || typeof first !== "object" || first === null || !("queries" in first)) {
    throw new Error("not a my20Q save");
  }
  return recs as RoundRecord[];
}

interface Step {
  roundIndex: number;
  round: RoundRecord;
  entry: HistoryEntry | null; // null = a round with no queries (e.g. emergency)
  entryNumber: number; // 1-based query/synthesis number in the round; 0 for context
  isRoundStart: boolean;
}

function buildSteps(records: RoundRecord[]): Step[] {
  const steps: Step[] = [];
  records.forEach((round, roundIndex) => {
    const entries = round.queries ?? [];
    if (entries.length === 0) {
      steps.push({ roundIndex, round, entry: null, entryNumber: 0, isRoundStart: true });
      return;
    }
    let qn = 0;
    entries.forEach((entry, i) => {
      const numbered = entry.kind !== "context" && entry.kind !== "diagnostic";
      if (numbered) qn += 1;
      steps.push({
        roundIndex,
        round,
        entry,
        entryNumber: numbered ? qn : 0,
        isRoundStart: i === 0,
      });
    });
  });
  return steps;
}

function emotionLine(state: Record<string, number>): string {
  const parts = Object.entries(state || {})
    .filter(([, v]) => v)
    .map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v.toFixed(2)}`);
  return parts.join(" · ");
}

/** Compose the spoken line for a step: question, then reasoning, then the
 *  patient's answer — read aloud during navigation / auto-play. */
function stepSpeech(s: Step): string {
  const e = s.entry;
  if (!e) return "";
  if (e.kind === "diagnostic") return ""; // technical detail — not voiced
  if (e.kind === "context") return e.text;
  const parts = [e.text];
  if (e.rationale) parts.push(e.rationale);
  if (e.answer) parts.push(`The patient then indicated ${ANSWER_LABEL[e.answer]}`);
  return parts.join(". ");
}

function StepKindLabel({ step }: { step: Step }) {
  if (step.entry === null) {
    return (
      <span class="rstep-kind">
        {OUTCOME_LABEL[step.round.outcome ?? ""] ?? "No questions"}
      </span>
    );
  }
  if (step.entry.kind === "context") {
    return <span class="rstep-kind context">caregiver context</span>;
  }
  if (step.entry.kind === "diagnostic") {
    return <span class="rstep-kind diagnostic">reasoning failure</span>;
  }
  return (
    <span class="rstep-kind">
      {step.entry.kind === "synthesis" ? "Proposed message" : `Question ${step.entryNumber}`}
    </span>
  );
}

/** Read-only session review — load a saved/recorded .jsonl and step through
 *  each query, response, and reasoning. The whole conversation is shown as a
 *  scrollable transcript; the active step is highlighted and the rest dim,
 *  descending one pair (question + reasoning) at a time. See task #22. */
export function ReviewDashboard({
  audioOn,
  ttsAvailable,
}: {
  audioOn: boolean;
  ttsAvailable: boolean;
}) {
  const [records, setRecords] = useState<RoundRecord[] | null>(null);
  const [source, setSource] = useState<string>("");
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [serverList, setServerList] = useState<RecordingFile[]>([]);

  const canVoice = audioOn && ttsAvailable;

  // Server-side recordings exist only for a real patient; empty otherwise.
  useEffect(() => {
    api
      .recordings()
      .then(setServerList)
      .catch(() => setServerList([]));
  }, []);

  // Stop any readout when this view unmounts.
  useEffect(() => () => stopSpeaking(), []);

  const steps = useMemo(() => (records ? buildSteps(records) : []), [records]);
  const total = steps.length;

  const load = (recs: RoundRecord[], label: string) => {
    stopSpeaking();
    setRecords(recs);
    setSource(label);
    setStep(0);
    setPlaying(false);
    setError(null);
  };

  // Keep the highlighted step scrolled into view as it descends.
  const activeRef = useRef<HTMLLIElement>(null);
  useEffect(() => {
    activeRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [step]);

  // Auto-play: speak the active step (when voice is on) then advance; without
  // voice, advance on a fixed cadence. Re-runs on each step so it chains.
  useEffect(() => {
    if (!playing || total === 0) return;
    let cancelled = false;
    let timer: number | undefined;
    const advance = () => {
      if (cancelled) return;
      if (step >= total - 1) setPlaying(false);
      else setStep(step + 1);
    };
    if (canVoice) {
      speak(stepSpeech(steps[step])).then(() => advance());
    } else {
      timer = window.setTimeout(advance, 4500);
    }
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [playing, step, canVoice, steps, total]);

  const goTo = (i: number) => {
    if (i < 0 || i >= total) return;
    setPlaying(false);
    stopSpeaking();
    setStep(i);
    if (canVoice) speak(stepSpeech(steps[i]));
  };

  const togglePlay = () => {
    if (total === 0) return;
    setPlaying((p) => {
      if (p) stopSpeaking();
      // Restart from the top if we're parked on the last step.
      else if (step >= total - 1) setStep(0);
      return !p;
    });
  };

  const onUpload = (e: Event) => {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        load(parseRecords(String(reader.result)), file.name);
      } catch {
        setError("That file isn’t a my20Q save (expected .jsonl rounds).");
      }
    };
    reader.readAsText(file);
  };

  const onPickServer = (sid: string) => {
    if (!sid) return;
    api
      .recordingRecords(sid)
      .then((recs) => load(recs, `${sid.slice(0, 8)} (recorded)`))
      .catch((err) => setError(friendlyError(err)));
  };

  const loader = (
    <div class="review-loader">
      <label class="review-upload">
        <span>Open a saved conversation</span>
        <input type="file" accept=".jsonl,.json" onChange={onUpload} />
      </label>
      {serverList.length > 0 && (
        <label class="review-server">
          <span>…or a recorded session</span>
          <select
            onChange={(e) => onPickServer((e.target as HTMLSelectElement).value)}
          >
            <option value="">Choose a recording…</option>
            {serverList.map((r) => (
              <option value={r.session_id}>
                {r.session_id.slice(0, 8)} — {r.rounds} round(s) · {r.modified}
              </option>
            ))}
          </select>
        </label>
      )}
      {error && <div class="review-error">{error}</div>}
    </div>
  );

  if (!records || total === 0) {
    return (
      <div class="review">
        <h2>Session review</h2>
        {loader}
        {records && total === 0 && (
          <p class="empty">That save has no rounds to review.</p>
        )}
      </div>
    );
  }

  const cur = steps[Math.min(step, total - 1)];
  const round = cur.round;

  return (
    <div class="review">
      <div class="review-top">
        <h2>Session review</h2>
        <span class="review-source">{source}</span>
        {loader}
      </div>

      <div class="review-body">
        <aside class="review-round-info">
          <div class="ri-line">
            <span class="ri-key">Round</span>
            <span class="ri-val">
              {cur.roundIndex + 1} / {records.length}
            </span>
          </div>
          <div class="ri-line">
            <span class="ri-key">Topic</span>
            <span class="ri-val">{round.topic_id}</span>
          </div>
          <div class="ri-line">
            <span class="ri-key">Engine</span>
            <span class="ri-val">{round.engine}</span>
          </div>
          <div class="ri-line">
            <span class="ri-key">Outcome</span>
            <span class={`ri-val outcome ${round.outcome ?? ""}`}>
              {OUTCOME_LABEL[round.outcome ?? ""] ?? round.outcome ?? "—"}
            </span>
          </div>
          {round.outcome === "synthesized" && round.final_utterance && (
            <div class="ri-utterance">“{round.final_utterance}”</div>
          )}
          <div class="ri-line">
            <span class="ri-key">Job-B</span>
            <span class="ri-val">{round.job_b}</span>
          </div>
          {emotionLine(round.emotional_state) && (
            <div class="ri-emotion">{emotionLine(round.emotional_state)}</div>
          )}
        </aside>

        <ol class="review-transcript">
          {steps.map((s, i) => {
            const active = i === step;
            const e = s.entry;
            return (
              <Fragment key={i}>
                {s.isRoundStart && (
                  <li class="rstep-round" aria-hidden="true">
                    <span class="rr-num">Round {s.roundIndex + 1}</span>
                    <span class="rr-topic">{s.round.topic_id}</span>
                    <span class={`rr-outcome ${s.round.outcome ?? ""}`}>
                      {OUTCOME_LABEL[s.round.outcome ?? ""] ?? s.round.outcome ?? "—"}
                    </span>
                  </li>
                )}
                <li
                  ref={active ? activeRef : undefined}
                  class={`rstep${active ? " active" : ""}${
                    e?.kind === "context" ? " is-context" : ""
                  }`}
                  onClick={() => goTo(i)}
                >
                  <StepKindLabel step={s} />
                  {e === null ? (
                    <p class="rstep-empty">
                      This round had no questions ({s.round.outcome ?? "—"}).
                    </p>
                  ) : (
                    <>
                      <p class="rstep-text">{e.text}</p>
                      {e.kind !== "context" && e.answer && (
                        <span class={`chip ${e.answer}`}>
                          {ANSWER_LABEL[e.answer]}
                        </span>
                      )}
                      {e.kind !== "context" && e.rationale && (
                        <div class="rstep-reasoning">
                          <span class="tag">reasoning</span>
                          <p>{e.rationale}</p>
                        </div>
                      )}
                    </>
                  )}
                </li>
              </Fragment>
            );
          })}
        </ol>
      </div>

      <div class="review-nav">
        <button disabled={step === 0} onClick={() => goTo(step - 1)}>
          ‹ Prev
        </button>
        <button
          class={`review-play${playing ? " playing" : ""}`}
          onClick={togglePlay}
          title={playing ? "Pause auto-play" : "Auto-play through the conversation"}
        >
          {playing ? "❚❚ Pause" : "▶ Auto-play"}
        </button>
        <span class="review-progress">
          Step {step + 1} / {total}
        </span>
        <button disabled={step >= total - 1} onClick={() => goTo(step + 1)}>
          Next ›
        </button>
      </div>
    </div>
  );
}
