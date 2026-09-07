# Dictate Local: Final Architecture

**Status:** Final implementation design for user review. The code does not yet implement it.

**Decision:** Build Option 2, the lean hybrid finalizer.

**Product boundary:** One user, one Windows laptop, English-first dictation, permanently local after model installation, no subscription, no usage quota, and no required server.

**Authority:** This file is the implementation source of truth. It supersedes the earlier v1 architecture narrative and the broad multilingual proposal in `docs/superpowers/specs/2026-09-06-multilingual-accuracy-engine-design.md`. Git history remains the record of how v1 was built.

## 1. Final product promise

Press F9, speak naturally for as long as needed, press F9 again, and receive one accurate, polished result in the application where dictation started.

The product should feel invisible during daily use:

- no account;
- no cloud transcription;
- no quota;
- no audio stored on disk;
- no speculative text that later changes;
- no need to speak punctuation commands for ordinary prose;
- no repeated correction of the same personal term;
- no complex control panel;
- no lost transcript when another component fails.

"Flawless" is a product goal, not a truthful guarantee. For this project it means that at least 95 percent of ordinary daily utterances are accepted without a manual edit after personalization, protected facts are never changed by the formatter, and silence never produces inserted text.

## 2. Why Option 2 is the final choice

Three possible directions were considered:

1. Keep the current streaming and rule-based pipeline. This is fast, but real speech is fragmented at thinking pauses and the cleanup rules can make the completed sentence less accurate.
2. Use one strong whole-utterance ASR pass, deterministic personal corrections, one optional local semantic formatter, and a strict validator. This maximizes final-text accuracy while keeping the product local and small.
3. Build a large multilingual ensemble with several ASR models, span-level language routing, extensive context collection, and multiple specialist services. This is technically interesting but too large for the immediate English dictation goal and the 4 GB GPU constraint.

Option 2 is selected because it addresses the measured failure, not an imagined feature gap. The user values correct completed English more than seeing unstable text while still speaking.

## 3. Current evidence and the problem being fixed

The existing application already proves the difficult desktop foundations:

- global F9 toggle;
- in-memory microphone capture;
- CUDA inference with CPU fallback;
- Silero voice activity detection;
- personal vocabulary biasing and deterministic `heard -> wanted` rules;
- spoken commands;
- tray, non-focus-stealing status pill, settings, and startup shortcut;
- duplicate-instance protection;
- sleep and GPU recovery handling;
- synthetic regression suites and a private real-voice evaluation harness.

Those parts should be retained unless a failing test proves they must change.

The latest private real-voice result is the baseline, not a public release claim. Its exact values remain in the gitignored `eval/private/results/` directory and can be reproduced locally with:

```text
python eval/bench_real.py --models small.en
```

The tracked repository must not quote private-corpus accuracy values that a public reviewer cannot reproduce. The aggregate and category evidence nevertheless identifies the architectural fault:

- fillers are handled well;
- ordinary speech is usable but not correction-free;
- false starts and self-corrections are made materially worse by the current cleanup;
- URLs, dates, numbers, and technical language remain weak;
- the current final cleanup is worse than the raw transcript overall in the private evaluation;
- pause-finalized streaming splits one intended sentence into several independent recognition and cleanup jobs.

Therefore v2 is not a cosmetic upgrade. It changes the unit of reasoning from each pause to the complete utterance.

## 4. Final top-level architecture

```text
F9 hotkey
   |
   v
Session controller captures target window and starts microphone
   |
   v
In-memory audio buffer
   |
   v
One VAD and background-speech safety gate
   |
   v
Whole-utterance ASR using the locally benchmarked model
   |
   v
Raw transcript, timing, and confidence evidence
   |
   v
Deterministic normalization and personal corrections
   |
   v
Protected-span masker
   |
   v
Optional local semantic formatter
   |
   v
Deterministic safety validator
   |                    |
   | accepted           | rejected, timed out, or unavailable
   v                    v
Validated result     Deterministic fallback
   |                    |
   +---------+----------+
             |
             v
Single insertion into the original target
             |
             v
Recovery record and explicit correction learning
```

