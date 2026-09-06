# Dictate Local v2: Multilingual Accuracy Engine

**Status:** Proposed architecture for review

**Date:** 6 September 2026

**Product boundary:** Single-user Windows dictation, local after initial model download, no subscription, no usage quota, no required server

**Primary speech:** English, Marathi, Hindi, Hinglish, and Marathi-English code-switching
**Reference experience:** The speed, correction handling, contextual formatting, and "dictate anywhere" behavior associated with Wispr Flow, implemented with local and open components rather than proprietary services

## 1. Executive decision

This system is feasible, but not as a model replacement inside the current English-only pipeline.

The proposed product is a small Windows application with a layered local accuracy engine:

1. Capture clean audio and detect speech boundaries.
2. Produce one or more multilingual transcription candidates.
3. Detect language and script at span level, not once for the entire utterance.
4. Resolve names, code-switched words, and transliteration using local context and a personal lexicon.
5. Interpret false starts and spoken corrections without changing meaning.
6. Format for the active application.
7. Validate the result against the acoustic transcript.
8. Insert Unicode safely into the original field.
9. Learn only from corrections the user explicitly accepts.

The product remains unlimited because every inference path runs locally. Network access is needed only to install the application and download models. No Oracle instance, API key, account, quota, or subscription is required.

The architecture does not promise numerical parity with a proprietary service before testing. It defines an evidence path to reach or exceed that service on Tejas's own voice, vocabulary, and daily applications.

## 2. Product promise

The user presses or holds one hotkey, speaks naturally in English, Marathi, Hindi, or a mixture, and receives polished text in the active application.

The software must:

- work after installation with the network disconnected;
- have no transcription quota;
- preserve English terms inside Marathi and Hindi speech;
- support Devanagari, Latin, and mixed-script output;
- understand common self-corrections such as "actually," "no," "sorry," "नाही," and "म्हणजे" only when they function as edits;
- recognize personal names, company names, abbreviations, filenames, and technical vocabulary;
- adapt punctuation and layout to the active application;
- never silently translate unless the user explicitly selected translation;
- never silently learn an uncertain correction;
- never lose a completed transcript because insertion failed;
- degrade to a simpler local path if a model, GPU, or application integration fails.

## 3. What "like Wispr" means in this design

The comparison is behavioral, not a claim to reproduce proprietary internals.

Required behavioral parity:

| Capability | Required local behavior |
|---|---|
| Dictate anywhere | Insert into focused Windows text fields across common applications |
| Fast completion | Final text appears quickly enough to preserve conversational flow |
| Natural speech | Remove clear fillers and false starts without requiring command syntax |
| Backtracking | Resolve "Tuesday, actually Wednesday" into the corrected statement |
| Context awareness | Use nearby text, application category, names, and code identifiers |
| Personal vocabulary | Boost terms and maintain deterministic correction rules |
| Style awareness | Format differently for email, messaging, documents, search, and code |
| Recovery | Preserve text if the target field disappears or insertion fails |
| Privacy control | Keep audio, text, context, and correction data on the device |
| No quota | Run indefinitely without a hosted inference dependency |

Wispr publicly describes context-conditioned ASR, learning from corrections, context awareness, and LLM-based formatting. Those capabilities explain the target experience. They do not establish which internal models or algorithms must be copied.

## 4. Scope

### 4.1 In scope

- Windows 10 and Windows 11.
- One user on one computer.
- English speech.
- Marathi speech.
- Hindi speech.
- Hinglish, meaning Hindi and English code-switching.
- Marathi-English code-switching.
- Devanagari Marathi and Hindi output.
- Romanized Marathi and Hindi output.
- Mixed-script output such as `आज client meeting आहे`.
- Local vocabulary and correction learning.
- Application-aware punctuation and layout.
- Recovery history sufficient to prevent lost dictations.
- CPU fallback and low-memory operating modes.
- Existing F9 toggle behavior plus an optional hold-to-talk interaction.

### 4.2 Explicit non-goals for v2

- Mobile applications.
- Multi-user accounts.
- Cloud sync.
- Meeting transcription.
- Speaker diarization.
- Translation as a default behavior.
- Training a foundation speech model from scratch.
- Continuously recording the microphone.
- Reading screenshots.
- Sending screen context to any external service.
- Supporting all Indian languages in the first release.
- Replacing the Windows input-method editor for arbitrary text composition.

The internal interfaces must allow additional Indian languages later, but English, Marathi, Hindi, and their code-switched forms are the release boundary.

## 5. User-visible modes

The application should expose few controls. Complexity belongs inside the engine.

### 5.1 Speech mode

Default: `Auto: English + Marathi + Hindi`

Optional choices:

- English only
- Marathi only
- Hindi only
- English + Marathi
- English + Hindi
- Auto: English + Marathi + Hindi

These choices are priors, not rigid filters. Auto mode may use more computation. A fixed mode lowers ambiguity and latency when the user knows what will be spoken.

### 5.2 Output script

Default: `Follow context`

Choices:

- Follow context
- Devanagari
- Roman
- Preserve mixed speech

Rules:

- Follow context uses nearby text and the application profile.
- Devanagari writes Marathi and Hindi in Devanagari while retaining protected English terms in Latin script.
- Roman transliterates Marathi and Hindi words into Latin characters.
- Preserve mixed speech retains the selected candidate's script with minimal normalization.

Examples:

| Spoken intent | Follow-context output in English chat | Follow-context output in Marathi document |
|---|---|---|
| Marathi plus English | `Aaj client meeting aahe.` | `आज client meeting आहे.` |
| Hindi plus English | `Kal deployment complete karna hai.` | `कल deployment complete करना है.` |
| English name in Marathi | `Tejas ला document पाठव.` | `Tejas ला document पाठव.` |

The system must not transliterate protected entities such as `Zalando`, `PitchBook`, `GitHub`, `Qwen`, or identifiers unless the user explicitly created a native-script alias.

### 5.3 Cleanup level

- Verbatim: transcription plus required Unicode normalization only.
- Clean: punctuation, capitalization, obvious filler removal, and explicit corrections.
- Smart: Clean plus semantic backtracking, lists, and application formatting.

Default: `Smart`, with a one-action "Undo smart edit" recovery path.

### 5.4 Trigger behavior

