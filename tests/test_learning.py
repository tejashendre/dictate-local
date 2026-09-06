"""Phase 4: only what the user deliberately taught, and always reversible.

ARCHITECTURE.md Section 15. Two refusals define this feature:

  It learns only from an explicit "Correct last dictation". Watching keyboard
  edits would learn from a sentence the user simply changed their mind about,
  and nothing in the keystrokes distinguishes that from a transcription error.

  A global rule is a bounded substitution. A long rewrite becomes a retrievable
  example instead, because a rule that rewrites whole sentences will eventually
  rewrite the wrong one.

    python tests/test_learning.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import io
import json
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dictate_learning as L      # noqa: E402


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def test_a_correction_is_applied_next_time():
    print("\n  1. the same mistake is not made twice")
    # The Phase 4 exit criterion: an approved correction applies on a repeated
    # phrase. This is the whole product goal in one test.
    store = L.empty_store()
    proposal, why = L.propose("I use flow code every day",
                              "I use Claude Code every day")
    ok = check("a bounded mapping is proposed", proposal is not None, why)
    if proposal:
        ok &= check("it isolates only what changed",
                    proposal["heard"] == "flow code"
                    and proposal["wanted"] == "Claude Code", str(proposal))
        L.add_rule(store, proposal["heard"], proposal["wanted"])

    out, fired = L.apply_rules("flow code is what I meant", store)
    ok &= check("the correction applies to a later phrase",
                out == "Claude Code is what I meant", out)
    ok &= check("and it is reported", fired == [("flow code", "Claude Code")],
                str(fired))
    ok &= check("use_count is tracked", store["rules"][0]["use_count"] == 1)
    return ok


def test_only_bounded_rules_become_global():
    print("\n  2. a long rewrite never becomes a global search and replace")
    ok = True
    long_heard = ("the thing about the way the workflow is set up is that it "
                  "blocks on one person")
    long_wanted = ("the workflow blocks on a single person and that is the "
                   "constraint we should fix")
    proposal, why = L.propose(long_heard, long_wanted)
    ok &= check("a sentence rewrite is refused as a rule", proposal is None,
                why)
    ok &= check("and the reason names the alternative", "example" in why, why)

    # It is still kept, just as an example the formatter can retrieve.
    store = L.empty_store()
    L.add_example(store, long_heard, long_wanted)
    ok &= check("it is stored as an example instead",
                len(store["examples"]) == 1)
    ok &= check("and no rule was created", not store["rules"])
    return ok


def test_nothing_is_learned_without_confirmation():
    print("\n  3. proposing is not storing")
    # Section 15.1: the mapping is enabled only after explicit confirmation.
    store = L.empty_store()
    proposal, _why = L.propose("guitar repo", "GitHub repo")
    ok = check("a proposal was made", proposal is not None)
    ok &= check("but nothing was stored by proposing", not store["rules"],
                "propose() must never write")

    L.add_rule(store, proposal["heard"], proposal["wanted"])
    ok &= check("only add_rule stores it", len(store["rules"]) == 1)
    ok &= check("and it is marked as coming from the user",
                store["rules"][0]["source"] == L.SOURCE_USER)
    return ok


def test_every_rule_is_reversible():
    print("\n  4. a rule that causes a regression can be switched off")
    store = L.empty_store()
    rule = L.add_rule(store, "no", "No")
    ok = check("the rule fires while enabled",
               L.apply_rules("no problem", store)[0] == "No problem")

    L.set_enabled(store, rule["id"], False)
    ok &= check("disabling stops it",
                L.apply_rules("no problem", store)[0] == "no problem")
    L.set_enabled(store, rule["id"], True)
    ok &= check("and it can be switched back on",
                L.apply_rules("no problem", store)[0] == "No problem")

    L.edit_rule(store, rule["id"], wanted="NO")
    ok &= check("it can be edited",
                L.apply_rules("no problem", store)[0] == "NO problem")

    ok &= check("and deleted", L.delete_rule(store, rule["id"]) == 1)
    ok &= check("after which nothing fires", not store["rules"])
    return ok


def test_longest_rule_wins():
    print("\n  5. a longer rule beats a shorter one inside it")
    store = L.empty_store()
    L.add_rule(store, "flow", "Flow")
    L.add_rule(store, "flow code", "Claude Code")
    out, _fired = L.apply_rules("I ran flow code today", store)
    ok = check("the two-word rule wins", out == "I ran Claude Code today", out)

    # Whole words only. A rule for "no" must not touch "nothing".
    store2 = L.empty_store()
    L.add_rule(store2, "no", "No")
    out2, _f = L.apply_rules("nothing is known here", store2)
    ok &= check("a rule never fires inside another word",
                out2 == "nothing is known here", out2)
    return ok


def test_storage_survives_a_bad_file():
    print("\n  6. a malformed correction file never stops dictation")
    # Section 20: quarantine it, load no learned rules, keep working.
    ok = True
    d = tempfile.mkdtemp()
    path = os.path.join(d, "corrections.json")

    io.open(path, "w", encoding="utf-8").write("{not json at all")
    store, problem = L.load(path)
    ok &= check("a corrupt file loads as empty", store["rules"] == [])
    ok &= check("and the problem is reported", bool(problem), str(problem))
    ok &= check("the bad file is quarantined, not deleted",
                os.path.exists(path + ".quarantined"))

    io.open(path, "w", encoding="utf-8").write('{"version":1,"rules":"nope"}')
    store, problem = L.load(path)
    ok &= check("rules that are not a list are refused too", bool(problem))
    return ok


def test_storage_round_trip_is_atomic():
    print("\n  7. the store survives a write and read")
    ok = True
    d = tempfile.mkdtemp()
    path = os.path.join(d, "corrections.json")
    store = L.empty_store()
    L.add_rule(store, "nokri", "Naukri")
    L.add_example(store, "a long thing", "a better long thing")
    L.save(store, path)

    ok &= check("no temporary file is left behind",
                not os.path.exists(path + ".tmp"),
                "an atomic write must clean up")
    back, problem = L.load(path)
    ok &= check("it reads back without problems", problem is None, str(problem))
    ok &= check("the rule survived", back["rules"][0]["heard"] == "nokri")
    ok &= check("the example survived", len(back["examples"]) == 1)
    ok &= check("every field Section 15.2 requires is present",
                all(k in back["rules"][0] for k in
                    ("id", "heard", "wanted", "scope", "source", "created_at",
                     "use_count", "enabled")),
                str(sorted(back["rules"][0])))
    return ok


def test_retrieval_sends_at_most_three():
    print("\n  8. retrieval is bounded, deterministic, and quiet when unsure")
    store = L.empty_store()
    for i in range(8):
        L.add_example(store, "the quarterly report for region %d" % i,
                      "the Q%d regional report" % i)
    L.add_example(store, "send the invoice to accounts", "send it to Accounts")

    got = L.retrieve("the quarterly report for region 3", store)
    ok = check("at most three are returned", len(got) <= 3, "%d" % len(got))
    ok &= check("the most relevant is first",
                "region 3" in got[0]["heard"] if got else False,
                got[0]["heard"] if got else "nothing")

    none = L.retrieve("completely unrelated aviation terminology", store)
    ok &= check("nothing relevant means nothing sent", not none,
                "%d returned" % len(none))
    ok &= check("empty text retrieves nothing", not L.retrieve("", store))

    # Deterministic: the same query twice gives the same answer.
    a = [e["id"] for e in L.retrieve("the quarterly report for region 3", store)]
    b = [e["id"] for e in L.retrieve("the quarterly report for region 3", store)]
    ok &= check("the same query gives the same result", a == b)
    return ok


def test_proposal_refuses_what_it_cannot_generalise():
    print("\n  9. it declines rather than inventing a rule")
    ok = True
    for heard, wanted, why_contains in [
        ("same text", "same text", "already correct"),
        ("", "something", "nothing to compare"),
        ("the report", "the report today", "only adds words"),
    ]:
        proposal, why = L.propose(heard, wanted)
        ok &= check("%-34s refused" % (heard or "(empty)")[:34],
                    proposal is None, why)
        ok &= check("%-34s    explains why" % (heard or "(empty)")[:34],
                    why_contains in why, why)
    return ok


def main():
    results = [test_a_correction_is_applied_next_time(),
               test_only_bounded_rules_become_global(),
               test_nothing_is_learned_without_confirmation(),
               test_every_rule_is_reversible(),
               test_longest_rule_wins(),
               test_storage_survives_a_bad_file(),
               test_storage_round_trip_is_atomic(),
               test_retrieval_sends_at_most_three(),
               test_proposal_refuses_what_it_cannot_generalise()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