There is no required network edge in this runtime graph. Downloads during installation are the only network requirement.

## 5. Non-negotiable design decisions

### 5.1 Whole utterance is the default accuracy unit

F9 release, not a thinking pause, ends the utterance. Internal pauses remain part of the same audio and language context.

Silero VAD may:

- reject a recording with no valid near-field speech;
- trim leading and trailing silence;
- calculate speech duration and quality warnings.

Silero VAD must not divide ordinary dictation into independently formatted phrases. The old streaming path remains temporarily available as an advanced experimental mode until v2 passes acceptance, but the shipped default is `stream = false`.

### 5.2 Text is inserted once

No partial hypothesis is typed into the target application. No background worker backspaces over text the user may have edited. The status pill may show activity, but target text appears only when the final result is accepted.

### 5.3 The acoustic transcript remains authoritative

The formatter is an editor, not an author. It may:

- add punctuation and capitalization;
- remove recognized filler words;
- collapse accidental immediate repetitions;
- resolve an explicit self-correction or abandoned start;
- apply a small application style profile.

It may not:

- invent a fact;
- answer the dictated text;
- summarize it;
- translate it;
- change sentiment, tense, negation, names, numbers, dates, money, addresses, URLs, email addresses, or identifiers;
- remove uncertain content merely to make the sentence sound smoother.

### 5.4 Personalization is explicit and reversible

The application learns only when the user chooses `Correct last dictation`. It never assumes that subsequent keyboard editing was a correction, and it never reads arbitrary document history to infer one.

Every learned rule has a source, date, scope, use count, enabled state, and delete action.

### 5.5 Accuracy decisions come from the real voice corpus

No model becomes the production default because it is newer, larger, or popular. It must beat the existing model on the same valid local recordings while satisfying latency and memory limits.

### 5.6 One laptop, no infrastructure

Oracle Free Tier, a website, a remote API, accounts, databases, queues, telemetry services, and cloud LLMs are outside the architecture. They add failure paths without helping home dictation accuracy.

## 6. Runtime session design

Each press-to-press dictation is one immutable session with a unique identifier.

```text
IDLE
  -> LISTENING
  -> FINALIZING_AUDIO
  -> TRANSCRIBING
  -> NORMALIZING
  -> FORMATTING
  -> VALIDATING
  -> INSERTING
  -> COMPLETE

Any processing state may move to RECOVERABLE_ERROR.
LISTENING may move to CANCELLED.
No completed state may return to an earlier state.
```

At session start the controller records:

- session identifier;
- monotonic start time;
- foreground window handle and a short diagnostic title;
- selected microphone identifier;
- active application category;
- current settings snapshot.

Audio remains a 16 kHz mono float32 buffer in memory. The callback performs no model work and no disk write. The existing five-minute hard limit remains. If the limit is reached, the normal finalization path runs so completed speech is not discarded.

A worker result may insert text only when its session identifier still matches the active completed session. This prevents a delayed worker from inserting into a later dictation.

## 7. Audio safety gate

Run voice analysis once and reuse the result. The gate returns:

```python
AudioEvidence(
    has_speech: bool,
    speech_seconds: float,
    trailing_silence_seconds: float,
    rms: float,
    near_field_likelihood: float | None,
    clipped_fraction: float,
    warnings: tuple[str, ...],
)
```

Required behavior:

1. No speech means no ASR call and no inserted text.
2. Very short low-energy clips are rejected before Whisper can invent subtitle-like text.
3. The learned noise gate may reject distant television or room speech only when calibration is valid.
4. If the learned gate rejects several consecutive clear recordings, it disables itself and reports the problem rather than silently making the app unusable.
5. VAD failure is visible in diagnostics. It must not silently claim a clean result.
6. Silence and noise tests must exercise the same function used by F9.