- Toggle: press once to begin and once to stop. This preserves the existing daily-use behavior.
- Hold: hold the configured hotkey, then release to finish.
- Hands-free: double-press in Hold mode to enter a bounded toggle session.

Only one trigger mode is shown in normal settings. Hands-free is documented as an interaction, not a separate settings category.

## 6. Top-level architecture

```text
┌────────────────────────────────────────────────────────────────────┐
│                         Windows desktop app                        │
│                                                                    │
│  Hotkey + mic                                                      │
│      │                                                             │
│      v                                                             │
│  Audio Front End -> Segmenter -> Recognition Coordinator           │
│                                      │                             │
│                         ┌────────────┴────────────┐                │
│                         v                         v                 │
│                General multilingual ASR   Indic specialist ASR     │
│                         └────────────┬────────────┘                │
│                                      v                             │
│                             Candidate Lattice                      │
│                                      │                             │
│      Active app + nearby text ------>│<------ Personal lexicon     │
│                                      v                             │
│                    Language, entity, and script resolver           │
│                                      │                             │
│                                      v                             │
│                     Correction and formatting engine               │
│                                      │                             │
│                                      v                             │
│                         Semantic safety validator                  │
│                                      │                             │
│                                      v                             │
│                         Unicode insertion broker                   │
│                                      │                             │
│                                      v                             │
│                        Recovery + reviewed learning                │
└────────────────────────────────────────────────────────────────────┘

No required network path exists after model installation.
```

## 7. Core architectural principles

### 7.1 Span-level language decisions

An utterance such as `आज client meeting reschedule करूया` is not Marathi or English as one label. It contains Marathi spans and English spans. A single utterance-level language decision will force one part into the wrong script or vocabulary.

The internal representation must carry a language and script hypothesis for each token span:

```json
{
  "surface": "client meeting",
  "language": "en",
  "script": "Latin",
  "start_ms": 410,
  "end_ms": 1080,
  "confidence": 0.91,
  "protected_entity": false
}
```

Language labels are evidence used by the resolver. They are not immutable truth.

### 7.2 Acoustic evidence remains authoritative

The formatter may reorganize, punctuate, remove clear fillers, and apply explicit self-corrections. It may not add facts that have no acoustic or contextual support.

Names, numbers, dates, negations, currencies, URLs, email addresses, and code identifiers are protected spans. Any change to a protected span requires either:

- an exact personal correction rule;
- a higher-confidence ASR candidate;
- an explicit spoken correction;
- or explicit user confirmation.

### 7.3 Uncertainty spends computation

Easy phrases use one fast pass. Difficult phrases receive more work.

```text
high confidence -> fast deterministic path
medium confidence -> context and lexicon reranking
low confidence -> specialist or stronger second ASR pass
unsafe disagreement -> preserve conservative transcript and flag locally
```

This makes better use of a 4 GB GPU than running every model on every utterance.

### 7.4 No required LLM

The application must work with the formatter unavailable. Deterministic transcription, vocabulary correction, commands, and insertion remain a complete fallback product.

### 7.5 Personalization is reversible

Every learned item records its source and can be disabled or deleted. A single accidental edit must never permanently poison future output.

## 8. Detailed component design

### 8.1 Session controller

Responsibilities:

- register hotkeys;
- create one dictation session at a time;
- snapshot the target window and caret anchor;
- start and stop microphone capture;
- enforce duration and memory limits;
- coordinate cancellation;
- preserve a recovery record until insertion succeeds;
- publish states to the existing pill and tray.

Session states:

```text
IDLE
LISTENING
FINALIZING_AUDIO
TRANSCRIBING
RESOLVING
FORMATTING
VALIDATING
INSERTING
COMPLETE
RECOVERABLE_ERROR
CANCELLED
```

State transitions must be monotonic for one session. Each session receives a random identifier so delayed worker results cannot be inserted into a newer session.

### 8.2 Audio front end

Inputs:

- selected microphone;
- 16 kHz mono float audio;
- current calibration profile.

Processing:

1. Convert device format to 16 kHz mono float32.
2. Remove DC offset.
3. Apply a conservative high-pass filter.
4. Estimate signal-to-noise ratio.
5. Apply optional local denoising only when the measured profile proves it helps.
6. Run VAD.
7. Track clipping, silence, device changes, and queue growth.

Do not apply aggressive automatic gain control by default. It can amplify fans, televisions, and distant speakers. Microphone-specific calibration should store expected near-voice energy, noise floor, and clipping threshold.

Output contract:

```python
AudioSession(
    pcm: Float32Array,
    sample_rate: 16000,
    speech_regions: list[TimeRange],
    snr_db: float | None,
    clipped_fraction: float,
    device_id: str,
    warnings: list[str],
)
```

### 8.3 Segmenter

The segmenter separates acoustic boundaries from semantic boundaries.

- Short silence can stabilize a partial result.
- Longer silence can end a clause.
- Hotkey release ends the utterance.
- A language switch must not automatically end a segment.
- A 300-second hard limit prevents unbounded memory use.

For streaming mode, only prefixes agreed upon by repeated recognition passes may be committed. Unstable suffixes remain inside the pill and are never typed. The current LocalAgreement-2 principle remains valid.

For maximum accuracy, final formatting waits until hotkey release or a confirmed clause boundary. This matches the user's expectation that text can be corrected before insertion.

### 8.4 Context collector

The context collector runs when dictation begins and refreshes once at completion if the target still exists.

Collected locally:

- process executable name;
- top-level window class;
- window title;
- browser domain when safely available through accessibility metadata;
- focused control type;
- up to a bounded number of characters before and after the caret;
- selected text;
- nearby names and capitalized terms;
- for supported IDEs, open filename and nearby identifiers;
- keyboard layout and active input language;
- whether the field is password-protected or security-sensitive.

Not collected in v2:

- screenshots;
- entire documents;
- browser history;
- unrelated windows;
- password fields;
- data from applications on the deny list.

Context lifetime:

- raw context is held in memory for one dictation;
- it is discarded after validation and insertion;
- only non-sensitive derived signals may be stored, such as `app_category=email`;
- surrounding text is never added to transcript history.

Prompt-injection boundary:

Screen text is untrusted data. It must be placed in a delimited data field and never concatenated as instructions. The formatter receives a fixed task schema. Even then, deterministic output validation is mandatory because prompt delimiting is not a complete security boundary.

