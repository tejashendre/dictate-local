# Build Log

**This tool was built in about 29 hours across two days, by dictating at it.**

Once the first version could type, most of the instructions that shaped the rest of it were spoken into the tool itself. The vocabulary layer exists because it kept misspelling *Naukri* while I was asking it to fix something else.

I am publishing the cost of that because the number surprised me, and because "built with AI" usually gets stated without one.

---

## What it cost

Measured from the session transcripts, 31 August to 1 September 2026:

| | Tokens | Share |
|---|---:|---:|
| Fresh input | 4,578 | 0.0% |
| Cache writes — context stored | 19,862,588 | 2.0% |
| **Cache reads — the same context re-read every turn** | **950,868,345** | **97.7%** |
| Output — code, tests and prose actually written | 2,629,340 | 0.3% |
| **Total** | **973,364,851** | |

Across **2,299 turns** in **29.2 hours**.

**The headline number is almost entirely re-reading.** 97.7% of it is the conversation being read again on every turn — not new work, and roughly an order of magnitude cheaper per token than fresh input. Quoting 973M as though it were 973M of thinking would be dishonest.

The number that reflects work done is **2.6M output tokens**.

### And from my side

| | |
|---|---|
| Messages written or dictated | 60 |
| Words | 21,834 (~29,000 tokens) |
| Produced | 4,450 lines of application code, 2,942 lines of tests |

About **90 tokens written for every token I spoke.**

---

## What that ratio does and does not mean

It does not mean the work was free, and it does not mean 21,834 words of instruction produced a working tool on their own.

Most of those 60 messages were **not** feature requests. They were bug reports from using the thing:

> *"so i had folded the laptop and then now after 3 hours i cannot see it working when i pressed f9"*

> *"The bot after f9 doesnt work and it closes and i cannot even see if it is even working"*

> *"Like the design that is there is very shittier that you are trying to design. It is the worst one."*

The leverage is real, but it is leverage on **direction**, not on judgement. Every one of the 15 test suites in `tests/` exists because something broke in use first, and several of those failures were introduced by fixes for earlier failures. Three separate crashes during these two days were caused by a repair to something that had been working.

That is the honest shape of building this way: **fast, and self-inflicted.** The tests are not there for coverage. They are there because the same class of bug kept coming back, so each one is verified by reverting its fix and confirming the test fails.

---

## The failures worth reading

Ordered by how long they took to find, not how bad they looked.

**The GPU had never actually run.** `cublas64_12.dll` was missing, so every transcription had been silently falling back to CPU. `ctranslate2` loads it with a plain `LoadLibrary`, which means `os.add_dll_directory` does not help — it has to be on `PATH` before the import. Found only by checking which device was really in use rather than trusting the startup message.

**The status pill stole focus, so it typed into itself.** `WS_EX_NOACTIVATE` alone was not enough: `update_idletasks()` takes the foreground before any window style exists. Fixed by capturing the previous foreground window and handing it back.

**Half a second of near-silence produced "Thank you for watching."** A YouTube artifact from the training data, typed confidently into whatever was focused. Voice activity detection had to gate the output, not just trim it.

**Sleep killed the process in native code.** After a lid-close, the hotkey stopped working. Fixing that exposed a worse fault underneath: suspend destroys the CUDA context, and touching the old model afterwards crashes with `ucrtbase.dll` / `0xc0000409` and no Python traceback at all. The model now rebuilds on wake, and the dead handle is deliberately leaked rather than freed — because freeing it is itself a call into the destroyed context.

**A safety feature locked me out of my own tool.** A noise gate meant to ignore other voices in the room started rejecting mine, because a test had written synthetic audio levels into the live settings file. Three fixes: tests can no longer write live settings, the gate self-disables after repeated rejections, and rejections are now shown on screen instead of happening silently.

**Three faults that never announced themselves.** Found on the last pass by probing rather than using: one oversized line in `vocabulary.txt` silently switched off *all* vocabulary biasing; a malformed `settings.json` killed startup before the log file existed; and a recording nobody stopped grew unbounded at 64 KB/s. None had ever been reported, because nothing visibly broke.

---

## What I would tell someone doing this

**Measure before optimising.** Latency felt like a Python problem. Python was 2.3 ms of 1,125 ms — 0.2%. Model inference was 78.6%. Rewriting in a faster language would have saved two milliseconds.

**Bigger models are not better under a constraint.** `medium.en` ran 2.9× slower for +592 MB of VRAM and got one vocabulary term *worse*. `distil-medium.en` returned 92.6% WER and was unusable.

**A test that passes on broken code is decoration.** Every suite here was checked by reverting its fix and confirming it fails. Two of them caught nothing until that check was run.

**The last mile is the product.** Audio to text is a library call. Text into the right window, with the right capitalisation, without stealing focus, recovering after suspend — that is where the actual work was, and it is most of this repository.

---

*Full design record, including what was tried and rejected, is in [`ARCHITECTURE.md`](ARCHITECTURE.md).*
