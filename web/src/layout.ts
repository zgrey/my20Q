// Cockpit shell behaviour: viewport, full screen, tile sizing, and the
// persisted show/hide toggles. Split out of app.tsx because none of it is
// about the dialogue — it is about surviving a real device, mostly iPad
// Safari, where the cockpit is actually used.

import { useCallback, useEffect, useRef, useState } from "preact/hooks";

// ------------------------------------------------------------- preferences

const PREFIX = "my20q.";

function readPref<T>(key: string, parse: (raw: string) => T | null): T | null {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw === null ? null : parse(raw);
  } catch {
    return null; // private browsing / storage disabled
  }
}

function writePref(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, value);
  } catch {
    /* storage unavailable — the preference still applies for this session */
  }
}

/** A remembered on/off switch — the show/hide toggles on the tile sections. */
export function useRemembered(
  key: string,
  initial: boolean,
): [boolean, () => void] {
  const [on, setOn] = useState<boolean>(
    () => readPref(key, (raw) => raw === "1") ?? initial,
  );
  const toggle = useCallback(() => {
    setOn((prev) => {
      writePref(key, prev ? "0" : "1");
      return !prev;
    });
  }, [key]);
  return [on, toggle];
}

// ---------------------------------------------------------- visual viewport

/**
 * Pin the cockpit shell to the VISUAL viewport.
 *
 * The defect (owner, iPad Safari): the caregiver context field at the bottom
 * of the input tile gets covered and cannot be scrolled back into view. Two
 * separate iOS behaviours cause it, and `height: 100vh` + `overflow: hidden`
 * makes both unrecoverable:
 *
 *  - `100vh` is the LARGE viewport — the height the page would have if the
 *    browser chrome were hidden. With the toolbar showing, the bottom of the
 *    layout is simply below the window, and a non-scrolling body cannot reach
 *    it.
 *  - The on-screen keyboard shrinks the visual viewport but NOT the layout
 *    viewport, and iOS then scrolls the layout viewport to reveal the focused
 *    field — sliding the whole fixed-height shell up by `offsetTop`.
 *
 * `100dvh` in the stylesheet fixes the first on modern browsers. Measuring
 * `visualViewport` here fixes both, including the keyboard, whose height no
 * CSS unit reports. Full screen is the owner's other request and helps, but
 * it is a workaround for this — not the fix.
 */
export function useVisualViewport(): void {
  useEffect(() => {
    const vv = window.visualViewport;
    const root = document.documentElement;
    const apply = () => {
      const h = Math.round(vv ? vv.height : window.innerHeight);
      root.style.setProperty("--app-h", `${h}px`);
      root.style.setProperty("--app-top", `${Math.round(vv?.offsetTop ?? 0)}px`);
    };
    apply();
    vv?.addEventListener("resize", apply);
    vv?.addEventListener("scroll", apply);
    window.addEventListener("resize", apply);
    window.addEventListener("orientationchange", apply);
    return () => {
      vv?.removeEventListener("resize", apply);
      vv?.removeEventListener("scroll", apply);
      window.removeEventListener("resize", apply);
      window.removeEventListener("orientationchange", apply);
    };
  }, []);
}

// -------------------------------------------------------------- full screen

interface FullscreenDocument extends Document {
  webkitFullscreenEnabled?: boolean;
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
}

interface FullscreenElement extends HTMLElement {
  webkitRequestFullscreen?: () => Promise<void> | void;
}

/** Swallow the rejection a denied fullscreen request produces. */
function ignore(result: Promise<void> | void): void {
  void Promise.resolve(result).catch(() => undefined);
}

/**
 * Full screen, and the way back out — one control, two directions.
 *
 * iPad Safari implements the webkit-prefixed API only; iPhone Safari
 * implements neither (video elements only). `supported` is false there, and
 * the button hides itself rather than offering a control that does nothing.
 */
export function useFullscreen(): {
  supported: boolean;
  active: boolean;
  toggle: () => void;
} {
  const doc = document as FullscreenDocument;
  const [active, setActive] = useState(false);
  const supported = !!(doc.fullscreenEnabled || doc.webkitFullscreenEnabled);

  useEffect(() => {
    const sync = () =>
      setActive(!!(doc.fullscreenElement || doc.webkitFullscreenElement));
    sync();
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
    return () => {
      document.removeEventListener("fullscreenchange", sync);
      document.removeEventListener("webkitfullscreenchange", sync);
    };
  }, [doc]);

  const toggle = useCallback(() => {
    const el = document.documentElement as FullscreenElement;
    if (doc.fullscreenElement || doc.webkitFullscreenElement) {
      ignore(doc.exitFullscreen ? doc.exitFullscreen() : doc.webkitExitFullscreen?.());
    } else {
      ignore(
        el.requestFullscreen ? el.requestFullscreen() : el.webkitRequestFullscreen?.(),
      );
    }
  }, [doc]);

  return { supported, active, toggle };
}

// ------------------------------------------------------------- tile sizing

/** 1.55fr + 1fr — the stylesheet default, held constant as the split moves. */
const COL_TOTAL = 2.55;
/** Neither column may be dragged narrower than this (px). */
const MIN_COL = 260;
/** The input tile keeps its answer buttons (px). */
const MIN_ROW = 120;

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), hi);
}