### 8.5 Application classifier

Categories:

- `email`
- `work_message`
- `personal_message`
- `document`
- `code_editor`
- `ai_prompt`
- `search`
- `terminal`
- `unknown`
- `secure`

Classification should be deterministic from process, control type, and browser domain. It must not require an LLM.

Unknown applications use the `document` punctuation policy but disable context reading beyond the caret buffer.

Secure fields disable dictation insertion and show a clear local warning. Terminal fields default to Verbatim mode because semantic rewriting can change commands.

### 8.6 Recognition coordinator

This component owns model selection, language priors, retries, and candidate generation. It exposes one interface to the rest of the application:

```python
recognize(audio_session, speech_mode, context_hints) -> RecognitionBundle
```

It does not perform formatting or insertion.

#### 8.6.1 Candidate model families

**General candidate: multilingual Whisper**

The first models to benchmark are `small`, `medium`, and `large-v3-turbo`, not their `.en` variants. Whisper large-v3-turbo supports 99 languages and has 809 million parameters. It is a pruned large-v3 with fewer decoding layers, trading a small amount of quality for speed.

Why it is the primary candidate:

- one model can hear English, Hindi, and Marathi;
- it has broad accent and code-switch tolerance;
- faster-whisper already fits the current architecture;
- large-v3-turbo has reported int8 memory use around 1.5 GB in a community benchmark, which makes a 4 GB GPU trial plausible but not guaranteed.

Risks:

- uneven performance across low-resource languages;
- hallucination on silence and noisy audio;
- language detection instability on short code-switched clips;
- transliteration behavior may not match the desired script policy.

**Indic specialist candidate: AI4Bharat IndicConformer**

AI4Bharat publishes a 600M multilingual IndicConformer and monolingual models for all 22 official Indian languages, including Marathi and Hindi.

Why it is a specialist candidate:

- Indian-language training focus;
- separate Marathi and Hindi evaluation is possible;
- open implementation and model availability.

Risks:

- NeMo dependency weight and Windows packaging complexity;
- uncertain code-switched English behavior;
- different timestamps and confidence semantics;
- open issues around installation and model handling;
- running it concurrently with Whisper may exceed the intended resource budget.

It should enter the product only if the local corpus proves a material gain on difficult Marathi or Hindi spans.

**Rejected as the sole v2 model: Qwen3-ASR**

The current Qwen3-ASR model card lists Hindi but does not list Marathi. It may be tested for Hinglish research, but it cannot be the sole supported recognizer for the stated product.

#### 8.6.2 Model selection profiles

At first run, a benchmark wizard measures available RAM, GPU memory, cold-start time, real-time factor, and accuracy on a short guided corpus.

Profiles:

| Profile | Primary ASR | Second pass | Formatter | Intended hardware |
|---|---|---|---|---|
| Lite | Whisper small multilingual | None | Rules only or tiny text model | CPU or constrained GPU |
| Standard | Whisper large-v3-turbo int8 if verified | Same model with stronger decode | Qwen3 1.7B quantized on CPU | 4 GB NVIDIA GPU plus adequate RAM |
| Indic quality | Best measured Whisper | IndicConformer on uncertain Indic spans | Qwen3 1.7B quantized | Hardware that passes runtime budget |

The profile is selected by evidence. Model names remain replaceable through adapters.

#### 8.6.3 First-pass decoding

First-pass outputs must include:

- text;
- word or segment timestamps;
- language probabilities where available;
- average log probability;
- no-speech probability;
- compression ratio;
- temperature used;
- per-token confidence where available;
- decode duration and model identity.

The current fixed `language="en"` must be removed from Auto mode. Fixed-language modes may provide a strong language prior.

#### 8.6.4 Uncertainty score

The uncertainty score combines normalized signals:

- low acoustic confidence;
- disagreement between language windows;
- repeated text or high compression ratio;
- vocabulary near-miss;
- improbable script transition;
- entity mismatch with nearby context;
- number or negation ambiguity;
- high noise or clipping;
- disagreement between two decode settings;
- unusually short single-word audio.

No one threshold is trusted across models. Thresholds are calibrated on the local corpus.

Protected uncertainty includes:

- names;
- numbers;
- dates and times;
- negations such as `not`, `नाही`, and `नहीं`;
- currency and quantities;
- URLs and email addresses;
- file and variable names.

Any protected uncertainty can trigger a second pass even when aggregate confidence is high.

#### 8.6.5 Selective second pass

Second-pass order:

1. Retry the same recognizer with a longer context window and vocabulary prompt.
2. Retry with an alternate language prior if language evidence is split.
3. Use the Indic specialist only for a bounded uncertain span when available.
4. Compare candidates using acoustic confidence, context, lexicon, and script consistency.
5. If candidates remain unsafe, preserve the conservative primary transcript.

The second pass has a strict time budget. If it expires, the first safe candidate proceeds. A slow quality feature must never cause lost speech.

### 8.7 Candidate lattice

The recognition bundle stores alternatives instead of collapsing immediately to one string.

```python
RecognitionBundle(
    candidates: list[TranscriptCandidate],
    token_spans: list[TokenSpan],
    language_hypotheses: list[LanguageSpan],
    protected_spans: list[ProtectedSpan],
    acoustic_warnings: list[str],
    timings: StageTimings,
)
```

Candidate example:

```text
Audio: "आज client meeting reschedule करूया"

A: आज क्लायंट मीटिंग रिस्केड्यूल करूया
B: आज client meeting reschedule करूया
C: Aaj client meeting reschedule karuya
```

The resolver chooses B in a mixed-script work context, A in a Marathi-only document if the vocabulary does not protect the English terms, and C in a Roman chat context.

### 8.8 Personal lexicon

The existing flat vocabulary evolves into a local database while preserving text import and export.

Schema:

```text
lexicon_entry
  id
  canonical_form
  language
  script
  category
  protected
  priority
  enabled
  created_at
  updated_at

spoken_variant
  id
  lexicon_entry_id
  surface_form
  phonetic_key
  language_hint
  source
  success_count
  rejection_count

context_rule
  id
  lexicon_entry_id
  app_category
  nearby_terms
  boost

correction_rule
  id
  heard_form
  wanted_form
  language_context
  app_category
  source
  confidence
  enabled
```