The current four silence recordings are insufficient for release. The corpus must contain at least 50 varied silence and household-noise clips before the zero-false-insertion gate can be trusted.

## 8. Recognition engine

### 8.1 Interface

The rest of the application sees one replaceable interface:

```python
RecognitionResult(
    text: str,
    language: str,
    segments: tuple[Segment, ...],
    average_log_probability: float | None,
    no_speech_probability: float | None,
    compression_ratio: float | None,
    model_name: str,
    device: str,
    compute_type: str,
    elapsed_seconds: float,
)

recognize(audio: np.ndarray, prompt: str | None) -> RecognitionResult
```

Recognition performs no cleanup, command execution, formatting, or typing.

### 8.2 Model selection

The existing private evaluation harness is the selection authority. Benchmark these already defined candidates sequentially on the same corpus:

- `small.en`, the production baseline;
- `distil-large-v3`;
- `large-v3-turbo`.

Do not download a model without explicit user approval. Do not change `settings.json` during a benchmark.

Selection order:

1. Eliminate a model if it does not fit with at least 600 MB of GPU headroom.
2. Eliminate it if any protected category materially regresses.
3. Eliminate it if post-stop latency exceeds the release budget.
4. Among the remaining models, choose the lowest real-voice raw WER.
5. Break a near tie using exact-utterance rate, protected-field accuracy, and resource use.

A larger model is not automatically selected. The old synthetic benchmark could not reveal an improvement because `small.en` already scored perfectly on that easy corpus. The private natural corpus is now mandatory.

### 8.3 Decode policy

The production path uses one high-quality deterministic decode. Initial values to test are:

- language fixed to English for the English profile;
- temperature `0`;
- beam size between `3` and `5`;
- condition on previous text disabled between independent F9 sessions;
- personal vocabulary prompt kept within the existing token budget;
- word timestamps enabled only if needed by confidence or correction logic.

These are experiment inputs, not claims of optimal values. Claude must record a comparison before changing a default.

An uncertainty-triggered retry may use the same resident model with a broader beam. A second resident ASR model is not part of the normal runtime. The retry is allowed only when confidence is poor, output is empty despite valid speech, or protected content appears malformed.

### 8.4 Resource behavior

The GPU path is warmed at startup. If CUDA initialization, inference, or post-sleep recovery fails, the application retries once on CPU and clearly shows degraded mode.

ASR and the local formatter must not independently assume all GPU memory is available. The resource coordinator chooses one of these measured configurations:

- ASR on GPU and formatter on CPU;
- sequential GPU residency if unload and reload latency remains acceptable;
- rules-only fallback when neither arrangement meets the latency budget.

Running two large models concurrently on a 4 GB GPU is prohibited.

## 9. Deterministic accuracy layer

This layer runs before any local LLM and remains a complete fallback product.

Order is fixed:

1. Unicode and whitespace normalization.
2. Exact `heard -> wanted` corrections.
3. Conservative vocabulary snapping.
4. Address, URL, email, and identifier joining.
5. Unambiguous standalone spoken commands.
6. Filler tagging, not deletion yet.
7. Protected-span detection.

Exact correction rules beat fuzzy rules. Fuzzy snapping must retain the existing collision tests and must never rewrite an ordinary word merely because it resembles a personal term.

Self-correction logic must see the whole utterance. It must not delete a clause based only on the presence of words such as `actually`, `sorry`, `no`, or `I mean`. These words are markers only when the surrounding structure provides evidence of a replacement.

## 10. Protected spans

Protected spans are extracted after personal correction and before semantic formatting.

Protected categories:

- personal and company names in the local lexicon;
- all numbers and numeric ranges;
- currency values and percentages;
- dates and times;
- negations;
- postal addresses;
- URLs and email addresses;
- filenames, paths, command fragments, code identifiers, and abbreviations.

Each span is replaced with an opaque placeholder before formatter inference:

