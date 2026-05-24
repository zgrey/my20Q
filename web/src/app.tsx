import { useCallback, useEffect, useState } from "preact/hooks";

import { api, friendlyError } from "./api";
import {
  ConversationTile,
  InputTile,
  PictogramTile,
  ReasoningTile,
  TopicBar,
} from "./components";
import type { Answer, RecordingStatus, RoundState, Topic } from "./types";

type Theme = "dark" | "light";

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
    if (!sessionId || !round) return;
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
  }, [sessionId, round?.round_id]);

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

  const terminal = round?.outcome != null;
  const canAnswer =
    !!round &&
    !busy &&
    !terminal &&
    (round.event.kind === "query" || round.event.kind === "synthesis");
  const canUndo = !!round && !busy && round.history.length > 0;

  const answer = (a: Answer) => {
    if (sessionId && round && canAnswer) {
      run(() => api.answer(sessionId, round.round_id, a));
    }
  };
  const undo = () => {
    if (sessionId && round && canUndo) {
      run(() => api.undo(sessionId, round.round_id));
    }
  };
  const newRound = () => {
    if (sessionId && topicId && !busy) {
      run(() => api.startRound(sessionId, topicId));
    }
  };
  const changeTopic = (id: string) => {
    setTopicId(id);
    if (sessionId && !busy) run(() => api.startRound(sessionId, id));
  };
  const sendContext = (text: string) => {
    if (sessionId && round && !busy) {
      run(() => api.addContext(sessionId, round.round_id, text));
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
    // Trigger a download; the server's Content-Disposition names the file.
    const a = document.createElement("a");
    a.href = api.exportUrl(sessionId, "md");
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
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

  // y/n/k/s answer shortcuts, u = undo, q = new round. The handlers are
  // re-bound each render so they close over current state.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const map: Record<string, Answer> = {
        y: "yes",
        n: "no",
        k: "kinda",
        s: "not_sure",
      };
      const key = e.key.toLowerCase();
      if (key in map) answer(map[key]);
      else if (key === "u") undo();
      else if (key === "q") newRound();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

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
      />
      {error && <div class="errorbar">{error}</div>}
      <main class="grid">
        <ConversationTile round={round} busy={busy} />
        <PictogramTile event={round?.event ?? null} />
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
          terminal={!!terminal}
          busy={busy}
          onAnswer={answer}
          onUndo={undo}
          onSend={sendContext}
          onNewRound={newRound}
        />
      </main>
    </div>
  );
}
