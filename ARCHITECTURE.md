# Local Dictation — Architecture

**A permanently local, unmetered speech-to-text system. The goal is not to imitate Wispr Flow; it is to remove the limit, the subscription and the network round trip while keeping the part that actually matters: text appearing where the cursor is, fast enough that you stop noticing the tool.**

**Status: v1 built, measured, and confirmed working on a real voice, 31 August 2026. Steps 1 to 4 plus the always-on-top pill are done and covered by six test suites. Run `Run-Tests.cmd` to reproduce every number in this document.**

---

## 1. Why build this at all

| Problem with the hosted tool | What local solves |
|---|---|
| Daily and monthly caps | **No cap. The constraint is your GPU, not a plan tier** |
| Subscription cost | Zero, permanently |
| Audio leaves the machine | **Nothing leaves.** Relevant when dictating client material or private notes |
| Breaks when it breaks | You own the failure and can fix it |
| **No custom vocabulary at all**, confirmed by inspecting its config | **You can bias the model toward your own words**: Naukri, ESCP, Zalando, PitchBook, Arbeitszeugnis, candidature spontanée |

**The honest counterpoint:** a hosted product has done work on formatting, punctuation restoration and command handling that a weekend build will not match immediately. **v0 is already usable. Closing the remaining gap is the point of v1.**

---

## 2. Current state, v1

```
mic ──▶ sounddevice ──▶ float32 buffer ──▶ Silero VAD ──▶ faster-whisper (small.en)
                                                │                    │
                                    pause detected?          initial_prompt
                                                │                    │
                                                ▼                    ▼
                          corrections ──▶ near-miss snap ──▶ command grammar
                                                                     │
                                              keyboard.write() ──▶ focused window
                                                                     │
                                                              transcript.log
```

| Component | Choice | Why |
|---|---|---|
| Capture | `sounddevice`, 16 kHz mono float32 | Whisper's native rate. No resampling |
| Model | `faster-whisper` `small.en`, int8_float16 | Roughly 500 MB. Fits 4 GB VRAM with headroom |
| Trigger | `keyboard`, F9 toggle | **Toggle, not push-to-hold.** Holding a key while speaking quickly is the wrong interaction |
| VAD | Silero, bundled with faster-whisper | ~11 ms per pass. No extra download, no network |
| Output | `keyboard.write()` | Types into any focused window. No clipboard, so nothing is clobbered |
| Indicator | tkinter pill, `WS_EX_NOACTIVATE` | Always in front, **never takes keyboard focus** |
| Log | append-only `transcript.log` | Nothing spoken is ever lost |

**Code layout.** `dictate.py` owns the microphone, the hotkey and the keyboard. `dictate_core.py` and `dictate_stream.py` own everything else, and deliberately contain no audio or keyboard code — which is why the whole of the vocabulary, command and fallback behaviour can be tested headlessly.

### The correction that mattered most

**The v0 note in this document claimed the GPU path was verified. It was not.** `cublas64_12.dll` was absent machine-wide: no CUDA toolkit, no NVIDIA pip packages. `WhisperModel(...)` constructed happily on CUDA and the process only fell over later, inside `transcribe()`, when the encoder first touched cuBLAS — so v0's `try/except` around the constructor caught nothing and the tool exited on first use.

Two fixes, both in the code now:

- `nvidia-cublas-cu12` installed, and its directory pushed onto `PATH` **before** `faster_whisper` is imported. `os.add_dll_directory` is not enough; ctranslate2 loads the DLL with a plain `LoadLibrary`, which searches `PATH`.
- A **warmup transcribe at startup**, so a broken GPU path fails in the first two seconds instead of on the first thing you dictate.

**Measured after the fix:** 2.5 s warm start, **11–16× realtime** on GPU, 433 MB VRAM. CPU fallback runs at **2.3–2.7× realtime**.

---

## 3. What v1 added, and what it measured