Categories include person, company, place, acronym, product, code symbol, phrase, and ordinary word.

Priority order:

1. Exact user correction in matching context.
2. Protected personal term.
3. Nearby context entity.
4. Application-specific term.
5. General vocabulary boost.
6. Fuzzy match within a conservative distance.

Fuzzy correction must remain conservative. A multilingual collision corpus is required because edit-distance behavior differs across Devanagari grapheme sequences and Romanized forms.

### 8.9 Transliteration and script resolver

Transliteration is separate from translation and separate from speech recognition.

The resolver receives:

- ASR candidates;
- token-level language hypotheses;
- desired script mode;
- surrounding script distribution;
- personal lexicon;
- protected entities;
- application category.

For Marathi and Hindi words, it may create Devanagari and Roman alternatives. AI4Bharat IndicXlit is an optional candidate generator. It is a roughly 11M parameter local transliteration model supporting 21 Indic languages, including Marathi.

Because IndicXlit operates primarily at word level, it must not independently decide the final sentence. Contextual ranking resolves ambiguous Roman strings.

Examples of ambiguity:

- `kal` can correspond to `कल` with different meanings based on Hindi context.
- `kar` may be an English word, a name fragment, or an Indic verb form.
- `ahe` may be Marathi `आहे`, but should not be changed inside a URL or identifier.
- Marathi and Hindi share Devanagari but differ in vocabulary and grammar.

Normalization requirements:

- Unicode NFC normalization;
- consistent Devanagari combining marks;
- no insertion of zero-width characters unless required;
- preserve Latin identifiers exactly;
- preserve user-selected spelling even if a generic normalizer prefers another form;
- maintain spaces around mixed-script spans according to tested conventions.

### 8.10 Entity resolver

The entity resolver extracts candidate names from:

- personal lexicon;
- nearby text;
- window title;
- selected text;
- supported IDE symbols;
- prior user-confirmed corrections.

It generates phonetic and orthographic alternatives but does not overwrite without evidence.

Entity match score:

```text
acoustic similarity
+ exact context occurrence
+ personal priority
+ recent confirmed use
+ matching app category
- edit-distance risk
- collision with common word
- language mismatch
```

An entity visible near the cursor should receive a strong temporary boost but must disappear when the dictation ends.

### 8.11 Deterministic correction engine

This engine runs before any LLM formatter.

Responsibilities:

- explicit heard-to-wanted substitutions;
- safe near-miss snapping;
- protected entity casing;
- punctuation commands;
- line and paragraph commands;
- literal escape handling;
- repeated-fragment cleanup with exact guards;
- normalization of clear ASR artifacts;
- script-safe whitespace normalization.

Commands need language-specific grammars. Initial supported forms should be collected from the user corpus rather than guessed broadly.

Example command families:

| Intent | English examples | Hindi or Marathi candidates |
|---|---|---|
| Full stop | `full stop`, `period` | `पूर्णविराम` |
| New line | `new line` | `नई लाइन`, `नवीन ओळ` |
| New paragraph | `new paragraph` | `नया पैराग्राफ`, `नवीन परिच्छेद` |
| Undo phrase | `scratch that`, `remove that` | `ते काढ`, `वापस लो` |
| Literal | `literally` | configurable equivalent |

Every command must have ordinary-language negative tests. A phrase that contains the same words in a normal sentence must not execute.

### 8.12 Self-correction interpreter

This component detects edits spoken inside the utterance.

Input:

- ordered token spans with timestamps;
- pause lengths;
- correction markers;
- syntactic compatibility;
- semantic alternatives;
- protected spans.

Patterns:

```text
replacement:
  "Tuesday, actually Wednesday" -> choose Wednesday

restart:
  "Please send, no wait, please review the document" -> keep restarted clause

clarification:
  "Rahul, Rahul Deshmukh" -> prefer full clarified entity

non-correction use:
  "I actually liked it" -> preserve actually

Marathi correction:
  "उद्या, नाही परवा पाठव" -> keep परवा
```

The first implementation combines deterministic correction markers with a constrained formatter. It must not use a broad rule that deletes everything before `actually`, `नाही`, or `म्हणजे`.

### 8.13 Local formatter

The formatter converts a resolved transcript into application-appropriate text. It is an editor, not an author.

Default candidate: a quantized Qwen3 1.7B text model through a local runtime, because Qwen3 publicly lists Marathi, Hindi, and English among its supported languages. This is a candidate pending local instruction-following and latency tests.

The prompt is a fixed structured request:

```json
{
  "task": "edit_dictation",
  "cleanup_level": "smart",
  "app_category": "email",
  "script_policy": "follow_context",
  "raw_transcript": "...",
  "resolved_entities": ["..."],
  "protected_spans": ["..."],
  "nearby_text": "bounded untrusted context",
  "allowed_operations": [
    "punctuate",
    "capitalize",
    "remove_clear_filler",
    "apply_self_correction",
    "format_list"
  ]
}
```

Required response:

```json
{
  "text": "...",
  "operations": [
    {"type": "self_correction", "removed": "Tuesday", "kept": "Wednesday"}
  ],
  "protected_spans_preserved": true
}
```

Runtime rules:

- temperature 0;
- bounded output length;
- grammar-constrained JSON where the runtime supports it;
- no chain-of-thought output;
- no external tools;
- no network access;
- strict timeout;
- one retry only for malformed structure;
- deterministic fallback on any failure.

The model remains warm only if the memory governor permits it. Otherwise Smart mode may use a short-lived process or rules-only fallback.

### 8.14 Semantic safety validator

The validator is a required boundary between the formatter and insertion.

Checks:

1. Output is non-empty when input contains speech.
2. Length ratio stays inside corpus-calibrated bounds.
3. Every protected entity is preserved or changed by an authorized rule.
4. Numbers, dates, times, currencies, URLs, and negations are conserved.
5. Added content consists only of permitted punctuation, structure, or inflection required by an accepted correction.
6. Output language and script comply with the selected policy.
7. No model commentary, answer, heading, quotation wrapper, or explanation appears.
8. Repetition and hallucination detectors pass.
9. A semantic similarity score remains above the calibrated threshold.
10. Application-specific constraints pass, especially terminal and search modes.

Fallback ladder:

