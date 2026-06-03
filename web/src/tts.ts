// Cockpit audio. Voice readouts (queries + utterances) come from the
// backend's local piper TTS; the input-confirmation cue is a short
// WebAudio beep generated in-browser, so it works even before piper is
// installed. All audio is gated by the cockpit's mute toggle. There is no
// cloud-voice path — if /api/tts is unavailable, readouts stay silent.

let currentAudio: HTMLAudioElement | null = null;
let audioCtx: AudioContext | null = null;
// Resolver for the in-flight speak() promise, so stopSpeaking() (or starting
// a new readout) settles a pending caller rather than leaving it hanging.
let endResolve: (() => void) | null = null;

// Browsers block programmatic audio until the page has a real user gesture, so
// the first query readouts (which fire from a load effect, not a click) get
// dropped until enough interaction accrues. We unlock on the first gesture by
// playing a silent clip *inside* that gesture; after that, readouts fired from
// async effects are allowed. `blockedText` holds a readout that was dropped
// before the gesture, so we can replay it the moment audio unlocks.
const SILENT_WAV =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";
let audioPrimed = false;
let blockedText = "";

/** Install one-time listeners that unlock audio on the first user gesture.
 *  Call once on startup. Idempotent. */
export function installAudioUnlock(): void {
  const prime = () => {
    if (audioPrimed) return;
    audioPrimed = true;
    // Unlock HTMLAudio: a silent play *within* the gesture grants the page
    // permission to play audio programmatically afterwards.
    try {
      const a = new Audio(SILENT_WAV);
      a.volume = 0;
      void a.play().catch(() => undefined);
    } catch {
      /* ignore */
    }
    // Resume the WebAudio context used by the input beep, if it exists.
    try {
      void audioCtx?.resume();
    } catch {
      /* ignore */
    }
    window.removeEventListener("pointerdown", prime, true);
    window.removeEventListener("keydown", prime, true);
    window.removeEventListener("touchstart", prime, true);
    // Replay a readout that was dropped before the gesture.
    if (blockedText) {
      const t = blockedText;
      blockedText = "";
      void speak(t);
    }
  };
  window.addEventListener("pointerdown", prime, true);
  window.addEventListener("keydown", prime, true);
  window.addEventListener("touchstart", prime, true);
}

/** Stop any in-flight voice readout (e.g. on a new query or muting). */
export function stopSpeaking(): void {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  if (endResolve) {
    const r = endResolve;
    endResolve = null;
    r();
  }
}

/** Speak `text` via the backend piper voice. Silent if TTS is unavailable.
 *  The returned promise resolves when playback finishes (or is interrupted by
 *  a new readout / stopSpeaking), which lets callers chain readouts — e.g.
 *  the review auto-play advancing one step after each line is read. */
export async function speak(text: string): Promise<void> {
  const t = text.trim();
  if (!t) return;
  stopSpeaking();
  let resp: Response;
  try {
    resp = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: t }),
    });
  } catch {
    return; // server/network unavailable — stay silent
  }
  if (!resp.ok) return; // 503: piper not installed — stay silent
  const url = URL.createObjectURL(await resp.blob());
  const audio = new Audio(url);
  currentAudio = audio;
  return new Promise<void>((resolve) => {
    endResolve = resolve;
    const done = () => {
      URL.revokeObjectURL(url);
      if (currentAudio === audio) currentAudio = null;
      if (endResolve === resolve) endResolve = null;
      resolve();
    };
    audio.onended = done;
    audio.onerror = done;
    audio
      .play()
      .then(() => {
        blockedText = ""; // a readout started — clear any earlier blocked one
      })
      .catch(() => {
        // Autoplay blocked (no user gesture yet) — remember to replay on unlock.
        blockedText = t;
        done();
      });
  });
}

/** A short confirmation beep for caregiver input (local, no backend). */
export function confirmBeep(): void {
  try {
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    audioCtx = audioCtx ?? new Ctor();
    const ctx = audioCtx;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 660;
    gain.gain.setValueAtTime(0.06, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.12);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.12);
  } catch {
    /* no audio context available — ignore */
  }
}