Every number below comes from `Run-Tests.cmd`. The test corpus is generated locally with the Windows SAPI voices at a calibrated **141 WPM**, inside the measured 120–147 band. **Synthetic speech is a regression signal, not an accuracy guarantee** — the final check is always a real voice.

### 3.1 Custom vocabulary — done

Terms live in `vocabulary.txt`, one per line, and are fed to Whisper as an `initial_prompt`. **This was the highest value per line of code, as predicted.**

| | Terms correct |
|---|---|
| No prompt | 7/14 — **50%** |
| `initial_prompt` | 12/14 — **86%** |
| Prompt + corrections + snapping | **14/14 — 100%** |

**Control WER stayed at 0.0% throughout**, which is the number that actually mattered: a prompt that fixes proper nouns but degrades ordinary speech would be a bad trade.

Three layers, because one was not enough:

1. **`initial_prompt`** biases decoding. Soft, and it does most of the work.
2. **Near-miss snapping** catches what biasing misses. Streaming proved this was necessary: the same word came out `Arbeitsugnis` in one chunking and `Arbeitsuegnis` in another, and no hand-written list of variants converges on a German compound noun.
3. **Explicit `heard -> wanted` rules**, for what neither of the above can reach — mainly terms too short for snapping, like `Deami -> DEAMIE`.

**Snapping is the part that could do real damage**, since silently rewriting an ordinary English word into a proper noun is worse than leaving one term misspelt. The guard is an edit-distance ratio of 0.2. The worked example: `linked` is two edits from `LinkedIn`, which is 0.25 — at 0.25 the sentence "I linked the file" becomes "I LinkedIn the file"; at 0.2 it does not. **Measured against an 89-word collision corpus: zero rewrites.**

### 3.2 Spoken punctuation and commands — done

**The probe that shaped this: Whisper already converts a spoken "comma" by itself.** `"Add milk comma eggs comma and bread"` transcribes as `"Add milk, eggs, and bread"`. So **`comma` is deliberately not a command** — implementing it would buy nothing and would start mangling "the comma in that sentence". What Whisper does *not* convert, and what is therefore worth handling:

| Spoken | Effect |
|---|---|
| `full stop` | `.` and capitalise the next word |
| `new line` | one line break |
| `new paragraph` | two line breaks |
| `question mark` / `exclamation mark` | `?` / `!` |
| `scratch that` | Discard the last thing typed |
| `cap that` | Capitalise the previous word |
| `literally <word>` | Escape hatch, type the word instead of running it |

**The hard part is that the command use and the ordinary use are the same words.** "I finished the report full stop send it" and "the car came to a full stop at the light" are identical to a text matcher. The discriminator that works is **the determiner in front**: nobody dictating punctuation says "a full stop", and everybody using it as a noun does. A following "of" is the second guard, for "a new line of work".

All six ambiguous phrasings in the test suite stay quiet. All eight command phrasings fire.

### 3.3 GPU contention — done

**The trap is that a CUDA failure does not surface when the model is constructed.** It surfaces later, inside `transcribe()`. So a `try/except` around the constructor catches nothing, and the tool dies mid-dictation. Both halves are now handled:

- **Warmup at startup** with half a second of silence, so a broken GPU path fails immediately rather than on the first utterance.
- **Retry on CPU mid-session**, replaying the same audio. **Falling back must never cost the words that were just spoken.**

Before loading, free VRAM is read from `nvidia-smi` and compared against the model's needs plus 400 MB of headroom. Verified against real contention: with `qwen3.5:4b` loaded and 1440 MB free, dictation still ran on the GPU; below the threshold it moves to CPU and says why.

**Deliberately not implemented: automatic downgrade to `base.en`.** The rule in Part 4 stands — `base.en` starts dropping words at this speaking rate, so the fallback trades speed for accuracy by moving to CPU, not accuracy for speed by shrinking the model. The ladder stays available manually via `DICTATE_MODEL`.