```text
validated Smart output
  -> deterministic Clean output
  -> resolved ASR output
  -> raw safest ASR candidate
```

The fallback reason is logged without storing sensitive surrounding context.

### 8.15 Unicode insertion broker

The current `keyboard.write()` path is not sufficient as the only mechanism for Devanagari and mixed-script text.

The insertion broker stores:

- original target process and window handle;
- focused control identity when available;
- caret or selection anchor;
- session identifier;
- completed text;
- insertion method attempted;
- recovery state.

Insertion order:

1. Use a supported accessibility or editor API when it can insert into the exact original field.
2. Use Win32 `SendInput` with Unicode events for controls that accept it.
3. Use a clipboard transaction for applications that require paste.
4. If the original field cannot be confirmed, do not type elsewhere. Show a recovery action and retain the text locally.

Clipboard transaction:

1. Snapshot current clipboard formats when reasonably safe.
2. Place the transcript as Unicode text.
3. Confirm the target window still matches.
4. Paste.
5. Verify insertion where accessibility APIs allow it.
6. Restore the previous clipboard after success.
7. If paste fails, keep the transcript available rather than restoring over the only recoverable copy.

Focus safety:

- the overlay remains non-activating;
- no insertion occurs if focus moved to a secure field;
- stale worker results cannot type into a new window;
- the user can choose "Insert here" from recovery after focusing a new field.

### 8.16 Recovery store

Audio is not retained by default.

Each completed dictation creates a temporary recovery record before insertion:

```text
session_id
created_at
final_text
raw_transcript
target_app_category
inserted_state
failure_reason
expiry
```

Records are encrypted with a key protected by Windows DPAPI. Successfully inserted records expire quickly unless history is enabled. Failed insertion records remain until recovered or explicitly dismissed, subject to a bounded count and age limit.

Transcript history is opt-in. Existing append-only plain-text logging should not remain the only recovery mechanism for multilingual private use.

### 8.17 Correction learning

Learning phases:

**Phase A: explicit only**

- User highlights or corrects a recent output.
- User invokes `Learn correction`.
- The app shows `heard -> wanted`, language, and optional app scope.
- User confirms.

**Phase B: suggested learning**

- For a bounded period after insertion, the app observes only the inserted span through accessibility APIs.
- A diff engine proposes a likely correction.
- Suggestions appear in a review queue.
- Nothing becomes active without approval.

**Phase C: high-confidence automatic application**

- Only repeated, approved patterns can become automatic.
- A rule needs multiple confirmations and no conflicting rejection.
- The user can inspect, disable, or delete every rule.

Never learn from:

- wholesale paragraph rewrites;
- deleted dictations;
- cursor movement into another field;
- formatting-only edits unless the learning category is style;
- password or secure fields;
- context that cannot be tied to the inserted span;
- one ambiguous edit.

## 9. Resource and model manager

The target machine has an RTX 3050 Laptop GPU with 4 GB VRAM. GPU memory is shared with other local models and desktop applications.

Responsibilities:

- detect GPU, free VRAM, system RAM, CPU capabilities, and current contention;
- load only models allowed by the selected profile;
- warm models at startup when memory permits;
- serialize GPU inference by default;
- move the formatter to CPU when that preserves ASR latency;
- retry a failed utterance on CPU without losing audio;
- rebuild GPU models after suspend;
- enforce per-stage deadlines;
- expose degraded state in the pill;
- never unload or free a known-invalid CUDA object through an unsafe path.

Suggested budget for the Standard profile, pending measurement:

- ASR GPU budget: at most 2.2 GB steady state.
- GPU safety headroom: at least 600 MB after model warmup.
- Formatter: quantized CPU execution by default.
- Total resident RAM budget: measured and configurable, with a target under 6 GB for all model processes.
- Finalization latency target: p50 under 1 second and p95 under 2.5 seconds for ordinary phrases.

These are release targets, not current claims.

If large-v3-turbo does not meet the GPU or latency budget, the Standard profile falls back to multilingual `small`. A slower model is not accepted merely because its benchmark WER is better.

## 10. Data model and local storage

Recommended local files:

```text
data/
  settings.json
  lexicon.sqlite3
  recovery.sqlite3
  evaluations/
  models/manifest.json
  logs/runtime.jsonl
```

Repository safeguards:

- `data/` is ignored by Git.
- model weights are not committed.
- evaluation audio is not committed.
- local databases are not committed.
- secrets and environment files are ignored.
- export requires an explicit user action.

Sensitive storage rules:

- no audio retention by default;
- no raw screen context retention;
- recovery text encrypted at rest;
- operational logs contain stage names, durations, model IDs, and error codes, not full transcripts;
- diagnostics export previews every included field before creation;
- all history can be deleted from one settings action.

## 11. Minimal software surface

The normal UI remains three elements:

1. Hotkey.
2. Non-activating status pill.
3. Tray menu with Settings, Last Dictation, Learn Correction, Pause, and Quit.

Settings pages:

- General: hotkey, trigger mode, microphone, start with Windows.
- Language: speech mode, output script, cleanup level.
- Vocabulary: words and correction pairs.
- Privacy: history, context awareness, data deletion.
- Advanced: model profile and diagnostics.

Normal users should never choose beam size, compute type, VAD threshold, model path, or token budget. Advanced settings may expose diagnostics, but automatic benchmark selection remains the default.

Pill states:

- Listening
- Processing
- Learning disabled or active indicator only when relevant
- CPU fallback
- Inserted
- Click to recover
- Microphone problem

The pill must not show unstable model text by default. Optional preview text can be enabled later.

## 12. End-to-end data flow

### 12.1 Ordinary mixed-language dictation

1. User starts dictation in a document.
2. Session controller anchors the focused control.
3. Context collector reads a bounded caret window and classifies the app.
4. Audio front end captures and validates speech.
5. Recognition coordinator runs the primary multilingual ASR.
6. Candidate lattice records text, timing, language, and confidence alternatives.
7. Resolver identifies Marathi, Hindi, and English spans.
8. Personal lexicon protects names and domain terms.
9. Script resolver follows the surrounding document.
10. Deterministic rules apply exact corrections and commands.
11. Smart formatter resolves false starts and layout.
12. Safety validator confirms semantic conservation.
13. Recovery store saves final text.
14. Insertion broker confirms the original target and inserts Unicode.
15. Recovery record is marked successful.
16. Ephemeral context is destroyed.

