# Handoff prompt for the new dictate-local chat

**Copy everything below the line into a new Claude Code session opened in `C:\Users\tejas\Downloads\Code Projects\dictate-local`.**

*(The project moved here from `TJ Career Center/Projects/` on 31 August 2026. Shortcuts and the Startup entry were repointed; if anything still references the old path, re-run `python install.py`.)*

---

I am building a permanently local, unmetered speech-to-text tool to replace Wispr Flow. Read `ARCHITECTURE.md` in this folder first — it holds the design, what already works, what was measured, and what was learned from inspecting the Wispr Flow installation.

## What exists and works

**v1 is finished and in daily use.** It is a background tray application: no console, single instance, starts from the Start Menu, and types into any window. Confirmed working on Tejas's real voice across several sessions on 31 August 2026 — including the messages that drove most of these fixes. Run `Run-Tests.cmd` before changing anything, so you know the baseline works on this machine. It is fully offline — the corpus is generated with the local Windows SAPI voices.

| File | What it is |
|---|---|
| `dictate.py` | Microphone, hotkey, keyboard. The only file that touches hardware |
| `dictate_core.py` | CUDA setup, device choice, vocabulary, corrections, command grammar |
| `dictate_stream.py` | Streaming with VAD |
| `dictate_overlay.py` | The always-on-top pill. Read its docstring before touching it |
| `dictate_polish.py` | Filler/stutter cleanup, and the optional local-LLM pass |
| `dictate_config.py` | settings.json, and the start-with-Windows shortcut |
| `dictate_settings.py` | The settings window. NOT a popup menu - that hangs the process |
| `vocabulary.txt` | **Your terms. Edit this one** |
| `dictate_tray.py` | Tray icon. The app's real home |
| `install.py` | Start Menu / Desktop shortcuts and the icon |
| `tests/` | **Eleven suites, all offline** |

**One launcher: `Dictate.cmd`.** Settings are in the app - right-click the pill. `Dictate-Everywhere.cmd` is the same app started elevated, so it reaches administrator windows too. `Run-Tests.cmd` runs the suite; `install.py` puts it in the Start Menu; `mine_vocabulary.py` reads an Obsidian vault for terms.

**What it does now:** F9 toggle; `small.en` on CUDA int8_float16 with automatic CPU fallback; custom vocabulary via `initial_prompt` plus near-miss snapping; spoken punctuation (`full stop`, `new line`, `new paragraph`) and commands (`scratch that`, `cap that`, `literally <word>`); optional streaming; appends everything to `transcript.log`.

## Measured on this machine, not assumed

| | |
|---|---|
| Vocabulary term recall | **50% → 100%**, control WER unchanged at 0.0% |
| Commands | 8 phrasings fire, 6 ambiguous ones stay quiet |
| GPU | **11–16× realtime**, 433 MB VRAM, 2.5 s warm start |
| CPU fallback | 2.3–2.7× realtime |
| Streaming | First text at **31%** of the utterance; ends 0.4 s after the last word |
| Pill | Never becomes the foreground window, verified across repeated runs |
| Fast cleanup | 1.5 ms; 12 look-alike-but-real sentences untouched |
| LLM cleanup | 2.1 s resident (`qwen3.5:4b`); 263 tok/s prompt, 15 tok/s generation |
| Pause timer | **0.7 s.** 0.4 s was tuned on a synthetic voice and cut real sentences in half |
| Real speaking rate | **132-146 wpm median, peaks 150-153** - measured from his own voice, matching the documented 120/147 |

## Hard facts about this machine

| | |
|---|---|
| GPU | RTX 3050 Laptop, **4 GB VRAM**, shared with a local Ollama model |
| Python | **3.14** — `faster-whisper`, `sounddevice` and `keyboard` all install fine |
| My speaking rate | **120 WPM average, 147 WPM peak, measured over 16,989 words** |
| Ollama | `qwen3.5:4b` may already hold VRAM. Whichever loads first wins |

**The speaking rate is a design constraint, not trivia.** `small.en` sustains it. `base.en` starts dropping words. **The fallback therefore moves to CPU rather than shrinking the model** — it trades speed for accuracy, never the reverse. Do not change that without testing against real speech at that rate.

**Two traps already paid for, do not undo either.**

**One:** the pill takes the foreground at `update_idletasks()`, before any window style exists — `WS_EX_NOACTIVATE` alone does not prevent it and `withdraw()` does not either. The fix is to record the foreground window before creating anything and hand it back with `SetForegroundWindow`. Remove that and dictation types into the pill instead of my document.

**Two:** `cublas64_12.dll` was missing on this machine, and a CUDA failure does *not* surface when `WhisperModel(...)` is constructed — only later inside `transcribe()`. So `dictate.py` pushes the nvidia wheel directory onto `PATH` *before* importing `faster_whisper`, and `Transcriber` does a warmup transcribe at startup. Removing either brings back a crash that a `try/except` around the constructor cannot catch.

## What is worth doing next

1. **Use it for a real day's work and add what breaks to `vocabulary.txt`.** Every measured number above is from synthetic speech at a calibrated 141 WPM. It is a regression signal, not proof of accuracy on my voice. The 15 terms currently in the list were guessed from `ARCHITECTURE.md`, not mined from real usage.
2. **The settings window still looks bland.** This is the one open item he named at handoff. It uses sv-ttk (Sun Valley / Windows 11 widgets), which fixed the worst of it, but the layout itself is plain. Everything else he asked for is done.
3. **Decide whether streaming becomes the default.** It needs a week of real use first.
4. **Possibly mine `flow.sqlite` for term frequency** — see the rules below.

## Rules

- **Never read, export or process `%APPDATA%\Wispr Flow\flow.sqlite`** unless I explicitly ask. It is 495 MB of everything I have ever dictated. If I do ask, it is for term-frequency mining to improve the vocabulary prompt, and the extracted words stay on this machine.
- **Do not decompile the Wispr Flow application binaries.** Their config is fair game because it is my data. Their code is not. (A copy of the app folder sits in `WisprFlow/` here; leave it alone.)
- Nothing in this tool may make a network call after the one-time model download.
- Test before claiming something works. This machine has surprised us before: a 262K context window that Ollama actually loaded at 4096, prompt processing at roughly 1.4 tokens per second, and a GPU path that was documented as verified but had never once run.

## The goal in one line

**Text appears where my cursor is, fast enough that I stop noticing the tool, forever, with no quota.**
