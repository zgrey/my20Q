import { useEffect, useMemo, useState } from "preact/hooks";

import { api, friendlyError } from "./api";
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
  isRoundEnd: boolean;
}

function buildSteps(records: RoundRecord[]): Step[] {
  const steps: Step[] = [];
  records.forEach((round, roundIndex) => {
    const entries = round.queries ?? [];
    if (entries.length === 0) {
      steps.push({ roundIndex, round, entry: null, entryNumber: 0, isRoundEnd: true });
      return;
    }
    let qn = 0;
    entries.forEach((entry, i) => {
      if (entry.kind !== "context") qn += 1;
      steps.push({
        roundIndex,
        round,
        entry,
        entryNumber: entry.kind === "context" ? 0 : qn,
        isRoundEnd: i === entries.length - 1,
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

/** Read-only session review — load a saved/recorded .jsonl and step through
 *  each query, response, and the reasoning. See task #22. */
export function ReviewDashboard() {
  const [records, setRecords] = useState<RoundRecord[] | null>(null);
  const [source, setSource] = useState<string>("");
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [serverList, setServerList] = useState<RecordingFile[]>([]);

  // Server-side recordings exist only for a real patient; empty otherwise.
  useEffect(() => {
    api
      .recordings()
      .then(setServerList)
      .catch(() => setServerList([]));
  }, []);

  const steps = useMemo(() => (records ? buildSteps(records) : []), [records]);
  const load = (recs: RoundRecord[], label: string) => {
    setRecords(recs);
    setSource(label);
    setStep(0);
    setError(null);
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

  if (!records || steps.length === 0) {
    return (
      <div class="review">
        <h2>Session review</h2>
        {loader}
        {records && steps.length === 0 && (
          <p class="empty">That save has no rounds to review.</p>
        )}
      </div>
    );
  }

  const cur = steps[Math.min(step, steps.length - 1)];
  const round = cur.round;
  const total = steps.length;

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

        <section class="review-step">
          {cur.entry === null ? (
            <p class="empty">
              This round had no questions ({round.outcome ?? "—"}).
            </p>
          ) : cur.entry.kind === "context" ? (
            <div class="rs-context">
              <span class="tag">caregiver context</span>
              <p>{cur.entry.text}</p>
            </div>
          ) : (
            <>
              <div class="rs-kind">
                {cur.entry.kind === "synthesis"
                  ? "Proposed message"
                  : `Question ${cur.entryNumber}`}
              </div>
              <p class="rs-text">{cur.entry.text}</p>
              {cur.entry.answer && (
                <span class={`chip ${cur.entry.answer}`}>
                  {ANSWER_LABEL[cur.entry.answer]}
                </span>
              )}
              {cur.entry.rationale && (
                <div class="rs-reasoning">
                  <span class="tag">reasoning</span>
                  <p>{cur.entry.rationale}</p>
                </div>
              )}
            </>
          )}
        </section>
      </div>

      <div class="review-nav">
        <button disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
          ‹ Prev
        </button>
        <span class="review-progress">
          Step {step + 1} / {total}
        </span>
        <button
          disabled={step >= total - 1}
          onClick={() => setStep((s) => s + 1)}
        >
          Next ›
        </button>
      </div>
    </div>
  );
}