| Model | Size | Use |
|---|---|---|
| `base.en` | ~140 MB | Only if you have tested it against your own speech |
| `small.en` | ~500 MB | **Default. The right point on this hardware** |
| `distil-medium.en` | ~750 MB | Try if accuracy is short and VRAM allows |
| `medium.en` | ~1.5 GB | Too large alongside anything else on 4 GB |

### 3.4 Streaming — done, and behind a switch

Enabled with `DICTATE_STREAM=1` or `Dictate-Streaming.cmd`. **The batch path is untouched and remains the default**, because it is the one that has been used in anger.

**Streaming dictation dies in one specific way: the tool types a guess, changes its mind, and has to unsay it.** Backspacing over text the user can already see produces flicker, races their own typing, and destroys anything they did in the window in between. So the rule is absolute: **nothing is ever retyped or unsaid.** Text is emitted only once it cannot change. That single constraint drives the whole design:

- **Pause-finalised, the common case, and it is exact.** Silero VAD watches for a pause of 0.8 s. On a pause the audio is a complete phrase, so it is transcribed once and emitted once. No guessing, no revision.
- **Stable-prefix, the fallback for long unbroken speech.** Past 12 s without a pause, the buffer is transcribed early and compared with the previous pass; only the words the two passes *agree* on are emitted. This is LocalAgreement-2.

**Measured on a 17.2 s clip, fed in real time through the real worker thread:** first text appeared at **5.3 s — 31% of the way through speaking** — and the last text landed **0.4 s after the final word**. Emissions were append-only, and the streamed transcript had **100% word overlap with a single batch pass**.

### 3.5 The pill — done

Dictation was invisible: you pressed a key and hoped. A small always-on-top indicator now shows idle / listening (with a timer) / thinking / typed. Drag it anywhere, position is remembered, right-click quits. `DICTATE_HIDE_CONSOLE=1` hides the console so the pill is the only UI.

**The entire difficulty is that an always-on-top window steals keyboard focus, which would be fatal here** — everything this tool does is type into the window you were already in, so a pill that takes focus types into itself.

`WS_EX_NOACTIVATE` alone does **not** fix it, and that is the trap. Tracing the foreground window through construction showed it is taken earlier than expected:

| Step | Foreground |
|---|---|
| `tk.Tk()` | unchanged |
| `root.withdraw()` | unchanged |
| `root.update_idletasks()` | **taken here**, by a window called `tk`, before any style exists |
| apply `WS_EX_NOACTIVATE` | too late |
| `ShowWindow(SW_SHOWNOACTIVATE)` | too late |

**`withdraw()` does not save you** — `update_idletasks()` realises and maps the window anyway — and once the foreground is taken, re-asserting the style does not give it back. Both were measured.

So the fix has two halves, and both are needed: the extended styles (so it cannot be activated by clicking, and stays out of alt-tab), **plus recording the foreground window before creating anything and handing it back with `SetForegroundWindow`.** That second half is what actually returns focus to your document.

`tests/test_overlay.py` asserts the pill never *becomes* the foreground window — deliberately not that the foreground never changes, since you switching apps mid-test is not a failure. **Stable across repeated runs; the first version of this test passed once by luck, which is why it now checks the right thing.**

### 3.6 Cleanup — done, in three lanes

`DICTATE_POLISH` = `off` / `fast` (default) / `llm`.

**The finding that set the default: most of the mess is mechanical.** Raw output from `small.en` on deliberately disfluent speech looked like:

> "So like basically what i'm trying to say is that ah **we need to we need to** finish the report before friday."

The parts a rule can remove with certainty — the `ah`, the repeated `we need to` — cost **1.5 ms**. Asking a 4B model to fix them costs **2 seconds**, because it regenerates every word that was already correct. Generation runs at ~15 tok/s here, so the bill scales with how much you said, not how much was wrong.

