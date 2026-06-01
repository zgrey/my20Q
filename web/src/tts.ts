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
    audio.play().catch(done); // autoplay blocked until a gesture — that's fine
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
