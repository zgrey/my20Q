# Testing the Kokoro voice (local TTS)

my20Q ships two **local-only** text-to-speech engines (patient audio never leaves
the host — same privacy stance as the LLM):

- **piper** — fast, but flat/robotic (default; voice `en_US-amy-medium`).
- **kokoro** — the open-weight [Kokoro-82M](https://github.com/thewh1teagle/kokoro-onnx)
  model via ONNX Runtime: warmer, more natural prosody, near real-time on CPU,
  Apache-2.0. Tiny footprint (~doesn't compete with the LLM for VRAM).

This file is just the test/setup notes for trying Kokoro. The engine code lives
in `src/my20q/tts/kokoro_tts.py`.

## 1. Install the package

On the host that runs the API (e.g. cerberus), into the shared venv:

```bash
pip install -e '.[kokoro]'     # pulls kokoro-onnx + onnxruntime + numpy
```

## 2. Download the model + voices (one time)

Kokoro needs two files from the kokoro-onnx releases:

- `kokoro-v1.0.onnx`  (~310 MB, the model)
- `voices-v1.0.bin`   (the voice pack)

```bash
mkdir -p ~/kokoro && cd ~/kokoro
curl -L -o kokoro-v1.0.onnx \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o voices-v1.0.bin \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

> Check the kokoro-onnx releases page for the current file URLs if these move.

## 3. Point my20Q at it

```bash
export MY20Q_TTS_ENGINE=kokoro
export MY20Q_KOKORO_MODEL=/c/Users/grey_/kokoro/kokoro-v1.0.onnx
export MY20Q_KOKORO_VOICES=/c/Users/grey_/kokoro/voices-v1.0.bin
export MY20Q_KOKORO_VOICE=af_heart      # optional; see voices below
```

The `serve_cerberus.sh` script already forwards these into its tmux session, so
exporting them before `./scripts/serve_cerberus.sh` is enough — no edits needed.

## 4. Verify

```bash
curl -s http://127.0.0.1:8000/api/tts/status        # -> {"available": true, "voice": "af_heart", ...}
curl -s -X POST http://127.0.0.1:8000/api/tts \
  -H 'content-type: application/json' \
  -d '{"text":"Hello, this is the Kokoro voice."}' --output sample.wav
```

If `available` is `false`, the `reason` field says why (package missing, model
not found, etc.). A missing model degrades gracefully — the cockpit just stays
silent; it never falls back to a cloud voice.

## Voices

Voice codes are `<accent><gender>_<name>`: first letter `a` = American,
`b` = British; second `f` = female, `m` = male. Good starting points:

| Code | Notes |
|------|-------|
| `af_heart`   | warm American female (good default) |
| `af_bella`   | American female, expressive |
| `am_michael` | American male, steady |
| `bf_emma`    | British female |

Set with `MY20Q_KOKORO_VOICE`. `MY20Q_KOKORO_SPEED` (default `1.0`) and
`MY20Q_KOKORO_LANG` (default `en-us`) are also available.

## Switching back to piper

Unset `MY20Q_TTS_ENGINE` (or set it to `piper`) and restart. Both engines are
always present; only the selected one is used.

## Config reference

| Env var | Default | Meaning |
|---------|---------|---------|
| `MY20Q_TTS_ENGINE`   | `piper`   | `piper` or `kokoro` |
| `MY20Q_KOKORO_MODEL` | —         | path to `kokoro-v1.0.onnx` |
| `MY20Q_KOKORO_VOICES`| —         | path to `voices-v1.0.bin` |
| `MY20Q_KOKORO_VOICE` | `af_heart`| voice code |
| `MY20Q_KOKORO_SPEED` | `1.0`     | speaking rate |
| `MY20Q_KOKORO_LANG`  | `en-us`   | language |