**What the fast lane deliberately will not touch.** `like`, `basically`, `actually`, `you know` and `I mean` are all real words — "I like this", "looks like rain", "you know the answer". Telling filler from content there needs sentence understanding, which is what the `llm` lane is for. `literally` is never touched either: it is the escape hatch in the command grammar. **12 sentences of look-alike-but-real usage pass through unchanged.**

**The `llm` lane, measured on this machine with `qwen3.5:4b` over local Ollama:**

| | |
|---|---|
| Prompt processing | **263 tok/s** — the old "1.4 tok/s" note in this document was wrong |
| Generation | **15 tok/s** — the bottleneck |
| Cleanup latency, model resident | **2.1 s** |
| Cleanup latency, model unloaded | **6 s+**, it times out |

That last row is why the model is **preloaded at startup with `keep_alive`**. Ollama unloads an idle model and reloading costs ~11 s, which would silently eat the per-utterance timeout and drop you back to rules without saying why.

Three guards, because a cleanup pass must never cost you your words:

1. **Length cap.** Over 60 words it skips the model rather than stalling.
2. **Sanity check.** If the output is under 0.4× or over 1.6× the input length, it is rejected — that is a model answering the text rather than editing it.
3. **Re-snap.** The model mangled a vocabulary term in testing (`Naukri last` → `Naukrilast`), undoing the work that got proper nouns to 100%, so `fuzzy_snap` runs again afterwards.

Any failure at all — unreachable, timeout, empty, rejected — falls back to the rules result.

### 3.7 The pause timer was set wrong

`DICTATE_PAUSE` was 0.8 s. **That was a guess, and a sweep showed it was backwards.** On speech with mid-clause hesitation — commas, thinking pauses, how anyone actually dictates — a 12.3 s clip gave:

| Pause | First text | Chunks | Words kept |
|---|---|---|---|
| 0.4 s | **2.5 s** | 4 | 100% |
| 0.5 s | 7.4 s | 2 | 100% |
| 0.8 s | **12.2 s** | 1 | 100% |

**At 0.8 s the timer never fires until you stop talking, so streaming bought nothing at all.** At 0.4 s text lands at 2.5 s with no measured loss — it splits at commas, which are natural boundaries, not mid-word. Default is now **0.4 s**.


### 3.8 One app, not a pile of launchers

**The honest failure this fixed: the tool had four `.cmd` files and eleven `DICTATE_*` environment variables.** That is a toolkit. Every feature added a switch, and the switches were never given a home.

Now there is one entry point, `Dictate.cmd`, and settings live in `settings.json`, edited by right-clicking the pill. Environment variables still take precedence when set, so every test and tuning note stays valid — but nobody has to use them.

| Was | Now |
|---|---|
| `Dictate-Streaming.cmd` | Settings → Speed → *Type as I pause* |
| `Dictate-Hands-Free.cmd` | Settings → Startup → *Hide the console* |
| `Dictate-Polished.cmd` | Settings → Cleanup → *Full* |
| `DICTATE_PAUSE=0.4` | Settings → Speed → slider, with what each end costs |
| nothing | Settings → Startup → *Start when Windows starts* |

`Dictate-Everywhere.cmd` survives as a separate file because it needs a UAC prompt at launch, which a running app cannot grant itself.

**Settings are deliberately not a popup menu on the pill.** `tk_popup` on a `WS_EX_NOACTIVATE` window **hangs the process outright** — it grabs input that a window which cannot take focus will never receive. That was measured while building this, and the stuck process had to be killed from outside. So settings open as an ordinary window, which is allowed to take focus because you are not dictating while configuring. `tests/test_settings.py` checks the pill still refuses focus after that window has opened and closed.

Changes that can apply live do (cleanup level, pause, snapping, commands); the rest say which ones need a restart rather than silently ignoring them.


### 3.9 First real voice, and the two bugs it found immediately