/**
 * Pointer drag with capture — mouse, touch and pencil through one path.
 *
 * Evie's splitters listen on `window` for mousemove/mouseup, which is
 * desktop-only; the cockpit is dragged on a tablet, so this captures the
 * pointer on the handle instead. The handle also needs `touch-action: none`
 * in CSS or iOS pans the page rather than reporting the move.
 */
function drag(
  e: PointerEvent,
  onMove: (dx: number, dy: number) => void,
  onEnd: () => void,
): void {
  const handle = e.currentTarget as HTMLElement | null;
  if (!handle) return;
  e.preventDefault();
  const x0 = e.clientX;
  const y0 = e.clientY;
  handle.setPointerCapture(e.pointerId);
  const move = (ev: PointerEvent) => onMove(ev.clientX - x0, ev.clientY - y0);
  const up = () => {
    handle.releasePointerCapture(e.pointerId);
    handle.removeEventListener("pointermove", move);
    handle.removeEventListener("pointerup", up);
    handle.removeEventListener("pointercancel", up);
    onEnd();
  };
  handle.addEventListener("pointermove", move);
  handle.addEventListener("pointerup", up);
  handle.addEventListener("pointercancel", up);
}

export interface SplitHandlers {
  onPointerDown: (e: PointerEvent) => void;
  onDblClick: () => void;
}

export interface TileLayout {
  /** Custom properties for the `.grid` element (a string — always typechecks). */
  style: string;
  vsplit: SplitHandlers;
  hsplit: SplitHandlers;
}

/**
 * Drag-to-resize for the two cockpit splitters, remembered across sessions.
 *
 * The grid is one 14px splitter track per axis rather than a plain `gap`, so
 * the draggable strip IS the gutter and the layout looks unchanged until it
 * is moved. Double-click restores the stylesheet default (Evie's gesture).
 *
 * The column split is stored as a FRACTION, not pixels: a px width taken on a
 * landscape iPad is wrong the moment the device is rotated, and this layout
 * is rotated constantly.
 */
export function useTileLayout(): TileLayout {
  const [cols, setCols] = useState<number | null>(() =>
    readPref("split.cols", (raw) => {
      const n = parseFloat(raw);
      return Number.isFinite(n) ? n : null;
    }),
  );
  const [rowPx, setRowPx] = useState<number | null>(() =>
    readPref("split.row", (raw) => {
      const n = parseInt(raw, 10);
      return Number.isFinite(n) ? n : null;
    }),
  );
  // Written on release, not on every pointermove — a drag is ~60 events a
  // second and localStorage is synchronous.
  const latest = useRef<{ cols: number | null; row: number | null }>({
    cols,
    row: rowPx,
  });
  latest.current = { cols, row: rowPx };

  const persist = useCallback(() => {
    const { cols: c, row: r } = latest.current;
    writePref("split.cols", c === null ? null : String(c));
    writePref("split.row", r === null ? null : String(r));
  }, []);

  const onColDown = useCallback(
    (e: PointerEvent) => {
      const grid = (e.currentTarget as HTMLElement).parentElement;
      const left = grid?.querySelector<HTMLElement>(".conversation");
      const right = grid?.querySelector<HTMLElement>(".reasoning");
      if (!left || !right) return;
      const lw = left.getBoundingClientRect().width;
      const total = lw + right.getBoundingClientRect().width;
      if (total < MIN_COL * 2) return; // too narrow to split meaningfully
      drag(
        e,
        (dx) => {
          const w = clamp(lw + dx, MIN_COL, total - MIN_COL);
          setCols(Math.round((w / total) * COL_TOTAL * 1000) / 1000);
        },
        persist,
      );
    },
    [persist],
  );

  const onRowDown = useCallback(
    (e: PointerEvent) => {
      const grid = (e.currentTarget as HTMLElement).parentElement;
      const input = grid?.querySelector<HTMLElement>(".input");
      if (!grid || !input) return;
      const h0 = input.getBoundingClientRect().height;
      const max = grid.getBoundingClientRect().height - MIN_ROW;
      drag(
        e,
        // Dragging UP grows the input tile, so the delta is subtracted.
        (_dx, dy) => setRowPx(Math.round(clamp(h0 - dy, MIN_ROW, Math.max(MIN_ROW, max)))),
        persist,
      );
    },
    [persist],
  );

  const resetCols = useCallback(() => {
    setCols(null);
    writePref("split.cols", null);
  }, []);
  const resetRow = useCallback(() => {
    setRowPx(null);
    writePref("split.row", null);
  }, []);

  const style =
    (cols === null
      ? ""
      : `--col-left:${cols}fr;--col-right:${Math.round((COL_TOTAL - cols) * 1000) / 1000}fr;`) +
    (rowPx === null ? "" : `--row-bottom:${rowPx}px;`);

  return {
    style,
    vsplit: { onPointerDown: onColDown, onDblClick: resetCols },
    hsplit: { onPointerDown: onRowDown, onDblClick: resetRow },
  };
}
