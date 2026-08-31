# Dictate Local

**A permanently local, unmetered speech-to-text system for Windows.**

Text appears where your cursor is, fast enough that you stop noticing the tool, forever, with zero quota and zero audio leaving your machine.

---

## The Problem

Hosted speech-to-text tools (Wispr Flow, etc.) suffer from four structural issues:
1. **Hard monthly/daily usage quotas** that disrupt deep work.
2. **Audio streaming to cloud servers**, creating data privacy risks for client notes and sensitive documents.
3. **Zero custom vocabulary biasing**, consistently mishearing company names, foreign terminology, and proper nouns.
4. **Recurring subscription costs** for commodity inference.

## The Approach

Run local inference on your GPU with zero cloud roundtrip:

```
mic ──▶ sounddevice ──▶ float32 buffer ──▶ Silero VAD ──▶ faster-whisper (small.en CUDA)
                                                │                    │
                                    pause detected (0.7s)      initial_prompt biasing
                                                │                    │
                                                ▼                    ▼
                          corrections ──▶ near-miss snap ──▶ command grammar
                                                                      │
                                               keyboard.write() ──▶ focused window (0 clipboard clobber)
```

## Measured on Real Hardware (RTX 3050 Laptop, 4 GB VRAM)

Every metric below is verified through automated offline test suites on synthetic and real voice recordings:

| Metric | Measured Value | Note |
|---|---|---|
| **GPU Inference Speed** | **11–16× Realtime** | ~210ms per phrase on `small.en` int8_float16 |
| **GPU Memory Footprint** | **433 MB VRAM** | Leaves ample headroom for local LLMs |
| **CPU Fallback Speed** | **2.3–2.7× Realtime** | Automatic fallback without process crashes |
| **Vocabulary Term Recall** | **50% ➔ 100%** | 3-layer prompt + near-miss snapping |
| **Control WER Degradation** | **0.0%** | Zero false rewrite collisions on 89-word corpus |
| **Latency to First Word** | **~31% into phrase** | With streaming mode enabled |
| **Pill Window Focus** | **WS_EX_NOACTIVATE** | Never steals active keyboard focus |

---

## 3-Tier Accuracy Architecture

1. **`initial_prompt` Biasing**: Soft-biases Whisper beam search towards words in `vocabulary.txt` (e.g. *Naukri*, *ESCP*, *Zalando*, *PitchBook*, *GitHub*).
2. **Near-Miss Phonetic Snapping**: Uses Levenshtein distance with a strict 0.2 cutoff to snap spelling drifts (e.g. *Arbeitszugnis* ➔ *Arbeitszeugnis*) without mutating ordinary English.
3. **Explicit Correction Rules**: Regex and phonetic rule replacement for short tokens (e.g. *Deami* ➔ *DEAMIE*, *guitar pre-po* ➔ *GitHub repo*).

---

## Spoken Commands & Punctuation

| Spoken | Effect |
|---|---|
| `full stop` | Inserts `.` and capitalizes following word |
| `new line` | Inserts `\n` |
| `new paragraph` | Inserts `\n\n` |
| `question mark` / `exclamation mark` | Inserts `?` / `!` |
| `scratch that` | Erases the previous phrase typed |
| `cap that` | Capitalizes previous word (`naukri cap that` ➔ `NAUKRI`) |
| `literally <word>` | Escapes command to type literal word |

---

## Quick Start

### 1. Requirements
- Windows 10/11
- Python 3.10 – 3.14
- NVIDIA GPU with CUDA support (or CPU mode)

### 2. Installation
```bash
git clone https://github.com/tejashendre/dictate-local.git
cd dictate-local
python -m pip install -r requirements.txt
python install.py
```

### 3. Usage
- **Start Dictation**: Run `Dictate.cmd` (or launch "Local Dictation" from Start Menu).
- **Toggle Recording**: Press **`F9`** anywhere in Windows. Speak, then press **`F9`** again (or pause in streaming mode).
- **Settings**: Right-click the system tray icon or the floating pill to configure models, pause thresholds, hotkeys, and vocabulary.

---

## License

MIT License.