**31 August 2026, 19:41. The tool was used to dictate a real message, and it worked.** `transcript.log` holds two `(streamed)` entries whose text matches what was sent: streaming fired, split at a natural pause, and typed into a chat box.

**That closes the caveat attached to every other number in this document.** Everything above was measured against synthetic Windows SAPI speech at a calibrated 141 WPM, with the standing admission that no real voice had ever been heard. It has now.

**One session of real use found two bugs that eight test suites had not.**

**One — Whisper invents text to fill silence.** Two half-second recordings produced:

```
(0.5s spoken) So...
(0.5s spoken) Thank you for watching.
```

Whisper was trained on subtitles, so given near-silence it writes what appears at the end of videos. **Both were typed into whatever window was focused**, which is the worst failure this tool can have. Fixed with a Silero VAD gate in the batch path — the streaming path already had one — requiring 0.35 s of actual speech, not merely that the key was held long enough. A narrow phrase backstop covers the short clips where VAD is least reliable, and it only fires under one second of speech so it can never eat a real "Yes."

**Two — streamed entries logged no duration**, so the one measurement this project most needed from real use was being thrown away. The log now records seconds, word count and words per minute per phrase.

**This is the loop that matters from here.** Synthetic speech proved the engine; only real use finds bugs like a subtitle artifact being typed into a document. `transcript.log` is now the source of truth for the real speaking rate — worth checking against the 120/147 WPM from Wispr Flow's history, since a rough read of that first session suggested rather faster.


### 3.10 Made it behave like software, not a script

Screenshot feedback, and it was fair: the pill was **sitting in the top-left corner over the title bar** of whatever was behind it, permanently, and a permanently visible indicator is a distraction whichever corner it is in.

Two genuine bugs were behind the first half, and both are the same root cause — trusting tkinter's idea of where a window is:

| Bug | Cause |
|---|---|
| Pill stuck at 0,0 | `geometry()` was set while the window was withdrawn and **never applied**, then `SetWindowPos(SWP_NOMOVE)` preserved that un-applied position. `update_idletasks()` after `geometry()` is what makes it real. |
| Dragging jumped | `winfo_x()` / `winfo_y()` **report 0 for an overrideredirect window**, so the drag offset was always computed from the wrong origin. Now read via `GetWindowRect`. |

The second half was a design error rather than a bug. **The fix is a tray icon.** Real Windows applications live in the notification area: quiet when idle, always reachable, quit from a right-click. So:

- **The tray icon is the app.** Colour follows state; right-click gives Settings, Edit my words, Start with Windows, Quit.
- **The pill only appears while working.** With the tray carrying the "I exist" job, nothing is on screen at all while you are not dictating.

`pystray` runs its own Win32 message loop and tkinter needs the main thread, so the icon runs on a worker. **Verified they coexist** before building on it — the tray survives a full tkinter mainloop and vice versa. Hiding uses `ShowWindow`, never `withdraw`/`deiconify`, because `deiconify` re-maps the window and takes the foreground.

If `pystray` is missing the app still runs; the pill simply stays visible, since otherwise nothing would show that the tool is alive.


### 3.11 The F9 crash, and the test class that was missing

**A NameError shipped past ten passing suites.** A setting was used inside `hotkey_loop` but the line defining it never landed. The app started perfectly and died the instant F9 was pressed:

```
NameError: name 'VAD_THRESHOLD' is not defined
```

**Unit tests could not catch this**, because the failure was in the wiring between parts rather than inside any of them, and no test ever entered that function — it only runs on a keypress. So `tests/test_endtoend.py` now does two things nothing else did:

1. **Resolves every global name** each function references, across all eight modules, against what the module actually defines. An unresolvable name is a crash waiting for the right keypress.
2. **Runs both recording paths for real** — batch and streaming, with real audio, with only the microphone and keyboard replaced. The code F9 reaches is now executed by the suite.

