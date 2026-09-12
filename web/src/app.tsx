import { useCallback, useEffect, useRef, useState } from "preact/hooks";

import { api, friendlyError } from "./api";
import {
  ConclusionModal,
  ConversationTile,
  ExitCard,
  InputTile,
  ProposalBanner,
  ReasoningTile,
  TopicBar,
} from "./components";
import { useTileLayout, useVisualViewport } from "./layout";
import { ReviewDashboard } from "./review";
import { confirmBeep, installAudioUnlock, speak, stopSpeaking } from "./tts";
import type { Answer, RecordingStatus, RoundState, Topic } from "./types";

/** Read the persisted audio preference (default on). */
function initialAudio(): boolean {
  try {
    return localStorage.getItem("audio") !== "0";
  } catch {
    return true;
  }
}

type Theme = "dark" | "light";
type View = "live" | "review";

/** Read the theme the pre-paint script in index.html already applied. */
function initialTheme(): Theme {
  return document.documentElement.getAttribute("data-theme") === "light"
    ? "light"
    : "dark";
}

/**
 * Caregiver cockpit — wired to the FastAPI backend (`python -m my20q.api`).
 * Every round-mutating call returns the full round state, so this
 * component just mirrors whatever the server reports.
 */
export function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [view, setView] = useState<View>("live");
  const [recording, setRecording] = useState<RecordingStatus | null>(null);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [topicId, setTopicId] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [round, setRound] = useState<RoundState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<string | null>(null);
  const [sseStatus, setSseStatus] = useState<"connecting" | "open" | "closed">(
    "closed",
  );
  const [emotion, setEmotion] = useState<Record<string, number>>({});
  const [audioOn, setAudioOn] = useState<boolean>(initialAudio);
  const [ttsAvailable, setTtsAvailable] = useState(false);
  // ✓ was pressed: show the explicit confirmation modal until "New round".
  const [concluded, setConcluded] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [currentModel, setCurrentModel] = useState<string | null>(null);
  const [canSelectModel, setCanSelectModel] = useState(false);
  // Quit was pressed: the server is going away, so the cockpit is replaced by
  // the "safe to exit" screen. null = still running.
  const [exited, setExited] = useState<{
    stopping: boolean;
    error: string | null;
  } | null>(null);
  const lastSpokenRef = useRef<string>("");
  // The cockpit is used on an iPad: size the shell to the VISUAL viewport (the
  // keyboard and the Safari toolbars both move it) and let the two splitters
  // resize the tiles.
  useVisualViewport();
  const layout = useTileLayout();

  // Bootstrap: load topics, create a session, open the first round.
  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    (async () => {
      try {
        const loaded = await api.topics();
        const sid = (await api.createSession()).session_id;
        const start =
          loaded.find((t) => t.id === "my_people") ??
          loaded.find((t) => !t.emergency) ??
          loaded[0];
        const rs = await api.startRound(sid, start.id);
        const rec = await api.recording();
        if (cancelled) return;
        setTopics(loaded);
        setSessionId(sid);
        setTopicId(start.id);
        setRound(rs);
        setRecording(rec);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(friendlyError(e));
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // SSE progress channel — live reasoner phase while a request is in flight.
  // The server immediately emits a {phase:"connected"} payload, so we flip
  // to "open" on the first message (more reliable across browsers than
  // EventSource.onopen alone, which doesn't fire until headers arrive).
  useEffect(() => {
    if (!sessionId || !round || exited) return;
    setSseStatus("connecting");
    const es = new EventSource(api.eventsUrl(sessionId, round.round_id));
    es.onopen = () => setSseStatus("open");
    es.onmessage = (e) => {
      setSseStatus("open");
      try {
        const data = JSON.parse(e.data);
        if (typeof data.phase === "string") setPhase(data.phase);
      } catch {
        /* ignore malformed SSE payloads */
      }
    };
    es.onerror = () => setSseStatus("closed");
    return () => {
      es.close();
      setSseStatus("closed");
    };
  }, [sessionId, round?.round_id, exited]);

  // Is local (piper) speech available? Drives the audio toggle's tooltip.
  useEffect(() => {
    api
      .ttsStatus()
      .then((s) => setTtsAvailable(s.available))
      .catch(() => setTtsAvailable(false));
  }, []);

  // Unlock browser audio on the first user gesture so the first query readouts
  // aren't dropped by the autoplay policy.
  useEffect(() => {
    installAudioUnlock();
  }, []);

  // Local models available for human-trial selection (Ollama only).
  useEffect(() => {
    api
      .models()
      .then((s) => {
        setModels(s.models);
        setCurrentModel(s.current);
        setCanSelectModel(s.can_select);
      })
      .catch(() => setCanSelectModel(false));
  }, []);

  // Switch the active model — takes effect on the next question (the backend
  // mutates the shared Ollama backend, which the reasoner reads per call).
  const selectModel = (model: string) => {
    setCurrentModel(model); // optimistic
    api
      .selectModel(model)
      .then((s) => {
        setModels(s.models);
        setCurrentModel(s.current);
      })
      .catch((e) => setError(friendlyError(e)));
  };

  // Speak each new query / proposed / confirmed utterance aloud (live mode
  // only, when audio is on and piper is available). The ref guards against
  // re-speaking the same text on unrelated re-renders.
  useEffect(() => {
    if (view !== "live" || !audioOn || !ttsAvailable) return;
    const ev = round?.event;
    if (!ev || !ev.text) return;
    if (!["query", "synthesis", "synthesized"].includes(ev.kind)) return;
    // A query is read with its distilled lead-in ("Okay, not food then — …")
    // so the readouts vary instead of firing bare questions back to back.
    const spoken =
      ev.kind === "query" && ev.preface ? `${ev.preface} ${ev.text}` : ev.text;
    if (spoken === lastSpokenRef.current) return;
    lastSpokenRef.current = spoken;
    speak(spoken);
  }, [round?.event.text, round?.event.preface, round?.event.kind, audioOn, ttsAvailable, view]);

  // Stop any readout when leaving live mode.
  useEffect(() => {
    if (view !== "live") stopSpeaking();
  }, [view]);

  const run = useCallback(async (fn: () => Promise<RoundState>) => {
    setBusy(true);
    setError(null);
    setPhase(null);
    try {
      setRound(await fn());
      // The dataset may have grown — refresh the recording monitor.
      api.recording().then(setRecording).catch(() => undefined);
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  }, []);

  // Queued caregiver edit, held while a question is being generated (CB-1).
  //
  // Typing must NEVER be blocked — a question takes 10-25s to generate, and
  // disabling the field for that whole window threw away the keystrokes. But
  // the request itself still has to wait: `add_context` and `replace` both
  // mutate history and then call `_advance()`, so dispatching one mid-flight
  // would put two `_advance()` calls on the same round. So capture is free and
  // DISPATCH is serialised — which is the guard the old code was really for.
  //
  // Last write wins: a second edit queued before the first drains replaces it,
  // rather than building a backlog the caregiver can no longer see or cancel.
  const queued = useRef<(() => Promise<RoundState>) | null>(null);
  const [queuedNote, setQueuedNote] = useState<string | null>(null);
  // `busy` read through a ref so `enqueue` need not be re-created on every flip
  // of it — a stale closure here would drop the edit silently.
  const busyRef = useRef(busy);

  const enqueue = useCallback(
    (fn: () => Promise<RoundState>, note: string) => {
      if (busyRef.current) {
        queued.current = fn;
        setQueuedNote(note);
        return;
      }
      run(fn);
    },
    [run],
  );

  useEffect(() => {
    busyRef.current = busy;
    if (!busy && queued.current) {
      const fn = queued.current;
      queued.current = null;
      setQueuedNote(null);
      run(fn);
    }
  }, [busy, run]);

  const terminal = round?.outcome != null;
  const canAnswer =
    !!round &&
    !busy &&
    !terminal &&
    (round.event.kind === "query" || round.event.kind === "synthesis");
  const canUndo = !!round && !busy && round.history.length > 0;
  // The opposition button works on a pending QUERY only — a proposed
  // utterance is confirmed/rejected, not flipped.
  const canFlip = !!round && !busy && !terminal && round.event.kind === "query";
  // Repeat re-speaks the pending question — only meaningful when there's a
  // query on screen and a voice to read it.
  const canRepeat =
    !!round &&
    !terminal &&
    round.event.kind === "query" &&
    audioOn &&
    ttsAvailable;

  const answer = (a: Answer) => {
    if (sessionId && round && canAnswer) {
      if (audioOn) confirmBeep(); // instant audible acknowledgement of input
      run(() => api.answer(sessionId, round.round_id, a));
    }
  };
  const undo = () => {
    if (sessionId && round && canUndo) {
      run(() => api.undo(sessionId, round.round_id));
    }
  };
  // Flip the pending question to its opposite connotation — a fast "try
  // again the other way around" trigger, not an answer.
  const flip = () => {
    if (sessionId && round && canFlip) {
      run(() => api.flip(sessionId, round.round_id));
    }
  };
  // Re-speak the current question aloud — same lead-in the auto-readout uses,
  // so a repeat sounds like the original. No server round-trip.
  const repeat = () => {
    const ev = round?.event;
    if (!ev || !canRepeat || !ev.text) return;
    const spoken = ev.preface ? `${ev.preface} ${ev.text}` : ev.text;
    lastSpokenRef.current = spoken;
    speak(spoken);
  };
  // Banner controls: Speak reads the draft (alternates spoken as "or", the
  // ellipsis dropped); ✓ concludes and raises the confirmation modal (the
  // synthesized event is auto-spoken by the readout effect); ⟳ restates the
  // same draft in different words; clicking a segment opens the editor and
  // replace applies its precise (category, old → new) edit.
  const speakDraft = () => {
    const b = round?.banner;
    if (b && b.state === "draft" && b.text && audioOn) {
      speak(b.text.replace(/\//g, " or ").replace(/…/g, "").trim());
    }
  };
  const acceptDraft = () => {
    if (sessionId && round && !busy && !terminal) {
      run(() => api.accept(sessionId, round.round_id)).then(() =>
        setConcluded(true),
      );
    }
  };
  const restateDraft = () => {
    if (sessionId && round && !terminal) {
      enqueue(() => api.restate(sessionId, round.round_id), "restate queued");
    }
  };
  const replaceDraft = (category: string, oldValue: string, newValue: string) => {
    if (sessionId && round && !terminal) {
      enqueue(
        () => api.replace(sessionId, round.round_id, category, oldValue, newValue),
        newValue
          ? `edit queued: ${category} → “${newValue}”`
          : `edit queued: remove ${category}`,
      );
    }
  };
  const retry = () => {
    if (sessionId && round && !busy && !terminal) {
      run(() => api.retry(sessionId, round.round_id));
    }
  };
  const newRound = () => {
    if (sessionId && topicId && !busy) {
      setConcluded(false);
      run(() => api.startRound(sessionId, topicId));
    }
  };
  // Quit for real: the server finalizes and records the live round, then stops
  // itself. Everything after this call is best-effort — the process may die
  // before the response is flushed — so a failed fetch still shows the exit
  // screen, just without the claim that the round was saved.
  const quit = () => {
    stopSpeaking();
    api
      .shutdown()
      .then((r) => setExited({ stopping: r.stopping, error: null }))
      .catch((e) => setExited({ stopping: true, error: friendlyError(e) }));
  };
  const changeTopic = (id: string) => {
    setTopicId(id);
    if (sessionId && !busy) run(() => api.startRound(sessionId, id));
  };
  const sendContext = (text: string) => {
    if (sessionId && round && !terminal) {
      enqueue(
        () => api.addContext(sessionId, round.round_id, text),
        `context queued: “${text}”`,
      );
    }
  };
  const togglePause = () => {
    if (!recording?.enabled || busy) return;
    api
      .setPaused(!recording.paused)
      .then(setRecording)
      .catch((e) => setError(friendlyError(e)));
  };
  const setEmotionValue = (id: string, value: number) => {
    setEmotion((prev) => {
      const next = { ...prev, [id]: value };
      if (sessionId) api.setEmotion(sessionId, next).catch(() => undefined);
      return next;
    });
  };
  const resetEmotion = () => {
    setEmotion({});
    if (sessionId) api.setEmotion(sessionId, {}).catch(() => undefined);
  };
  const exportConversation = () => {
    if (!sessionId) return;
    // Default to JSONL — the same record format the recorder writes, so
    // exports and recordings form one training corpus. The server's
    // Content-Disposition names the file.
    const a = document.createElement("a");
    a.href = api.exportUrl(sessionId, "jsonl");
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const toggleAudio = () => {
    setAudioOn((on) => {
      const next = !on;
      try {
        localStorage.setItem("audio", next ? "1" : "0");
      } catch {
        /* localStorage unavailable — preference applies for this session */
      }
      if (!next) stopSpeaking();
      return next;
    });
  };

  const toggleTheme = () => {
    setTheme((t) => {
      const next: Theme = t === "dark" ? "light" : "dark";
      const root = document.documentElement;
      if (next === "light") root.setAttribute("data-theme", "light");
      else root.removeAttribute("data-theme");
      try {
        localStorage.setItem("theme", next);
      } catch {
        /* localStorage unavailable — theme still applies for this session */
      }
      return next;
    });
  };

  // y/n/k/s answer shortcuts, u = undo, o = opposite (flip), r = repeat,
  // q = new round.
  // The handlers are re-bound each render so they close over current state.
  //
  // While a text-entry surface is focused (the caregiver context field, the
  // topic dropdown, the emotion sliders, or any contenteditable) the shortcuts
  // are DISABLED — the keyboard belongs to that control. Otherwise the first
  // letter of any context beginning with y/n/k/s/u/q would be swallowed as an
  // answer and never reach the field. The context field blurs itself on send,
  // so the shortcuts resume the moment context is submitted.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (view !== "live") return; // review mode has its own navigation
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      if (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        el?.isContentEditable
      ) {
        return;
      }
      const map: Record<string, Answer> = {
        y: "yes",
        n: "no",
        k: "kinda",
        s: "not_sure",
      };
      const key = e.key.toLowerCase();
      if (key in map) {
        e.preventDefault();
        answer(map[key]);
      } else if (key === "u") {
        e.preventDefault();
        undo();
      } else if (key === "o") {
        e.preventDefault();
        flip();
      } else if (key === "r") {
        e.preventDefault();
        repeat();
      } else if (key === "q") {
        e.preventDefault();
        newRound();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // The server is gone — nothing behind this screen would work.
  if (exited) {
    return <ExitCard stopping={exited.stopping} error={exited.error} />;
  }

  return (
    <div class="cockpit">
      <TopicBar
        topics={topics}
        topicId={topicId}
        onTopic={changeTopic}
        recording={recording}
        onTogglePause={togglePause}
        theme={theme}
        onToggleTheme={toggleTheme}
        engine={round?.engine ?? ""}
        busy={busy}
        onExport={exportConversation}
        canExport={!!sessionId}
        view={view}
        onView={setView}
        audioOn={audioOn}
        onToggleAudio={toggleAudio}
        ttsAvailable={ttsAvailable}
        models={models}
        currentModel={currentModel}
        canSelectModel={canSelectModel}
        onSelectModel={selectModel}
        onQuit={quit}
      />
      {error && <div class="errorbar">{error}</div>}
      {queuedNote && (
        <div class="queuedbar" aria-live="polite">
          {queuedNote} — applying when this question finishes
        </div>
      )}
      {concluded && round?.outcome === "synthesized" && round.final_utterance && (
        <ConclusionModal
          utterance={round.final_utterance}
          onNewRound={newRound}
        />
      )}
      {view === "review" ? (
        <main class="review-main">
          <ReviewDashboard audioOn={audioOn} ttsAvailable={ttsAvailable} />
        </main>
      ) : (
        <>
          {/* The living proposal — up top, before everything else, so the
              caregiver always sees what the round currently believes. */}
          <ProposalBanner
            banner={round?.banner ?? null}
            facets={round?.event.facets ?? []}
            busy={busy}
            terminal={!!terminal}
            onSpeak={speakDraft}
            onAccept={acceptDraft}
            onRestate={restateDraft}
            onReplace={replaceDraft}
          />
        <main class="grid" style={layout.style}>
          <ConversationTile round={round} busy={busy} phase={phase} onRetry={retry} />
          {/* The two splitters ARE the gutters — drag to resize, double-click
              to restore the stylesheet default (Evie's gesture). They are
              their own grid tracks, so the layout is unchanged until moved,
              and they are hidden in the phone stack. */}
          <div
            class="vsplit"
            role="separator"
            aria-orientation="vertical"
            title="Drag to resize · double-click to reset"
            onPointerDown={layout.vsplit.onPointerDown}
            onDblClick={layout.vsplit.onDblClick}
          >
            <span class="grip" />
          </div>
          <div
            class="hsplit"
            role="separator"
            aria-orientation="horizontal"
            title="Drag to resize · double-click to reset"
            onPointerDown={layout.hsplit.onPointerDown}
            onDblClick={layout.hsplit.onDblClick}
          >
            <span class="grip" />
          </div>
          {/* Pictogram tile shelved — the curated retrieval mostly fell back
              to "?" in real sessions. Component + backend retrieval are kept;
              re-mount once the image slot is driven by a generator (task). */}
          <ReasoningTile
            event={round?.event ?? null}
            busy={busy}
            phase={phase}
            sse={sseStatus}
            emotion={emotion}
            onEmotion={setEmotionValue}
            onResetEmotion={resetEmotion}
          />
          <InputTile
            canAnswer={canAnswer}
            canUndo={canUndo}
            canFlip={canFlip}
            canRepeat={canRepeat}
            terminal={!!terminal}
            busy={busy}
            onAnswer={answer}
            onUndo={undo}
            onFlip={flip}
            onRepeat={repeat}
            onSend={sendContext}
            onNewRound={newRound}
          />
        </main>
        </>
      )}
    </div>
  );
}
