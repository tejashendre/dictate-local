"""What to read aloud, and what each phrase is trying to break.

Every phrase here targets one failure mode. A corpus of pleasant, well-formed
sentences would show this tool performing beautifully and would be worthless,
because ordinary well-formed sentences are the case that already works.

Each entry is:

    (category, said, want, protected_names, protected_numbers)

`said` is what to read aloud. `want` is what should end up in the document.
They differ only where a human would expect the tool to clean something up:
a filler word, an abandoned clause, a spoken correction.

The vocabulary used is drawn from vocabulary.txt and tests/phrases.py, which
are already in this repository. Nothing here is new personal information.

Silence records carry empty text on purpose. They are the control that catches
a model inventing "Thank you for watching." out of room tone, which is a real
failure this project has already hit once.
"""

# (category, said, want, protected_names, protected_numbers)
PHRASES = [

    # ---------------------------------------------------------- ordinary
    ("ordinary",
     "I need to finish the report before the meeting tomorrow morning.",
     "I need to finish the report before the meeting tomorrow morning.",
     [], []),
    ("ordinary",
     "Please send me the updated numbers when you get a chance.",
     "Please send me the updated numbers when you get a chance.",
     [], []),
    ("ordinary",
     "We discussed the timeline and agreed to push the deadline by one week.",
     "We discussed the timeline and agreed to push the deadline by one week.",
     [], []),
    ("ordinary",
     "There are three things I want to cover in this call.",
     "There are three things I want to cover in this call.",
     [], []),
    ("ordinary",
     "Let me know if that works for you and I will send the invite.",
     "Let me know if that works for you and I will send the invite.",
     [], []),
    ("ordinary",
     "The client has not replied yet so I will follow up on Thursday.",
     "The client has not replied yet so I will follow up on Thursday.",
     [], []),

    # -------------------------------------------------------------- fast
    # Read these quickly, at the pace of talking to someone who is already
    # following you. The measured peak is 147 words per minute.
    ("fast",
     "So the thing is I already sent that over yesterday and I have not heard "
     "back which is a bit odd given how urgent they said it was.",
     "So the thing is I already sent that over yesterday and I have not heard "
     "back which is a bit odd given how urgent they said it was.",
     [], []),
    ("fast",
     "Can you check the numbers again because I think the second column is "
     "wrong and if it is wrong then the whole summary is wrong too.",
     "Can you check the numbers again because I think the second column is "
     "wrong and if it is wrong then the whole summary is wrong too.",
     [], []),
    ("fast",
     "I will do the deck tonight and send it first thing so you have it "
     "before the standup.",
     "I will do the deck tonight and send it first thing so you have it "
     "before the standup.",
     [], []),

    # -------------------------------------------------------------- long
    ("long",
     "The reason I restructured the research workflow rather than asking for "
     "more headcount is that the constraint was never the number of people, "
     "it was the order in which we did the verification steps, and once that "
     "changed the turnaround halved without anyone working longer hours.",
     "The reason I restructured the research workflow rather than asking for "
     "more headcount is that the constraint was never the number of people, "
     "it was the order in which we did the verification steps, and once that "
     "changed the turnaround halved without anyone working longer hours.",
     [], []),
    ("long",
     "When a partner case comes in blocked on billing there are usually three "
     "separate teams who each believe the problem belongs to one of the other "
     "two, and the actual work is not technical at all, it is getting the "
     "three of them into agreement about which system holds the truth.",
     "When a partner case comes in blocked on billing there are usually three "
     "separate teams who each believe the problem belongs to one of the other "
     "two, and the actual work is not technical at all, it is getting the "
     "three of them into agreement about which system holds the truth.",
     [], []),

    # ------------------------------------------------------------- names
    ("names",
     "I applied through Naukri and also through Instahyre last week.",
     "I applied through Naukri and also through Instahyre last week.",
     ["Naukri", "Instahyre"], []),
    ("names",
     "My masters was at ESCP Business School in Paris.",
     "My masters was at ESCP Business School in Paris.",
     ["ESCP"], []),
    ("names",
     "I sent my application to Zalando on Tuesday.",
     "I sent my application to Zalando on Tuesday.",
     ["Zalando"], []),
    ("names",
     "Pull the comparable transactions from PitchBook and cross check against "
     "Morningstar.",
     "Pull the comparable transactions from PitchBook and cross check against "
     "Morningstar.",
     ["PitchBook", "Morningstar"], []),
    ("names",
     "MicroStrategy holds a very large treasury position.",
     "MicroStrategy holds a very large treasury position.",
     ["MicroStrategy"], []),
    ("names",
     "The German employer gave me an Arbeitszeugnis when I left.",
     "The German employer gave me an Arbeitszeugnis when I left.",
     ["Arbeitszeugnis"], []),
    ("names",
     "The DEAMIE programme is the one I want to join.",
     "The DEAMIE programme is the one I want to join.",
     ["DEAMIE"], []),
    ("names",
     "I found the role on iimjobs rather than LinkedIn.",
     "I found the role on iimjobs rather than LinkedIn.",
     ["iimjobs", "LinkedIn"], []),
    ("names",
     "I automated the pipeline in n8n and deployed it behind Cloudflare.",
     "I automated the pipeline in n8n and deployed it behind Cloudflare.",
     ["n8n", "Cloudflare"], []),
    ("names",
     "Shubham is handling the SpendSignal demo and I am doing DoubleTick.",
     "Shubham is handling the SpendSignal demo and I am doing DoubleTick.",
     ["Shubham", "SpendSignal", "DoubleTick"], []),

    # ------------------------------------------------------- dates_times
    ("dates_times",
     "The interview is on the third of March at half past four.",
     "The interview is on the third of March at half past four.",
     [], []),
    ("dates_times",
     "I joined in March 2023 and left in August 2024.",
     "I joined in March 2023 and left in August 2024.",
     [], ["2023", "2024"]),
    ("dates_times",
     "Let us move the call to Tuesday the 15th at 9:30 in the morning.",
     "Let us move the call to Tuesday the 15th at 9:30 in the morning.",
     [], ["15", "9", "30"]),
    ("dates_times",
     "The deadline is the 30th of November 2026.",
     "The deadline is the 30th of November 2026.",
     [], ["30", "2026"]),

    # ----------------------------------------------------- numbers_money
    ("numbers_money",
     "The business case projected 1.6 billion euros in net benefit.",
     "The business case projected 1.6 billion euros in net benefit.",
     [], ["1.6"]),
    ("numbers_money",
     "We covered 3000 companies and validated 8 to 14 metrics on each one.",
     "We covered 3000 companies and validated 8 to 14 metrics on each one.",
     [], ["3000", "8", "14"]),
    ("numbers_money",
     "Turnaround went from 48 hours to 24 hours at 99 percent accuracy.",
     "Turnaround went from 48 hours to 24 hours at 99 percent accuracy.",
     [], ["48", "24", "99"]),
    ("numbers_money",
     "The salary band is 12 to 16 lakhs.",
     "The salary band is 12 to 16 lakhs.",
     [], ["12", "16"]),
    ("numbers_money",
     "It runs at 11 to 16 times realtime and uses 433 megabytes of VRAM.",
     "It runs at 11 to 16 times realtime and uses 433 megabytes of VRAM.",
     ["VRAM"], ["11", "16", "433"]),

    # ------------------------------------------------------- urls_emails
    ("urls_emails",
     "Send it to tejas dot hendre at edu dot escp dot eu.",
     "Send it to tejas.hendre@edu.escp.eu.",
     [], []),
    ("urls_emails",
     "The repository is github dot com slash tejashendre slash dictate dash "
     "local.",
     "The repository is github.com/tejashendre/dictate-local.",
     [], []),
    ("urls_emails",
     "My site is tejashendre dot com.",
     "My site is tejashendre.com.",
     [], []),

    # --------------------------------------------------------- technical
    ("technical",
     "The model runs on CUDA in int8 float16 and falls back to CPU cleanly.",
     "The model runs on CUDA in int8 float16 and falls back to CPU cleanly.",
     ["CUDA", "CPU"], ["8", "16"]),
    ("technical",
     "The pill uses WS underscore EX underscore NOACTIVATE so it never takes "
     "focus.",
     "The pill uses WS_EX_NOACTIVATE so it never takes focus.",
     [], []),
    ("technical",
     "Silero VAD gates the output and ctranslate2 loads cuBLAS at import time.",
     "Silero VAD gates the output and ctranslate2 loads cuBLAS at import time.",
     ["Silero", "VAD", "ctranslate2", "cuBLAS"], ["2"]),
    ("technical",
     "The initial prompt is capped at 180 tokens so it never crowds out the "
     "audio context.",
     "The initial prompt is capped at 180 tokens so it never crowds out the "
     "audio context.",
     [], ["180"]),

    # ----------------------------------------------------------- fillers
    # `want` drops the filler. `said` keeps it, because the raw score has to
    # see what was actually spoken.
    ("fillers",
     "So um I think we should uh probably wait until Monday.",
     "So I think we should probably wait until Monday.",
     [], []),
    ("fillers",
     "It is you know basically the same problem we had last quarter.",
     "It is basically the same problem we had last quarter.",
     [], []),
    ("fillers",
     "I mean the numbers are fine but uh the framing is wrong.",
     "I mean the numbers are fine but the framing is wrong.",
     [], []),

    # ------------------------------------------------------ false_starts
    ("false_starts",
     "I was going to, actually let me start again, the report is finished.",
     "The report is finished.",
     [], []),
    ("false_starts",
     "We could either, no, the simplest option is to ship it on Friday.",
     "The simplest option is to ship it on Friday.",
     [], []),
    ("false_starts",
     "The thing about the, the way the workflow is set up is that it blocks "
     "on one person.",
     "The way the workflow is set up is that it blocks on one person.",
     [], []),

    # --------------------------------------------------- self_correction
    ("self_correction",
     "Send it to Naukri, no wait, send it to Instahyre.",
     "Send it to Instahyre.",
     ["Instahyre"], []),
    ("self_correction",
     "The meeting is at four, sorry, I meant half past four.",
     "The meeting is at half past four.",
     [], []),
    ("self_correction",
     "It came to 24 hours, actually 48 hours before the change.",
     "It came to 48 hours before the change.",
     [], ["48"]),
    ("self_correction",
     "I worked at Zalando in Berlin, sorry, in Berlin from January.",
     "I worked at Zalando in Berlin from January.",
     ["Zalando"], []),

    # ------------------------------------------------------------ quiet
    # Same text as an ordinary phrase, recorded deliberately in the quietest
    # available condition, so noise can be isolated as a variable.
    ("quiet",
     "This one is recorded in a quiet room as the baseline condition.",
     "This one is recorded in a quiet room as the baseline condition.",
     [], []),
    ("quiet",
     "I applied to Zalando through Naukri and the recruiter mentioned "
     "PitchBook.",
     "I applied to Zalando through Naukri and the recruiter mentioned "
     "PitchBook.",
     ["Zalando", "Naukri", "PitchBook"], []),

    # --------------------------------------------------------- fan_noise
    ("fan_noise",
     "This one is recorded while the laptop fan is running under load.",
     "This one is recorded while the laptop fan is running under load.",
     [], []),
    ("fan_noise",
     "Pull the comparables from PitchBook and check them against Morningstar.",
     "Pull the comparables from PitchBook and check them against Morningstar.",
     ["PitchBook", "Morningstar"], []),

    # -------------------------------------------------------- tv_distant
    ("tv_distant",
     "This one is recorded with a television or another voice in the room.",
     "This one is recorded with a television or another voice in the room.",
     [], []),
    ("tv_distant",
     "The interview is on the third of March at half past four.",
     "The interview is on the third of March at half past four.",
     [], []),

    # ----------------------------------------------------------- silence
    # Say nothing. These catch the model inventing text from room tone, which
    # has already happened once in real use: half a second of near-silence
    # produced a confident "Thank you for watching."
    ("silence", "", "", [], []),
    ("silence", "", "", [], []),
    ("silence", "", "", [], []),
    ("silence", "", "", [], []),
]


def by_category():
    """Group the phrase list, so the recorder can walk one category at a time."""
    out = {}
    for cat, said, want, names, numbers in PHRASES:
        out.setdefault(cat, []).append((said, want, names, numbers))
    return out


def summary():
    """Counts per category, for the recorder banner and for tests."""
    counts = {}
    for cat, *_rest in PHRASES:
        counts[cat] = counts.get(cat, 0) + 1
    return counts