The name checker found three false positives on its first run — `__file__`, and two closure variables — which were fixed in the checker rather than the code. Nested functions are no longer scanned standalone, since they see their parent's locals.

**The crash handler earned its place the same day.** Running detached there is no console, so the traceback would have vanished; instead it landed in `dictate.log`, which is how the bug was diagnosed in one step.

### 3.12 Packaging: a shortcut, not a 1.5 GB executable

Asked for an `.exe` "like Claude has". Measured what one would have to contain:

| | |
|---|---|
| `nvidia` (CUDA runtime) | **914.7 MB** |
| `ctranslate2` | 59.8 MB |
| `numpy`, `PIL`, rest | 48 MB |
| **Total, before the speech model** | **1,022 MB** |

A single-file build would be roughly 1.5 GB, would unpack to a temp folder on **every launch**, and would put the already-delicate CUDA DLL discovery behind another layer. It would be a worse program that merely looked more official.

What actually makes software feel installed is being in the Start Menu with its own icon, starting without a console, and running until you quit it. `install.py` does that: a multi-size `.ico`, Start Menu and Desktop shortcuts, and `--remove` to undo. Nothing goes into Program Files, nothing touches the registry.

**The console bug this replaced was real, though.** The old launcher ran `python.exe` in a console window, so closing the window killed the app. It now launches `pythonw.exe` detached and exits immediately — verified by closing the launcher and confirming the process was still alive.

### 3.13 What the app actually stores

Measured rather than assumed, because the worry was that dictation would fill the disk:

| | |
|---|---|
| Audio written to disk | **none** — it exists only in memory |
| Text per utterance | 101 bytes |
| A peak day (2,789 words) | ~34 KB |
| A year of heavy use | **~12 MB** |

So storage was never the risk it felt like. Logs are trimmed anyway — about a year of transcript history, 1 MB of diagnostics — because nothing should append forever.


### 3.14 What a day of real use actually changed

Everything below came from using the tool, not from testing it. That
distinction is the point: eleven passing suites did not catch any of it.

**Two copies were running at once.** Both held the global hotkey and both
typed, so F9 toggled them out of step and one kept recording after the other
stopped. It looked exactly like broken transcription. A named mutex now
refuses the second copy. **No amount of model tuning would have fixed this.**

**A test wrote its own audio level into the live settings.** The noise gate
learned the synthetic corpus at RMS 0.1164; the real microphone measures
0.0087-0.0123, about ten times quieter. The floor then sat above every real
phrase and dictation stopped working, with the reason only in a log file.
Three fixes: `save()` refuses the live file during tests, the gate switches
itself off if nothing gets through, and rejected phrases now say so on screen.

**A `NameError` shipped past eleven suites**, because no test ever pressed F9.
`tests/test_endtoend.py` now resolves every global name in every module and
runs both recording paths for real.

**Voice detection ran twice per phrase**, computing the same answer. One pass
now, 50% off that step.

**Python is not the bottleneck**, measured on one utterance:

| | |
|---|---|
| Model inference (C++/CUDA) | **884 ms — 78.6%** |
| Voice detection (also a neural net) | 209 ms — 18.6% |
| **All the Python** | **2.3 ms — 0.2%** |

**Battery barely matters.** Under load the GPU held 1,920 MHz of 2,100 on
battery against 1,972 on AC, and drew *less* power for the same work.

**A bigger model does not help here.** `medium.en` cost 2.9x the wait and
592 MB more VRAM and scored one term *worse*; `distil-medium.en` came back at
92.6% WER, effectively broken in this configuration. The caveat matters:
`small.en` already scores 0.0% WER on this corpus, so the benchmark cannot
detect an improvement even if one exists on messier real speech.

**Most vocabulary candidates were already correct.** Mining 250 Obsidian notes
produced 90 candidates ranked by frequency - and frequency was the wrong
signal. Speaking each one and transcribing it with no prompt showed Whisper
already spelled **67 of 90** perfectly. Only 23 needed help, and what it
misheard became the correction rules for free.