```text
Raw:      Send EUR 1,250 to Tejas by 14 September.
Masked:   Send <P0> to <P1> by <P2>.
Required: <P0>, <P1>, and <P2> each occur once and in the same order.
```

The placeholder map exists only for the current session. Restoration is deterministic. If any placeholder is missing, duplicated, reordered in a meaning-changing way, or modified, formatter output is rejected.

Protection prevents a text model from changing correctly recognized facts. It cannot repair a number the ASR heard incorrectly. Recognition accuracy for those categories must therefore remain a separate release metric.

## 11. Local semantic formatter

### 11.1 Role

Use one local text model through the existing loopback-only formatter adapter. The model is optional at runtime but is part of the selected Smart experience when it passes evaluation.

It receives only:

- the masked transcript;
- a fixed editing instruction;
- the active application category;
- a small set of retrieved correction examples;
- an explicit output schema.

It does not receive screenshots, unrelated windows, full documents, browser history, Career Center files, or remote context.

### 11.2 Allowed operation

The formatter performs one task:

> Convert this spoken transcript into faithful written English. Preserve meaning and every placeholder. Remove only clear fillers and abandoned starts. Resolve only explicit self-corrections. Return the edited text and no answer or commentary.

Use temperature `0` and a strict timeout. Prefer structured JSON if the chosen local runtime returns it reliably:

```json
{
  "text": "the edited text",
  "operations": ["punctuation", "explicit_self_correction"]
}
```

### 11.3 Routing

The formatter is disabled for:

- secure fields;
- terminals and command prompts;
- utterances dominated by paths, URLs, email addresses, or code;
- very short command-like text;
- an unavailable or unhealthy local model;
- resource contention that would exceed the latency budget.

Those cases use deterministic output.

### 11.4 Failure behavior

The formatter can never cause loss of dictation. Timeout, malformed output, model unavailability, excessive length change, placeholder damage, or failed validation returns the deterministic candidate immediately.

No remote LLM fallback is permitted.

## 12. Safety validator

The validator compares four artifacts:

1. raw ASR transcript;
2. deterministic candidate;
3. protected-span map;
4. formatter candidate.

The formatter candidate is accepted only if all checks pass:

- output is valid and contains only the requested result;
- every placeholder occurs exactly once;
- all protected values restore exactly;
- negation count and identity are preserved;
- no new proper noun, number, URL, email, or code token appears;
- length remains inside the category-specific envelope;
- content-word additions are zero unless they come from an approved correction rule;
- deletions are explainable as tagged fillers, exact repetitions, or an explicit abandoned clause;
- normalized edit distance remains below the measured safety threshold;
- the output does not resemble an answer, refusal, heading, or model commentary.

Validation is deterministic and locally testable. It does not ask a second LLM whether the first LLM was safe.

The fallback hierarchy is:

```text
validated formatter result
    else deterministic corrected result
        else raw ASR transcript
            else recoverable error with no insertion
```

## 13. Application-aware formatting without surveillance

Only the foreground executable and focused control type are required. Classify the target into a small fixed set:

- `chat`;
- `email`;
- `document`;
- `search`;
- `code_or_terminal`;
- `unknown`;
- `secure`.

The category changes punctuation and formatting conservatively:

| Category | Behavior |
|---|---|
| Chat | Natural punctuation, contractions allowed, no formal rewriting |
| Email | Complete sentences and paragraph breaks when spoken |
| Document | Standard prose punctuation |
| Search | Minimal punctuation and no filler expansion |
| Code or terminal | Verbatim deterministic mode, no semantic formatter |
| Unknown | Document defaults with no surrounding-context collection |
| Secure | Do not insert and do not retain transcript text |

Version 2 does not read surrounding document text. That can be reconsidered only after the core no-edit target is met and a specific measured failure requires it.

## 14. Insertion and recovery

The insertion broker owns all interaction with the target application.

Required sequence:

1. Save the final text to the bounded local recovery record.
2. Confirm that the original window still exists.
3. Restore focus to it without activating the status pill.
4. Insert the text once using the safest available Windows method.
5. Confirm that the insertion command was issued.
6. Mark the recovery record complete.

