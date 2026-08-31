"""Test phrases for the dictation harness.

Each entry is (spoken_text, [terms that must appear verbatim in the output]).
The terms are what a general model gets wrong; they are the whole point of
the vocabulary work.
"""

VOCAB_PHRASES = [
    ("I applied through Naukri and also through Instahyre last week.",
     ["Naukri", "Instahyre"]),
    ("My masters was at ESCP Business School in Paris.",
     ["ESCP"]),
    ("I sent my application to Zalando on Tuesday.",
     ["Zalando"]),
    ("Pull the comparable transactions from PitchBook and cross check against Morningstar.",
     ["PitchBook", "Morningstar"]),
    ("MicroStrategy holds a very large treasury position.",
     ["MicroStrategy"]),
    ("The German employer gave me an Arbeitszeugnis when I left.",
     ["Arbeitszeugnis"]),
    ("I wrote a candidature spontanee to the Paris office.",
     ["candidature spontan"]),
    ("The DEAMIE programme is the one I want to join.",
     ["DEAMIE"]),
    ("I am applying for the Talent Passport visa route.",
     ["Talent Passport"]),
    ("I automated the whole pipeline in n8n and deployed it behind Cloudflare.",
     ["n8n", "Cloudflare"]),
    ("I found the role on iimjobs rather than LinkedIn.",
     ["iimjobs"]),
]

# Plain speech with no special vocabulary. Guards against the prompt making
# ordinary transcription worse, which is the real risk of initial_prompt.
CONTROL_PHRASES = [
    ("The quick brown fox jumps over the lazy dog.", []),
    ("I need to finish the report before the meeting tomorrow morning.", []),
    ("Please send me the updated numbers when you get a chance.", []),
    ("We discussed the timeline and agreed to push the deadline by one week.", []),
    ("There are three things I want to cover in this call.", []),
]