### 12.2 Low-confidence Marathi span

1. Primary ASR returns a Marathi phrase with low confidence.
2. Uncertainty detector identifies a protected name inside the span.
3. Same-model retry runs with lexicon and context priors.
4. If still uncertain and the Indic specialist is installed, it transcribes only the uncertain audio range.
5. Resolver compares candidates without changing high-confidence English spans.
6. If disagreement remains, the safest acoustically supported candidate is used.
7. The pill may show a non-blocking uncertainty marker in history, not interrupt the user.

### 12.3 Insertion target disappears

1. User changes windows while transcription is processing.
2. Insertion broker detects that the target identity no longer matches.
3. No keys are sent.
4. Final text remains encrypted in recovery.
5. Pill shows `Click to recover`.
6. User focuses a field and chooses Insert Here or Copy.

### 12.4 LLM unavailable

1. Formatter process times out, crashes, or cannot allocate memory.
2. Deterministic Clean output is selected.
3. Validation and insertion continue.
4. The engine records a formatter health event without transcript content.
5. Repeated failures disable Smart formatting for the session and avoid repeated delay.

## 13. Failure analysis and mitigations

| Failure | Why it happens | Detection | Required response |
|---|---|---|---|
| Marathi decoded as Hindi | Shared script and vocabulary | language disagreement and corpus error | rerank with user mode and Marathi lexicon |
| English term transliterated | script normalizer is too broad | protected entity diff | restore Latin term before insertion |
| Marathi word left as poor Roman spelling | ASR emits phonetic Latin output | script-policy mismatch | generate and rank IndicXlit candidate |
| One-word language error | too little acoustic context | short duration plus split language probability | use prior mode and nearby context; avoid aggressive rewrite |
| Hinglish forced entirely to Hindi | utterance-level language lock | mixed lexical evidence | span-level labels and mixed-script candidates |
| Meaning changed by formatter | small LLM over-edits | protected-span and semantic validation | reject Smart output and use Clean output |
| Negation removed | filler logic treats `नाही` or `नहीं` as correction | protected negation check | reject output unless explicit correction structure exists |
| Hallucination on silence | sequence model language prior dominates audio | VAD, no-speech score, repetition check | emit nothing and record diagnostic event |
| Television voice transcribed | distant speech passes VAD | calibrated near-voice level and repeated rejection logic | reject conservatively, then self-disable gate if it locks out user |
| Proper noun collision | fuzzy matcher replaces ordinary word | collision corpus and context scope | lower boost or require exact correction |
| Date ambiguity | `कल` or `परवा` depends on language and date context | protected temporal entity | preserve lexical form unless explicit formatting policy exists |
| Number corruption | spoken Indian number system is normalized incorrectly | numeric conservation check | preserve raw words or use deterministic number parser |
| Currency corruption | lakh/crore and decimal forms vary | quantity grammar | deterministic parser with locale-specific tests |
| CUDA out of memory | ASR and text model compete | preflight VRAM and allocation failure | serialize, move formatter to CPU, retry ASR on CPU |
| Model breaks after sleep | CUDA context is invalid | resume event and warmup probe | rebuild model before accepting hotkey |
| Cold model delay | model unloaded after inactivity | health state | keep within memory budget or show Processing and use deadline |
| Model download incomplete | interrupted installation | manifest size and hash | resume or redownload before activation |
| Dependency fails on Windows | NeMo or native library mismatch | startup probe | disable specialist and keep Whisper path |
| Unicode appears as boxes | target font lacks glyphs | limited post-insertion verification | preserve text in recovery and explain target-font problem |
| Unicode composition corrupted | insertion method mishandles combining marks | round-trip field check | retry with clipboard Unicode transaction |
| Clipboard overwritten | paste path restores incorrectly | transaction state | preserve transcript and restore original only after confirmed success |
| Text enters wrong window | focus changes during processing | window and control anchor mismatch | block insertion and offer recovery |
| Duplicate insertion | worker completes twice or retry races | session ID and inserted flag | idempotent insertion transaction |
| User starts another dictation | previous job still processing | state machine | queue one job or reject clearly; never mix sessions |
| Passive learning learns formatting | diff is misclassified | edit magnitude and token alignment | suggest only; require review |
| Learning database is poisoned | one bad correction receives high weight | source and confirmation counts | reversible rule, staged activation, rejection tracking |
| Screen text instructs model | untrusted content resembles prompt | context provenance | fixed schema, delimiters, output validator, no tools |
| Secure field is captured | accessibility classification fails | password flags and deny list | fail closed when field sensitivity is uncertain |
| Long dictation stalls | model and formatter exceed context budget | duration and token thresholds | clause segmentation and bounded processing |
| User speaks while model types | focus and text race | insertion phase state | do not stream unstable text; serialize final insertions |
| Model update regresses Marathi | benchmark focuses on English | multilingual release suite | block promotion on any protected slice regression |
| Script preference oscillates | nearby text is evenly mixed | low script confidence | use saved app preference or Preserve mixed speech |
| Transliteration changes a code symbol | word model sees identifier as language | code and protected span detector | bypass transliteration for identifier syntax |
| Punctuation harms search query | document policy used in search field | app classifier | minimal formatting profile for search |
| Rewriter alters terminal command | Smart mode applied to shell | terminal classification | force Verbatim unless user overrides visibly |

## 14. Indian-language specific normalization

The normalization layer must treat the following as first-class test areas:

- Devanagari combining marks.
- Anusvara and chandrabindu variation.
- Marathi-specific letters and forms.
- Hindi versus Marathi lexical ambiguity in shared script.
- English plural or tense attached to Indic speech.
- Latin acronyms inside Devanagari sentences.
- Indian personal and place names with multiple Roman spellings.
- Spoken lakh, crore, thousand, decimal, percentage, rupee, and paise forms.
- Dates spoken in English, Hindi, Marathi, or mixed form.
- Times such as `साडे तीन`, `ढाई`, and `quarter past three`.
- Honorifics and initials.
- English product names pronounced with an Indian accent.
- Marathi postpositions or particles adjacent to English names.

The product must preserve natural mixed script when that is how the user writes. "Correct" output is the user's intended register, not a language-purity rule.