Use direct keyboard typing for simple ASCII fields only when verified. Use a clipboard-preserving paste path for Unicode or applications where simulated typing loses characters. The user's previous clipboard contents must be restored after insertion.

If the original target cannot be restored, do not type into whichever window happens to be focused. Copy the final result to the clipboard, show `Target lost - text copied`, and keep it in recovery history.

Store no audio. Keep a bounded local history of raw and final text only for recovery and correction learning. The normal settings UI must offer `Clear history` and a way to disable transcript history while retaining the last recoverable item in memory.

## 15. Correction learning: the differentiating loop

The product goal is not merely lower first-pass WER. It is that the same personal mistake should not require the same correction again.

### 15.1 User flow

1. The last dictation is inserted.
2. If it is wrong, the user chooses `Correct last dictation` from the tray or its configurable hotkey.
3. A small window shows the raw transcript and an editable final result.
4. The user enters the intended text and selects `Apply and learn`.
5. The app replaces the last insertion only when the original target and text position can be verified. Otherwise it copies the corrected text and explains why automatic replacement was unsafe.
6. The app proposes any reusable mapping it inferred. The mapping is enabled only after explicit confirmation.

### 15.2 Stored correction

Use a versioned local JSON file, written atomically:

```json
{
  "version": 1,
  "rules": [
    {
      "id": "local-id",
      "heard": "flow code",
      "wanted": "Claude Code",
      "scope": "global",
      "source": "explicit_user_correction",
      "created_at": "local ISO timestamp",
      "use_count": 0,
      "enabled": true
    }
  ]
}
```

Global mappings are limited to bounded word or phrase substitutions. Long sentence rewrites are stored as local examples for formatter retrieval, not applied as global search-and-replace rules.

The correction screen must support disable, edit, and delete. A correction that causes a regression can therefore be removed without editing source files.

### 15.3 Retrieval

Before formatting, retrieve at most three relevant user-approved examples using deterministic token overlap. No vector database and no embedding service are needed. If no example is clearly relevant, send none.

## 16. Minimal user interface

The tray icon is the application. Left-click opens Settings. Right-click exposes:

- Start or stop dictation;
- Correct last dictation;
- Personal words and corrections;
- Settings;
- Quit.

The pill appears only during active work and never takes keyboard focus:

```text
Listening -> Processing -> Inserted
                    \-> Needs attention
```

Normal settings contain only:

1. Hotkey.
2. Microphone.
3. Writing style: Exact, Clean, or Smart.
4. Personal words and corrections.
5. Start with Windows.
6. Clear local history.

Advanced settings contain model, device, VAD, diagnostics, and the legacy streaming experiment. Pause timing must disappear from normal settings because pause chunking is no longer the default product.

First launch opens Settings once and explains F9 in one sentence. Later launches remain silent in the tray.

## 17. Configuration defaults

The final default behavior is:

```json
{
  "hotkey": "f9",
  "language": "en",
  "quality": "accurate",
  "model": "small.en",
  "device": "auto",
  "stream": false,
  "writing_style": "smart",
  "formatter": "local",
  "vocab": true,
  "fuzzy": true,
  "commands": true,
  "noise_gate": true,
  "overlay": true,
  "run_at_login": false
}
```

Production remains on `small.en` until the same-corpus benchmark proves that another concrete model is better and still meets the resource and latency gates.

Environment variables may remain for tests and diagnostics, but ordinary users should never need them.

## 18. Lean internal module boundaries

Avoid a framework rewrite. Keep the working desktop shell and add only two focused modules.

