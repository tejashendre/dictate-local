# Dictate Local

**Offline speech-to-text for Windows. Runs on a 4 GB laptop GPU, types into any application, and never touches the network after the model downloads.**

Press F9, talk, and the words appear where your cursor already is — in a browser, an IDE, a Word document, a Slack box. No quota, no subscription, no audio leaving the machine.

<!-- Replace with the demo link once recorded -->
**Demo video:** _coming shortly_ · **Repository:** you are here · **Author:** [Tejas Hendre](https://www.tejashendre.com/)

---

## Why I built it

I speak at about 120 words a minute and type at roughly half that. Closing that gap with dictation is a solved problem — I had been using a hosted tool happily and it worked well.

What I wanted to know was **how hard the problem actually is**, and whether the specific constraint I had could be met at all:

- **A 4 GB laptop GPU** already shared with a local LLM, so under 500 MB of VRAM to work with.
- **No network**, because a lot of what I dictate is client and research material.
- **Proper nouns that matter** — company names, foreign terms, and abbreviations that general models have no reason to know.
- **Unmetered**, because a quota that interrupts you mid-thought costs more than the subscription does.

That last set is a narrow constraint, not a market gap. Hosted tools optimise for accuracy, breadth and zero setup, and they are right to — those are what most people need. This project asks a different question: *what can you get if you refuse to send audio anywhere, and you only have 4 GB?*

The answer turned out to be: more than I expected, and the interesting part was never the transcription.

---

## How it works

```
mic ──▶ sounddevice ──▶ float32 buffer ──▶ Silero VAD ──▶ faster-whisper (small.en, CUDA int8_float16)
                                                │                       │
                                     pause detected (0.7s)      initial_prompt biasing
                                                │                       │
                                                ▼                       ▼
                          corrections ──▶ near-miss snap ──▶ command grammar
                                                                        │
                                            keyboard.write() ──▶ focused window (clipboard untouched)
```

### Three layers of accuracy, not one

A general model has no reason to know how *Naukri* or *DEAMIE* is spelled. Fine-tuning for that is overkill, so accuracy is built in layers, each catching what the one above it misses:

1. **`initial_prompt` biasing** — soft-biases decoding toward terms in `vocabulary.txt`, budgeted to 180 tokens so the prompt never crowds out the audio's own context.
2. **Explicit correction rules** — deterministic `heard -> wanted` mappings for what biasing cannot reach, such as *guitar pre-po → GitHub repo*.
3. **Near-miss snapping** — Levenshtein snapping at a strict 0.2 edit ratio with a 6-character floor, tuned so it never rewrites an ordinary English word.

Layer 1 alone gets 86% of the test terms. All three get 100%, and the control corpus shows **zero** degradation — the accuracy gain costs nothing on ordinary speech.

---

## Measured on real hardware

RTX 3050 Laptop, 4 GB VRAM. Every figure is produced by the offline test suite in `tests/`, reproducible with `Run-Tests.cmd`.

| Metric | Measured | Note |
|---|---|---|
| **GPU inference** | **11–16× realtime** | ~210 ms per phrase, `small.en` int8_float16 |
| **VRAM footprint** | **433 MB** | Leaves room for a local LLM alongside |
| **CPU fallback** | **2.3–2.7× realtime** | Degrades automatically; never crashes the process |
| **Vocabulary recall** | **50% → 100%** | 7/14 → 14/14 terms across the three layers |
| **Control WER change** | **0.0%** | No regression on an 89-word ordinary-speech corpus |
| **Latency to first word** | **~31% into the phrase** | Streaming mode, LocalAgreement-2 stable prefix |
| **Focus behaviour** | **`WS_EX_NOACTIVATE`** | The status pill never takes keyboard focus |

Larger models were measured and rejected: `medium.en` ran 2.9× slower for +592 MB and got one term *worse*; `distil-medium.en` returned 92.6% WER and was unusable. Bigger was not better under this constraint.

---

## What building it actually taught me

The transcription was the easy half. Everything below was found by using the tool daily and having it fail, and it is the part I would want to talk about:

**The last mile is the product.** Getting text out of audio is a solved library call. Getting it into the *right* window, with the right capitalisation, without stealing focus, without clobbering the clipboard, and recovering when the user has moved on — that is where the actual work is. The status pill alone needed `WS_EX_NOACTIVATE` plus restoring the prior foreground window, because a window that takes focus types your words into itself.

**Silence is not the absence of speech.** Half a second of near-silence produced a confident *"Thank you for watching."* — a YouTube artifact from the training data, which would have been typed into whatever was focused. Voice activity detection had to gate the output, not just trim it.

**Suspend destroys more than you think.** After a lid close, the hotkey silently stopped working. Fixing that revealed a second failure underneath: sleep also destroys the CUDA context, and touching the old model afterwards kills the process in native code with no Python traceback at all — `ucrtbase.dll`, `0xc0000409`. The model now rebuilds on wake, and the dead handle is deliberately leaked rather than freed, because freeing it is itself a call into the destroyed context.

**A safety feature that fires wrongly is worse than none.** A noise gate added to ignore other voices in the room locked the author out of his own tool, because a test had written synthetic audio levels into the live settings file. It now self-disables after repeated rejections, refuses to write settings while testing, and shows on screen when it rejects something.

**Quiet failures outlive loud ones.** The last audit found three faults that never crashed anything: one oversized line in the vocabulary file silently switched off *all* biasing; a malformed settings file killed startup before the log existed; and a recording nobody stopped grew unbounded at 64 KB/s. None had ever been reported, because nothing visibly broke.

Every one of those became a test. There are 15 suites, and each exists because something failed first — which is why each is checked by reverting the fix and confirming the test fails.

---

## What it does not do

Stated plainly, because a project page that only lists strengths is not worth reading:

- **English only.** `small.en` is an English model; multilingual dictation is not supported.
- **Windows only.** It depends on DWM, SendInput and Win32 focus behaviour throughout.
- **NVIDIA GPU for full speed.** It runs on CPU at 2.3–2.7× realtime, which is usable but noticeably slower.
- **Vocabulary is manual.** Terms come from a text file you maintain, or from mining your own notes. There is no learning loop.
- **Single user, single machine.** No sync, no accounts, no telemetry — by design, but it means no cross-device continuity.

---

## Spoken commands

| Say | Result |
|---|---|
| `full stop` / `comma` / `question mark` | Inserts the punctuation and capitalises what follows |
| `new line` / `new paragraph` | `\n` / `\n\n` |
| `scratch that` | Erases the last phrase typed (capped, so a mishearing cannot run away) |
| `cap that` | Capitalises the previous word — *naukri cap that* → `NAUKRI` |
| `literally <word>` | Types the word instead of executing it as a command |

---

## Running it

**Requirements:** Windows 10/11, Python 3.10–3.14, NVIDIA GPU with CUDA (or CPU mode).

```bash
git clone https://github.com/tejashendre/dictate-local.git
cd dictate-local
python -m pip install -r requirements.txt
python install.py
```

Then launch **Local Dictation** from the Start Menu, or run `Dictate.cmd`. Press **F9** anywhere to start and stop. Settings are on the tray icon.

Run `Run-Tests.cmd` to reproduce every number in the table above. The first run generates its speech corpus locally with the Windows SAPI voices, so the whole suite is offline.

`ARCHITECTURE.md` has the full design record: what was measured, what was tried and rejected, and what each failure taught.

[`BUILD_LOG.md`](BUILD_LOG.md) has something rarer: what it cost. This was built in 29 hours by dictating at it, and that page publishes the token accounting, the ratio of instruction to output, and the six failures that shaped the design — including the three that never crashed anything and so were never reported.

---

## Who built this

**Tejas Hendre** — Mumbai. I work on customer and commercial problems: diagnosing what is actually wrong, designing something that addresses it, and proving it works in front of the people who have to believe it. Previously partner consulting at Zalando and research operations at PitchBook; MSc from ESCP Business School.

This is one of several things I have built to stay close to the systems I talk about. If you are building something a customer has to *see* working before they will believe it, that is the work I want to be doing.

[tejashendre.com](https://www.tejashendre.com/) · [LinkedIn](https://linkedin.com/in/tejashendre)

---

## License

MIT.