---

## 4. What the Wispr Flow data actually showed

**Investigated 31 August 2026. Two folders exist and they are not equivalent.**

| Path | Contents | Verdict |
|---|---|---|
| `%LOCALAPPDATA%\WisprFlow` | Electron binaries, Squirrel installer, three versioned app folders | **Application code. Not read. Decompiling a commercial product to copy its implementation is not the approach here** |
| `%APPDATA%\Wispr Flow` | `config.json`, `flow.sqlite` at 495 MB, logs, backups | **User data. This is the useful half** |

### Finding 1: there is no custom vocabulary list

**`config.json` was checked for every plausible key: dictionary, vocabulary, word, term, replacement, custom, snippet, command, shortcut. None exists.** The top-level keys are `notifications`, `nudge`, `activationCron`, `voiceProfile`, `prefs`, `helperLaunch`, `calendarSync`, `syncCoordinator`, `syncSocketClient`.

**This changes the plan in a useful direction.** Step 1 was framed as catching up to a feature the hosted product has. It does not have it. **Vocabulary biasing via `initial_prompt` is therefore a differentiator rather than a gap**, and it remains the highest-value next step because proper nouns are what a general model gets wrong.

### Finding 2: the speaking rate justifies the design

| Measure | Value |
|---|---|
| Total words dictated | **16,989** |
| Reported average | **120 WPM** |
| Derived from total words over total duration | **147 WPM** |
| Words in one day | 2,789 |

**At 120 to 147 words per minute, push-to-hold is the wrong interaction and toggle is right.** It also sets the model floor: `small.en` sustains that rate, `base.en` would begin dropping words. **Do not downgrade the model to save VRAM without testing against real speech at this rate.**

### Finding 3: the history is a vocabulary source

**`flow.sqlite` holds 495 MB of dictation history.** Mining term frequency from it would produce a far better `initial_prompt` than guessing at a word list.

**It also contains everything ever dictated, so it is treated as private by default.** Do not read, export or process it without an explicit instruction, and never move it out of the machine.

### Still open

1. Whether a command grammar exists, and where it lives.
2. Whether formatting differs per application.
3. How partial versus final text is handled in the typing path.

**These were not answerable from the config, and answering them from the binaries is out of scope.**

**They stopped being blockers.** The point of asking was to avoid rediscovering solved problems slowly. In the event, building steps 2 and 4 answered the underlying design questions directly and from measurement rather than from someone else's implementation:

- **On the command grammar (question 1):** the real problem is not which commands to have, it is that command use and ordinary use are the same words. The determiner guard solves it, and it was cheap to find. Question 2 in Part 4a is now answered for this tool, if not for theirs.
- **On partial versus final text (question 3):** the answer is that there is no partial text. Nothing is emitted until it cannot change. See 3.4 — this is the constraint the entire streaming design is built on.
- **Per-application formatting (question 2)** remains genuinely unexplored, and is the one place where looking at a mature product would still have taught us something. It is not implemented and is not currently planned.

## 4a. The original questions, and which ones got answered

**Kept so the reasoning is visible. Questions 1 and 5 are answered in Part 4. The rest were not answerable from the config alone.**

1. **How is custom vocabulary stored and weighted?** Flat list, or per-context?
2. **What is the command grammar**, and how does it avoid firing on ordinary speech?
3. **Is there per-application behaviour** — different formatting in a terminal versus a document?
4. **How is partial versus final text handled** in the typing path, and does it correct in place?
5. **What does the settings schema cover** that this design has not anticipated?

**The point is not to copy the implementation.** It is to find the decisions that were already made well, so the same problems are not rediscovered slowly.

---

## 5. Non-goals

**Not building:** a cloud service, an account system, multi-user support, mobile, or a polished installer. **This is a personal tool on one machine, and that constraint is what makes it finishable.**