| File | Final responsibility |
|---|---|
| `dictate.py` | Composition root, hotkey loop, microphone lifecycle, session state, UI events |
| `dictate_core.py` | VAD evidence, ASR adapter, vocabulary, deterministic corrections, GPU fallback |
| `dictate_finalizer.py` | Protected spans, local formatter routing, validation, fallback selection |
| `dictate_learning.py` | Correction storage, rule proposal, retrieval, and correction-session data |
| `dictate_polish.py` | Small deterministic cleanup functions and loopback local-model client |
| `dictate_stream.py` | Legacy experimental streaming only, disabled by default |
| `dictate_config.py` | Versioned settings schema and migration |
| `dictate_settings.py` | Minimal settings and correction-management windows |
| `dictate_tray.py` | Discoverable tray actions |
| `dictate_overlay.py` | Non-focus-stealing status only |
| `eval/` | Private real-voice recording, scoring, and model comparison |
| `tests/` | Deterministic unit, integration, and regression coverage |

Do not create service containers, repositories, event buses, plugin systems, web dashboards, or databases for this version.

## 19. Data boundaries

| Data | Storage | Retention |
|---|---|---|
| Audio | Memory only | Destroyed after the session |
| Settings | `settings.json` | Until changed or reset |
| Manual vocabulary | `vocabulary.txt` | Until user edits it |
| Learned corrections | `corrections.json` | Until disabled, deleted, or reset |
| Recovery text | bounded local JSONL | Configurable, capped by count and bytes |
| Diagnostic events | rotating local log | Capped |
| Private evaluation audio | `eval/private/` | User-controlled and gitignored |
| Nearby document context | Not collected in v2 | None |

Secrets, private Career Center material, recordings, transcripts, model caches, settings, and evaluation output must remain untracked. `.gitignore` is an acceptance requirement, not documentation advice.

## 20. Failure policy

| Failure | Required result |
|---|---|
| No speech or room noise | Insert nothing and return to idle |
| GPU unavailable | Retry ASR once on CPU and show degraded status |
| ASR fails on both devices | Preserve no partial guess, show recoverable error |
| Formatter unavailable or slow | Use deterministic candidate immediately |
| Formatter changes protected content | Reject it and use deterministic candidate |
| Target window disappears | Copy final text, never type into another window |
| Clipboard insertion fails | Restore clipboard and retain recovery item |
| Correction file is malformed | Quarantine it, load no learned rules, keep dictation working |
| Settings file is malformed | Load safe defaults and report the reset |
| App starts twice | Refuse the second instance visibly |
| Laptop resumes from sleep | Rebuild unsafe audio and CUDA resources before accepting F9 |

No optional feature may take down basic transcription.

## 21. Evaluation and release gates

### 21.1 Three separate accuracy layers

Always report:

- raw WER against literal speech;
- deterministic-corrected WER against literal speech;
- final WER against intended written text.

Never hide a weak recognizer behind cleanup. Never claim improvement from a single aggregate when a protected category regressed.

### 21.2 Required corpus

The private real-voice corpus must include:

- ordinary messages;
- fast speech;
- long natural thoughts;
- names and companies;
- dates and times;
- numbers and money;
- URLs and email addresses;
- technical language;
- fillers;
- false starts;
- explicit self-corrections;
- quiet speech;
- fan noise;
- distant television or room voices;
- at least 50 silence and household-noise clips.

Synthetic Windows voices remain regression fixtures only.

### 21.3 Pre-release gates

All gates are measured on Tejas's laptop and voice:

| Gate | Required result |
|---|---:|
| Silence or household noise inserted as text | 0 cases |
| Final WER compared with raw WER | Final must be lower, never higher overall |
| Ordinary-speech raw WER | 10% or lower |
| Final WER | 8% or lower |
| Exact final utterances in the balanced corpus | 80% or higher |
| Protected names | 95% or higher |
| Protected numbers, dates, and money | 95% or higher |
| Formatter protected-span mutations | 0 in the full corpus |
| False-start and self-correction final WER | 15% or lower in each category |
| GPU post-stop latency for an utterance up to 15 seconds | p95 at or below 2.5 seconds |
| Target-window insertion failures | 0 in 100 automated focus-change trials |
| Existing regression suites | 100% passing |