## 15. Evaluation architecture

Accuracy work begins with measurement, before production model changes.

### 15.1 Corpus

Create a private, consented corpus recorded specifically for this project. Do not read or mine existing dictation history without explicit approval.

Initial corpus target:

- 100 English utterances.
- 100 Marathi utterances.
- 100 Hindi utterances.
- 150 Hinglish utterances.
- 150 Marathi-English utterances.
- 50 proper-noun stress tests.
- 50 command ambiguity tests.
- 50 self-correction and restart tests.
- 50 number, date, currency, URL, and identifier tests.
- quiet, fan noise, television, near voice, far voice, and different microphone conditions.

Each item stores:

```text
audio
verbatim reference
desired final text
language spans
script policy
app category
protected entities
allowed edits
forbidden edits
```

Audio and expected text remain outside Git.

### 15.2 Metrics

Raw ASR metrics:

- Word Error Rate for Latin text.
- Character Error Rate for Devanagari.
- Mixed Error Rate across scripts.
- Language-boundary accuracy.
- Named-entity recall.
- Protected-number accuracy.

Product metrics:

- Exact desired-output match.
- Meaning-changing error rate.
- Unsupported-word insertion rate.
- False correction rate.
- Command precision and recall.
- Script-policy accuracy.
- Corrections required per 1,000 dictated words.
- Successful insertion rate by application.
- Recovery success rate.
- p50 and p95 latency from end of speech to visible text.
- peak VRAM and RAM.
- offline-start success rate.

The primary success metric is corrections required per 1,000 words, sliced by language mode. WER alone does not capture whether the product feels accurate.

### 15.3 Comparative evaluation

If the user chooses to compare against Wispr Flow:

1. Play the same consented recordings into each system under controlled conditions.
2. Capture raw output and final formatted output.
3. Score both against the same desired text.
4. Separate network latency from local inference latency.
5. Report every language slice independently.
6. Do not claim superiority from a synthetic corpus alone.

### 15.4 Release gates

A candidate release is blocked if:

- any control phrase produces an unsupported sentence;
- any secure-field test leaks or inserts text;
- command false positives exceed the current zero-tolerance control set;
- protected numbers or negations regress;
- Marathi, Hindi, Hinglish, or Marathi-English quality regresses materially;
- p95 latency exceeds the agreed budget without a visible degraded-state explanation;
- GPU failure loses the current utterance;
- insertion can target the wrong window;
- a learned correction cannot be traced and reversed.

Exact numerical thresholds should be set after baseline measurement, then frozen before model selection.

## 16. Testing strategy

### 16.1 Unit tests

- language-span merging;
- Unicode normalization;
- transliteration candidate generation;
- protected span extraction;
- lexicon priority;
- correction-rule scoping;
- self-correction grammar;
- numeric and currency parsing;
- application classification;
- formatter response validation;
- insertion transaction idempotency;
- recovery expiry;
- model manifest verification.

### 16.2 Golden tests

Golden fixtures contain raw candidate bundles and expected resolved output. They test the pipeline without loading models.

Every production error that can be safely reproduced becomes a golden case. Fixtures containing personal content must be anonymized before entering Git.

### 16.3 Model replay tests

Run the private corpus through each model adapter. Cache raw model outputs keyed by model hash and decoder configuration so resolver tests do not repeatedly perform inference.

### 16.4 Integration tests

- Notepad Unicode insertion.
- Word or a compatible document editor.
- Browser text area.
- Slack or Teams test field where available.
- VS Code editor and AI chat field.
- Search box.
- Windows Terminal, with Verbatim enforcement.
- focus change during processing.
- clipboard contention.
- sleep and resume.
- GPU contention with a local text model.
- corrupted settings and databases.

### 16.5 Soak tests

- eight-hour idle process with periodic dictations;
- repeated sleep and resume;
- 1,000 short dictations without restart;
- model fallback loops;
- microphone disconnect and reconnect;
- long hands-free session bounded by the duration cap;
- low disk space;
- full recovery store;
- repeated LLM timeout.

## 17. Proposed internal module boundaries

The exact filenames can change during implementation planning. The interfaces should not.

```text
dictate.py                         application composition and lifecycle
dictate_config.py                  versioned user settings
dictate_session.py                 session state machine
dictate_audio.py                   capture, calibration, audio health
dictate_stream.py                  VAD and stable-prefix segmentation
dictate_context.py                 bounded Windows context collection
dictate_apps.py                    application classification and profiles
dictate_models.py                  model adapters and resource manager
dictate_recognition.py             routing, retries, candidate lattice
dictate_language.py                language-span and script decisions
dictate_lexicon.py                 vocabulary and correction database
dictate_transliteration.py         Roman and Devanagari candidates
dictate_corrections.py             commands and self-correction structure
dictate_polish.py                  deterministic and local-LLM formatting
dictate_validate.py                semantic safety checks
dictate_insert.py                  Unicode insertion and recovery
dictate_storage.py                 encrypted local persistence
dictate_overlay.py                 non-activating status UI
dictate_tray.py                    tray actions
```

Model adapters implement:

```python
class ASRBackend(Protocol):
    def load(self, device_plan) -> ModelHealth: ...
    def warmup(self) -> ModelHealth: ...
    def recognize(self, audio, options) -> TranscriptCandidate: ...
    def close(self) -> None: ...
```

Formatter adapters implement:

```python
class FormatterBackend(Protocol):
    def available(self) -> bool: ...
    def format(self, request, deadline) -> FormatCandidate: ...
```

Insertion adapters implement:

```python
class InsertionBackend(Protocol):
    def supports(self, target) -> bool: ...
    def insert(self, target, text) -> InsertionResult: ...
```

These boundaries let model or application support evolve without rewriting the safety logic.

## 18. Configuration schema

Suggested user-level schema:

```json
{
  "hotkey": "f9",
  "trigger_mode": "toggle",
  "speech_mode": "auto_en_mr_hi",
  "script_mode": "follow_context",
  "cleanup": "smart",
  "model_profile": "auto",
  "context_awareness": true,
  "history": false,
  "correction_suggestions": true,
  "stream_preview": false,
  "run_at_login": false
}
```

Internal tuning values belong in a versioned engine profile, not the ordinary settings file. This prevents a wall of unsupported controls and enables safe migration.

