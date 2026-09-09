// Cockpit audio. Voice readouts (queries + utterances) come from the
// backend's local piper TTS; the input-confirmation cue is a short
// WebAudio beep generated in-browser, so it works even before piper is
// installed. All audio is gated by the cockpit's mute toggle. There is no
// cloud-voice path — if /api/tts is unavailable, readouts stay silent.
//
// AUTOPLAY, and why this uses ONE audio element
// ---------------------------------------------
// Browsers block programmatic playback until the page has had a real user
// gesture. The rules differ in a way that matters here:
//
//   * Desktop Chrome grants activation to the DOCUMENT. Once the caregiver has
//     clicked anything, later `play()` calls from async effects are allowed,
//     including on freshly-constructed elements.
//   * iOS Safari grants it to the ELEMENT. A newly-constructed `Audio` has no
//     permission no matter how much the caregiver has interacted, so building
//     one per utterance means every readout after the unlock is rejected —
//     silently, since a rejected `play()` throws into a promise nobody watches.
//
// The cockpit is used on an iPad, and that second case is exactly the reported
// symptom: the question is never read aloud and the caregiver hits Repeat on
// every single turn. So there is exactly ONE element here, primed inside the
// first gesture and reused for every readout thereafter — only its `src`
// changes. The unlock listeners also stay armed for the life of the page: a
// rejection can happen later (a new tab, a revoked permission), and dropping
// the readout with no way back is what made this invisible for so long.

let audioCtx: AudioContext | null = null;
// Resolver for the in-flight speak() promise, so stopSpeaking() (or starting
// a new readout) settles a pending caller rather than leaving it hanging.
let endResolve: (() => void) | null = null;

const SILENT_WAV =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";

/** The single reusable element. iOS ties playback permission to the element,
 *  so constructing a new one per readout would forfeit it every time. */
let el: HTMLAudioElement | null = null;
let primed = false;
let speaking = false;
/** A readout dropped before audio was unlocked, replayed on the next gesture. */
let blockedText = "";
/** The object URL currently loaded, revoked when the next one replaces it. */
let currentUrl = "";

function element(): HTMLAudioElement {
  if (!el) {
    el = new Audio();
    el.preload = "auto";
  }
  return el;
}

/** True when the last readout was refused by the autoplay policy. The cockpit
 *  can surface this rather than letting the failure stay invisible. */
export function audioBlocked(): boolean {
  return blockedText !== "";
}

/** Install listeners that unlock audio on a user gesture.
 *  Call once on startup. Idempotent, and deliberately never removed. */
export function installAudioUnlock(): void {
  const prime = () => {
    // Never disturb a readout in progress — this fires on every gesture,
    // including the answer button pressed while a question is being read.
    if (speaking) return;
    const replayBlocked = () => {
      if (!blockedText) return;
      const t = blockedText;
      blockedText = "";
      void speak(t);
    };
    if (primed) {
      replayBlocked();
    } else {
      const a = element();
      try {
        // The clip is silent by CONTENT, so it is played at normal volume and
        // unmuted on purpose. Muted playback is permitted on iOS without a
        // gesture, so unlocking with `muted = true` would prove nothing and
        // leave real readouts still blocked; `volume = 0` is worse still,
        // since volume is read-only on iOS and the clip would simply play.
        a.src = SILENT_WAV;
        void a
          .play()
          .then(() => {
            a.pause();
            primed = true;
            // Only now — replaying earlier would swap `src` out from under
            // the in-flight unlock and abort it.
            replayBlocked();
          })
          .catch(() => {
            /* not unlocked yet — the next gesture tries again */
          });
      } catch {
        /* ignore */
      }
    }
    // Resume the WebAudio context used by the input beep, if it exists.
    try {
      void audioCtx?.resume();
    } catch {
      /* ignore */
    }
  };
  window.addEventListener("pointerdown", prime, true);
  window.addEventListener("keydown", prime, true);
  window.addEventListener("touchstart", prime, true);
}

/** Stop any in-flight voice readout (e.g. on a new query or muting). */
export function stopSpeaking(): void {
  if (el && !el.paused) el.pause();
  speaking = false;
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
  const audio = element();
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentUrl = url;
  audio.src = url;

  return new Promise<void>((resolve) => {
    endResolve = resolve;
    const done = () => {
      if (endResolve === resolve) endResolve = null;
      speaking = false;
      resolve();
    };
    audio.onended = done;
    audio.onerror = done;
    speaking = true;
    audio
      .play()
      .then(() => {
        blockedText = ""; // a readout started — clear any earlier blocked one
      })
      .catch(() => {
        // Refused by the autoplay policy. Remember it so the next gesture
        // replays it, and drop `primed` so that gesture re-unlocks the element
        // — on iOS the permission can lapse, and a one-shot unlock left every
        // later readout silently discarded.
        primed = false;
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