These are release gates, not current claims. If the model cannot reach them on this hardware, report the gap and keep the safest best-performing configuration. Do not manipulate the corpus or expected text to create a pass.

### 21.4 Daily-use acceptance

After laboratory gates pass, use the application for at least seven real days and at least 200 non-trivial utterances.

Track locally:

- utterances accepted without correction;
- corrected characters per 100 dictated characters;
- repeated errors after an approved correction;
- median and p95 post-stop latency;
- formatter rejection and timeout rate;
- target-loss recovery events.

Final product acceptance requires:

- at least 95% of eligible utterances accepted without editing;
- no protected-fact corruption;
- no recurrence of an exact approved correction in its valid scope;
- no lost transcript;
- user preference for Dictate Local over returning to manual typing during the trial.

## 22. Test additions

Keep all existing regression coverage. Add focused suites:

| Test file | Proof required |
|---|---|
| `tests/test_whole_utterance.py` | Thinking pauses never cause intermediate insertion |
| `tests/test_finalizer.py` | Formatter routing and fallback order are exact |
| `tests/test_protected.py` | Names, numbers, dates, negations, addresses, URLs, and identifiers round-trip unchanged |
| `tests/test_validator.py` | Hallucination, answer-like output, placeholder damage, and unsafe deletion are rejected |
| `tests/test_learning.py` | Only approved corrections are stored, scoped, retrieved, disabled, and deleted |
| `tests/test_recovery.py` | Target loss and insertion failure preserve the final text safely |
| `tests/test_quality_gate.py` | Aggregate and category regressions fail the release check |

Tests must call the same public functions used by the F9 path. A unit test for an unused helper is not runtime proof.

## 23. Implementation sequence

Each phase must leave the current app usable and must be independently reviewable.

### Phase 0: Freeze and reproduce the baseline

- Preserve the latest aggregate result and command used to produce it.
- Run all current deterministic suites.
- Repair the suite runner if it does not invoke every advertised test.
- Add a test proving the current production setting is not changed by benchmarking.

Exit: the baseline is reproducible and no current user setting or private corpus is committed.

### Phase 1: Make whole-utterance finalization the production path

- Change the default to `stream = false`.
- Introduce explicit session identifiers and monotonic state transitions.
- Run VAD once per completed recording.
- Insert exactly once after F9 release.
- Retain legacy streaming only behind Advanced settings.

Exit: natural pauses produce no intermediate typing and the existing batch path passes end-to-end tests.

### Phase 2: Select the recognizer by evidence

- Extend model metadata for all three candidates.
- Benchmark cached candidates first.
- Ask before downloading missing weights.
- Compare aggregate, category, latency, RAM, and VRAM results.
- Select a production model only when every release constraint is met better than the baseline.

Exit: one written comparison identifies the winner or truthfully retains `small.en`.

### Phase 3: Build the safe hybrid finalizer

- Add protected-span masking and restoration.
- Refactor the local formatter behind one adapter.
- Add deterministic validation and fallback.
- Route terminals, secure fields, and structured text away from semantic cleanup.
- Measure raw, deterministic, and final output separately.

Exit: final WER no longer exceeds raw WER and the formatter has zero protected-span mutations in the corpus.

### Phase 4: Add explicit correction learning

- Add atomic versioned correction storage.
- Add `Correct last dictation` to tray and settings.
- Propose only bounded reusable mappings.
- Require explicit confirmation.
- Add disable, edit, delete, and reset.
- Retrieve no more than three relevant approved examples.

Exit: an approved correction is applied on a repeated test phrase and an unapproved edit is never learned.

### Phase 5: Finish the minimal product surface

- Make Settings discoverable by left-clicking the tray icon.
- Reduce normal controls to the six listed in Section 16.
- Move model and VAD tuning into Advanced.
- Add visible copied-to-clipboard and degraded-mode states.
- Add first-run F9 guidance.

Exit: a new user can discover how to dictate, correct, configure, and recover without reading the source code.