## 19. Installation and offline operation

Installation sequence:

1. Verify Windows and Python runtime or install a packaged runtime.
2. Detect GPU and CPU capabilities.
3. Install core dependencies.
4. Download the selected primary model.
5. Verify model hashes.
6. Run offline warmup.
7. Record a short optional calibration set.
8. Select the initial profile.
9. Create Start Menu and optional startup shortcuts.
10. Enable an explicit offline lock test.

Offline guarantee test:

- start the application with network adapters disabled;
- transcribe each language slice;
- run Smart formatting;
- insert Devanagari and Latin text;
- restart and repeat;
- inspect outbound connection attempts;
- fail the release if any normal feature requires remote access.

Model downloads and software updates are user-triggered. The application must never replace a working model automatically.

## 20. Delivery sequence

### Phase 0: Measurement foundation

Deliver:

- private corpus format;
- guided recorder;
- multilingual scorer;
- baseline current `small.en` report;
- reproducible hardware and latency capture.

Exit condition:

- baseline error categories are known for all five speech slices.

### Phase 1: Multilingual baseline

Deliver:

- multilingual Whisper adapter;
- removal of hard-coded English in Auto mode;
- model bake-off for small, medium, and large-v3-turbo;
- fixed-language priors;
- CPU and GPU fallback tests.

Exit condition:

- one primary model meets resource limits and beats the English-only baseline on the combined corpus without unacceptable English regression.

### Phase 2: Unicode and script correctness

Deliver:

- Unicode insertion broker;
- Devanagari normalization;
- output script modes;
- protected Latin entities;
- recovery store.

Exit condition:

- verified insertion across the supported application matrix with no wrong-window events.

### Phase 3: Code-switch resolution

Deliver:

- span-level language representation;
- personal lexicon database;
- transliteration candidates;
- mixed-script resolver;
- Marathi-English and Hinglish golden suites.

Exit condition:

- mixed-language correction burden falls materially against Phase 1 and protected entities do not regress.

### Phase 4: Context awareness

Deliver:

- bounded Windows context collector;
- application classifier;
- email, messaging, document, search, terminal, and IDE profiles;
- secure-field fail-closed behavior.

Exit condition:

- context improves entity and formatting accuracy without retaining raw context or harming privacy tests.

### Phase 5: Smart correction and formatting

Deliver:

- constrained local formatter;
- self-correction interpreter;
- semantic safety validator;
- Undo Smart Edit;
- deterministic fallback.

Exit condition:

- corrections per 1,000 words improve while unsupported insertions and protected-span changes remain within the frozen release gates.

### Phase 6: Reviewed personalization

Deliver:

- Learn Correction action;
- correction suggestion queue;
- source, confidence, and reversibility for every rule;
- import and export.

Exit condition:

- repeated personal errors decline without silent rule poisoning.

### Phase 7: Reliability release

Deliver:

- soak tests;
- suspend recovery;
- contention behavior;
- installer and rollback;
- privacy and offline verification;
- honest comparative report.

Exit condition:

- the system can be used for a full week without lost dictations, wrong-window insertion, required network access, or unrecoverable learning errors.

## 21. Decisions deliberately deferred

- Fine-tuning Whisper or IndicConformer on the user's voice.
- Automatic correction learning without review.
- Additional Indian languages.
- Screenshot-based context.
- Mobile clients.
- Cloud backup.
- Team dictionaries.

Fine-tuning becomes worthwhile only after the corpus proves that remaining errors are acoustic and systematic. Many apparent ASR errors will instead be script, context, vocabulary, or formatting errors.

## 22. Feasibility conclusion

An all-local, unlimited system with a Wispr-like daily experience is feasible for a constrained target: one Windows user, known hardware, English, Marathi, Hindi, Hinglish, and Marathi-English.

The key is specialization. A proprietary general service must work for many users, languages, devices, and workflows. Dictate Local can optimize for one voice, one vocabulary, a small set of applications, and explicit script preferences.

The largest risks are:

1. Mixed-language recognition on short spans.
2. Devanagari versus Roman output ambiguity.
3. A small formatter changing meaning.
4. GPU contention on 4 GB VRAM.
5. Unicode insertion across inconsistent Windows applications.
6. Correction learning that mistakes ordinary edits for speech errors.

Each risk has a contained fallback. The application always retains a safe path from audio to conservative text, even when the specialist model, formatter, context collector, GPU, or insertion integration fails.

The recommended first implementation target is not the complete Smart system. It is Phase 0 through Phase 2: establish the multilingual benchmark, select the primary ASR on the actual laptop, and make Devanagari insertion reliable. Those phases determine whether the later intelligence is improving a sound foundation.

## 23. External technical basis

Sources checked on 6 September 2026:

- [Wispr Flow technical challenges](https://wisprflow.ai/post/technical-challenges): describes context-conditioned ASR, correction learning, and latency targets.
- [Wispr Flow Context Awareness](https://docs.wisprflow.ai/articles/4678293671-Context-Awareness): describes application and nearby-text context behavior.
- [Wispr Flow Smart Formatting and Backtrack](https://docs.wisprflow.ai/articles/5373093536-how-do-i-use-smart-formatting-backtrack): describes formatting and self-correction behavior.
- [OpenAI Whisper large-v3-turbo model card](https://huggingface.co/openai/whisper-large-v3-turbo): lists 99-language support, 809M parameters, and known multilingual limitations.
- [faster-whisper large-v3-turbo benchmark discussion](https://github.com/SYSTRAN/faster-whisper/issues/1030): provides a non-authoritative int8 memory and speed reference that must be remeasured locally.
- [AI4Bharat IndicConformer](https://github.com/AI4Bharat/IndicConformerASR): documents ASR models covering all 22 official Indian languages.
- [AI4Bharat IndicXlit](https://github.com/AI4Bharat/IndicXlit): documents local transliteration support for 21 Indic languages.
- [Qwen3 multilingual language coverage](https://qwenlm.github.io/blog/qwen3/): lists Marathi, Hindi, English, and other supported text languages.
- [Qwen3-ASR 1.7B model card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B): currently lists Hindi but not Marathi, which prevents its use as the sole recognizer here.

Source claims in this document are candidate-selection evidence only. Product performance must be established through the private local evaluation corpus.