**Not doing:** speaker diarisation, translation, real-time captions, or meeting transcription. **They are different products and each would double the work.**

---

## 6. Sequence

| Step | Work | State |
|---|---|---|
| 0 | Capture, transcribe, type, log, toggle hotkey | **Done** |
| 1 | Custom vocabulary via `initial_prompt` | **Done. 50% → 100% term recall, control WER unchanged** |
| 2 | Spoken punctuation and `scratch that` | **Done. 8 commands fire, 6 ambiguous phrasings stay quiet** |
| 3 | Read the Wispr Flow files and revise this document | **Done 31 August 2026. See Part 4** |
| 4 | Streaming with voice-activity detection | **Done, behind `DICTATE_STREAM=1`. First text at 31% of the utterance** |
| 5 | GPU contention handling and automatic fallback | **Done. Also fixed the latent crash it exposed** |
| 6 | Always-on-top pill, so the tool is in front | **Done. Never takes keyboard focus** |
| 7 | Cleanup lanes and a corrected pause default | **Done. Fast lane free, llm lane ~2.1 s** |
| 8 | Collapse into one app with real settings | **Done. One launcher, settings.json, start-with-Windows** |
| 9 | Microphone meter, silence guard, real-voice use | **Done. Validated on a real voice 31 Aug 2026** |
| 10 | Tray icon, auto-hiding pill, position fixes | **Done. Behaves like an installed application** |
| 11 | Single instance, detached launch, crash surfacing | **Done. Two copies at once was the real cause of the "buggy" session** |
| 12 | End-to-end test, Start Menu install, data pruning | **Done** |
| 13 | Noise gate, vocabulary mining, HD pill | **Done. Twelve suites** |

### What is worth doing next

1. **Use it for a real day's work and add what breaks to `vocabulary.txt`.** Every number here is from synthetic speech. The list is currently 15 terms guessed from this document, not mined from real usage.
2. **Tune `DICTATE_PAUSE` to your own rhythm.** 0.8 s is a reasonable clause boundary, not a measured optimum for this speaker.
3. **Decide whether streaming becomes the default.** It needs a week of real use before that call is worth making.
4. **Consider mining `flow.sqlite` for term frequency** — still the best available source for a real vocabulary list, and still untouched pending an explicit instruction.

---

## 6a. How to check any of this

```
Run-Tests.cmd
```

Twelve suites, all offline. The first run generates the speech corpus with the local Windows SAPI voices.

| Suite | Asks |
|---|---|
| `test_vocab` | Do your terms come out right, and does the prompt hurt ordinary speech? |
| `test_fuzzy` | Does near-miss snapping ever rewrite an ordinary English word? |
| `test_commands` | Do commands fire, and do they stay quiet on ordinary phrasing? |
| `test_fallback` | Does a broken or contended GPU degrade instead of crashing? |
| `test_stream` | Does text appear before you stop talking, and is it ever unsaid? |
| `test_overlay` | Does the pill ever steal keyboard focus? |
| `test_polish` | Is filler removed, and are real words ever eaten? |
| `test_settings` | Do settings persist and apply, and does the pill still refuse focus? |
| `test_silence` | Does near-silence ever type anything? |
| `test_tray` | Does the tray work, and does the pill sit right and hide when idle? |
| `test_endtoend` | Does every name resolve, and does the F9 path actually run? |

**Note on running the whole suite back to back:** each suite loads its own model, and on a 4 GB card a run can occasionally start before the previous one has released VRAM. If a suite fails once and passes alone, that is what happened.

---

## 7. Where this sits

**Personal tooling, not career evidence, until an external person uses it.** It is a legitimate portfolio piece under the same rule as everything else in this workspace: **a project becomes proof when someone other than its author uses it and something measurable changes.**

Until then it is a tool that makes the real work faster, which is a good enough reason to have built it.