### Phase 6: Acceptance and simplification

- Run every deterministic test.
- Run the full private real-voice benchmark.
- Run focus-change integration trials.
- Complete the seven-day local use trial.
- Remove dead branches and settings only after evidence shows they are no longer needed.
- Update README and BUILD_LOG from measured results only.

Exit: every release gate is reported as pass or gap. No unmeasured accuracy claim is published.

## 24. Multilingual path after English acceptance

Marathi, Hindi, Hinglish, and Marathi-English remain valuable, but they are not allowed to destabilize the English release.

The extension path is:

1. Add a language profile to the recognition interface.
2. Benchmark multilingual Whisper candidates on a separately recorded local corpus.
3. Add Unicode insertion before semantic formatting.
4. Preserve English protected entities inside Devanagari or Roman output.
5. Add script selection and transliteration only after native-script ASR is measured.
6. Add a specialist Indic recognizer only if it materially improves a defined failing category.

No current English module should assume ASCII text, but no multilingual model, transliterator, or second ASR service belongs in the English v2 build merely for future-proofing.

## 25. Explicit non-goals

Do not build:

- cloud inference or Oracle hosting;
- a web account or subscription system;
- a mobile app;
- meeting transcription or speaker diarization;
- continuous microphone listening;
- screenshot understanding;
- document-wide context collection;
- automatic translation;
- several simultaneous ASR models;
- a vector database;
- an agent framework;
- a plugin marketplace;
- a custom model-training pipeline;
- a large installer containing every possible model;
- speculative text replacement while the user is still speaking.

## 26. Claude Code execution contract

Claude Code can build this, but it must implement this file as an evidence-gated upgrade to the existing application, not start a new project.

Use this prompt only after reviewing this architecture:

```text
Work in the existing dictate-local repository.

Read AGENTS.md and ARCHITECTURE.md completely before changing anything. ARCHITECTURE.md is the final source of truth. The older multilingual design is superseded research, not the current implementation plan.

Implement the Option 2 lean hybrid finalizer phase by phase, following Section 23 in order. Preserve all working desktop behavior and all user changes. Do not rewrite the application from scratch.

Hard requirements:
- Keep runtime inference local and unmetered. No cloud API, Oracle dependency, telemetry, account, or subscription.
- English-first release. Do not implement the multilingual roadmap in this pass.
- Whole-utterance finalization is the default. Never type speculative partial text or revise text the user may have edited.
- Use the real-voice evaluation harness as the model and accuracy authority. Keep raw, corrected, and final scores separate.
- Do not download model weights without asking first.
- Do not change the production model unless the same-corpus benchmark proves the change.
- Treat names, numbers, dates, money, negations, addresses, URLs, emails, paths, and identifiers as protected spans.
- A local formatter is optional and must fail back to deterministic output on timeout, invalid output, or any safety-check failure.
- Learn only from an explicit Correct last dictation action. Every learned rule must be reversible.
- Never read, commit, or expose private Career Center files, private evaluation audio, transcripts, settings, model caches, secrets, or local result files.
- Never use an em dash in code, documentation, tests, output, or commit messages.
- Use test-driven development for each behavior change.
- Run the relevant focused test after every change and the complete suite at every phase boundary.
- Do not claim completion until Section 21 gates have been measured. Report unmet gates plainly.

Before implementation, inspect the working tree and preserve unrelated changes. Produce a concise phase plan naming exact files and tests. Then implement Phase 0 only, show its test and benchmark evidence, and stop for review before Phase 1. Do not commit or push unless explicitly asked.
```

The first Claude session should perform Phase 0 only. This prevents a large autonomous rewrite from hiding baseline errors or destroying the evidence needed to judge the upgrade.

## 27. Final decision

Yes, build this architecture.

The winning product is not the one with the most models or features. It is the smallest system that repeatedly turns Tejas's natural English into trustworthy final text without correction, keeps every byte local, and learns only what Tejas deliberately teaches it.
