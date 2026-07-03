#!/usr/bin/env python3
"""
Ads-domain deterministic pre-filter -- PLAN.md Phase 1 / Step 10 (the gate
agent's first stage, ahead of the `claude -p` relevance judge).

**This taxonomy is authored fresh for ads-keyword/search-intent semantics --
it is NOT a reuse of `job_market_agent/classify.py`'s `INCLUDE`/`EXCLUDE`
role-title taxonomy or `job_market_agent/scoring.py`'s emerging-role
buckets.** Those two modules classify *job titles* (is this LinkedIn/Naukri
posting an AI/ML role?); this module classifies *paid-search keyword intent*
(would a career-serious tech professional type this while shopping for IK's
interview-prep/AI-ML upskilling programs?) -- a materially different
classification problem with different false-positive/false-negative costs
(a bad ads-domain call spends real rupees or blocks a real query; a bad
job-title call just mis-buckets a dashboard row).

What IS reused, per PLAN.md's explicit instruction ("reuse the regex-dispatch
*pattern*, author the INCLUDE/EXCLUDE vocabulary fresh"):
  - The regex-dispatch *shape* -- `classify.py`'s `(pattern, label)` tuple
    list, first-match-wins dispatch (its `classify_role()`/`classify_tier()`).
  - The *seed vocabulary* for one input to the domain-match signal --
    `scoring.py`'s `SKILLS` dict (32 AI/ML tooling terms: LangChain, RAG,
    vector DBs, fine-tuning, MCP, PyTorch, AWS/Azure/GCP, etc.), adapted here
    as ads-domain "does this keyword center on an IK program area or
    seed-vocabulary skill" evidence -- one signal among several, not the
    whole taxonomy.

Everything else here -- the FAST_DROP vocabulary (non-tech professions,
K-12/tutoring, free-course-seeking, job-board intent, other-geo, literal
homonyms), the interview/career-switch intent vocabulary, and the
conflict/no-match -> "ambiguous" resolution policy -- is authored directly
against `brains/keyword_relevance_filter.md`'s rubric and worked examples
(the judge brain this pre-filter feeds), grounded in IK's real program
catalog (`brand-profile.json`'s `programs`/`audience_personas`) and the
staging-format real-run sample (`staging/raw_keyword_candidates_2026-07-04.json`).

## Design: conservative by construction

`pre_filter(keyword)` returns exactly one of three verdicts:

- `"pass"`   -- confidently, obviously tech/edtech-relevant. Bypasses the LLM
  entirely (`gate_keywords.py` scores it 0.9 / `relevant`).
- `"drop"`   -- confidently, obviously off-topic. Bypasses the LLM entirely
  (`gate_keywords.py` scores it 0.1 / `irrelevant`).
- `"ambiguous"` -- everything else, INCLUDING any keyword that matches BOTH
  a FAST_PASS and a FAST_DROP pattern (a real conflict, e.g. a job-board
  query that also contains the word "interview"). Ambiguous candidates are
  batched into the `claude -p` relevance judge
  (`brains/keyword_relevance_filter.md`) -- never guessed at deterministically.

This mirrors the judge brain's own explicit calibration bias ("When in
doubt, verdict borderline... reserve irrelevant for keywords you would
confidently defend excluding") one level earlier in the pipeline: the
deterministic layer only ever makes a call it would confidently defend
without a human/LLM in the loop. Bare skill/domain mentions without a
strong prep/career-transition/interview signal (e.g. "python course", "aws
certification worth it 2026", "scrum master certification") are
deliberately left ambiguous rather than fast-passed or fast-dropped --
exactly the keywords `keyword_relevance_filter.md`'s own worked-examples
table scores as `borderline`, not a confident `relevant`/`irrelevant`.
"""
from __future__ import annotations

import re


def _alt(*terms: str) -> str:
    """Non-capturing alternation group from a list of regex fragments."""
    return "(?:" + "|".join(terms) + ")"


def _lookahead_all(*groups: str) -> str:
    """Order-independent "all of these regex groups appear somewhere in the
    string" -- lets a single compiled pattern require e.g. "a domain term AND
    an interview-intent term, in either order" without enumerating every
    permutation."""
    return "".join(f"(?=.*{g})" for g in groups)


# ---------------------------------------------------------------------------
# FAST_PASS vocabulary
# ---------------------------------------------------------------------------
# IK program areas + role vocabulary, grounded in brand-profile.json's
# `programs` list and `keyword_relevance_filter.md`'s "IK program areas"
# section. Deliberately does NOT include bare generic terms whose relevance
# hinges on accompanying intent (e.g. bare "python", "aws", "product
# manager", "scrum master") -- see the module docstring's conservative-design
# note.
_IK_PROGRAM_ROLE_TERMS = [
    r"machine learning", r"deep learning", r"data science", r"data engineering",
    r"data scientist", r"data engineer\w*", r"generative ai", r"gen[\s-]?ai",
    r"\bllm\b", r"large language models?", r"agentic ai", r"ai agent",
    r"\bai/ml\b", r"\bml/ai\b", r"artificial intelligence", r"\bnlp\b",
    r"natural language processing", r"computer vision",
    r"system design", r"software engineer\w*", r"\bswe\b", r"software developer",
    r"full[\s-]?stack", r"front[\s-]?end develop\w*", r"back[\s-]?end develop\w*",
    r"android develop\w*", r"ios develop\w*", r"site reliability engineer\w*",
    r"\bsre\b", r"security engineering", r"cloud engineering", r"engineering manager",
    r"technical program manager", r"\btpm\b", r"\bdevops\b", r"\bmlops\b",
    r"coding interview", r"\bleetcode\b", r"\bfaang\+?\b", r"\bmaang\+?\b",
]

# 32-term AI/ML skills seed vocabulary, adapted from
# `job_market_agent/scoring.py`'s `SKILLS` dict (job_market_agent/scoring.py,
# lines 42-55) -- same terms, ads-domain word-boundary regex forms.
_SKILLS_VOCAB_TERMS = [
    r"langchain", r"langgraph", r"llama\s?index", r"crew\s?ai", r"autogen",
    r"semantic kernel", r"\bopenai\b", r"\banthropic\b", r"\bclaude\b", r"\bgemini\b",
    r"\bllama\b", r"\bmistral\b", r"hugging\s?face", r"\brag\b",
    r"retrieval[\s-]?augmented", r"vector (?:db|database|store)", r"\bfaiss\b",
    r"\bchroma\b", r"\bqdrant\b", r"\bpinecone\b", r"\bweaviate\b", r"\bpgvector\b",
    r"fine[\s-]?tun\w*", r"\blora\b", r"\bpeft\b", r"\brlhf\b", r"prompt engineer\w*",
    r"\bmcp\b", r"model context protocol", r"\bvllm\b", r"\blangsmith\b",
    r"\blangfuse\b", r"\bragas\b", r"guardrails?", r"\bmlflow\b", r"\bkubeflow\b",
    r"\bsagemaker\b", r"vertex ai", r"\bpytorch\b", r"\btensorflow\b",
    r"\bkubernetes\b", r"\bk8s\b", r"\baws\b", r"\bazure\b", r"\bgcp\b", r"google cloud",
]

DOMAIN_GROUP = _alt(*_IK_PROGRAM_ROLE_TERMS, *_SKILLS_VOCAB_TERMS)

# Prep/interview intent -- bare "interview" deliberately catches "mock
# interview" / "interview prep" / "interview questions" as substrings too.
INTERVIEW_GROUP = _alt(r"interview")

# Explicit career-switch / structured-program intent. Deliberately narrow
# (literal phrases, not a bare "switch"/"transition") so a genuinely
# ambiguous persona case (e.g. "how to switch from support role to software
# developer" -- `keyword_relevance_filter.md`'s own borderline worked
# example) is NOT fast-passed; it is left ambiguous for the LLM to weigh
# persona plausibility.
CAREER_SWITCH_GROUP = _alt(
    r"career switch", r"career transition", r"career pivot",
    r"pivot(?:ing)? to (?:ai|tech|software|ml|machine learning|data science)",
    r"switch(?:ing)? (?:to|into) (?:tech|software engineering|ai|machine learning|data science)",
    r"\bbootcamp\b", r"\bmasterclass\b", r"upskilling program", r"cohort program",
)

FAST_PASS: list[tuple[re.Pattern, str]] = [
    (re.compile(_lookahead_all(DOMAIN_GROUP, INTERVIEW_GROUP), re.I),
     "tech/edtech domain or AI/ML skill-vocabulary term + interview/prep intent "
     "present (order-independent)"),
    (re.compile(_lookahead_all(DOMAIN_GROUP, CAREER_SWITCH_GROUP), re.I),
     "tech/edtech domain or AI/ML skill-vocabulary term + explicit "
     "career-switch/bootcamp/masterclass/cohort-program intent present"),
    (re.compile(r"\bfaang\+?\b", re.I),
     "explicit FAANG target-company vocabulary -- IK's core persona shorthand"),
    (re.compile(r"\bmaang\+?\b", re.I),
     "explicit MAANG+ target-company vocabulary -- IK's core persona shorthand"),
    (re.compile(r"\bedgeup\b", re.I),
     "literal IK program name (EdgeUp, brand-profile.json programs list)"),
    (re.compile(r"\bagentic ai\b", re.I),
     "IK flagship program area (Agentic AI, brand-profile.json programs list) -- "
     "always relevant regardless of accompanying intent wording"),
]


# ---------------------------------------------------------------------------
# FAST_DROP vocabulary -- grounded in keyword_relevance_filter.md's
# "Explicitly IRRELEVANT categories" section.
# ---------------------------------------------------------------------------
_NON_TECH_PROFESSION_TERMS = [
    r"\bnursing\b", r"\bnurse\b", r"\bmba\b", r"\bupsc\b", r"\bssc\b",
    r"banking exam", r"civil services exam", r"\bteaching\b", r"\bteacher\b",
    r"\bhr executive\b", r"human resources", r"\bmedicine\b", r"\bmedical\b",
    r"\blaw\b", r"\badvocate\b", r"civil engineer\w*", r"mechanical engineer\w*",
    r"chartered accountant", r"\bcma\b", r"\bcfa\b", r"\bphysician\b", r"\bpharma\b",
    r"\bradiolog\w*", r"\bdentist\b",
]

_K12_TERMS = [
    r"class\s?\d+\b", r"\bschool\b", r"board exam", r"\bjee\b", r"\bneet\b",
    r"homework help", r"\btuition\b", r"for kids", r"kids learning",
]

_SELF_HELP_TERMS = [
    r"motivational quotes", r"career motivation quotes", r"morning routine of",
]

# Free/pirated-content-seeking intent -- keyword_relevance_filter.md: "A skill
# term plus 'free' is still a monetization-hostile intent."
_FREE_SEEKER_PATTERN = r"\bfree\b.*\b(?:course|courses|certificate|bootcamp|training|class|download|pdf)\b"
_OTHER_FREE_PATTERNS = [
    r"\bcracked course\b", r"\btorrent\b", r"pdf dump", r"course free download",
]

_JOB_BOARD_TERMS = [
    r"\bjobs?\b", r"\bvacanc(?:y|ies)\b", r"walk[\s-]?in interview",
    r"recruitment 20\d\d", r"salary in hand", r"hiring now",
]

_HOMONYM_PATTERNS = [
    r"python.*(?:snake|reptile|pet)", r"(?:snake|reptile).*python",
    r"java (?:island|coffee)", r"vitamin.*\bc\b", r"\bc\b.*vitamin",
]

_OTHER_GEO_TERMS = [r"\bh1b\b", r"visa sponsorship", r"study visa"]

FAST_DROP: list[tuple[re.Pattern, str]] = [
    (re.compile(_alt(*_NON_TECH_PROFESSION_TERMS), re.I),
     "non-tech profession/exam vocabulary (nursing/MBA/UPSC/civil-mechanical "
     "engineering/CA-CMA-CFA/medicine/law/etc.) -- outside IK's tech/edtech domain"),
    (re.compile(_alt(*_K12_TERMS), re.I),
     "K-12 / academic-tutoring vocabulary (school/board-exam/JEE/NEET/tuition) -- "
     "wrong audience for IK's experienced-tech-professional programs"),
    (re.compile(_alt(*_SELF_HELP_TERMS), re.I),
     "generic motivational/self-help intent, not tech interview-prep/upskilling intent"),
    (re.compile(_FREE_SEEKER_PATTERN, re.I),
     "free-course/certificate-seeking intent -- monetization-hostile even when "
     "the topic itself is technical"),
    (re.compile(_alt(*_OTHER_FREE_PATTERNS), re.I),
     "free-content/pirated-course-seeking intent"),
    (re.compile(_alt(*_JOB_BOARD_TERMS), re.I),
     "job-board/employment-seeking intent (jobs/vacancy/walk-in/recruitment/salary "
     "in hand), not prep/upskilling shopping intent"),
    (re.compile(_alt(*_HOMONYM_PATTERNS), re.I),
     "literal non-tech homonym (python the snake / java the island or coffee / "
     "vitamin C), zero tech intent"),
    (re.compile(_alt(*_OTHER_GEO_TERMS), re.I),
     "other-geo intent (H1B/visa sponsorship) not served by IK's India campaigns"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def pre_filter(keyword: str) -> tuple[str, str]:
    """Deterministic ads-domain pre-filter. Returns `(verdict, reason)` where
    `verdict` is one of `"pass"`, `"drop"`, `"ambiguous"`.

    Conservative by construction (see module docstring): a keyword matching
    BOTH a FAST_PASS and a FAST_DROP pattern is a genuine conflict, not a
    coin-flip resolved here -- it goes to `"ambiguous"` with both matched
    reasons cited, same as a keyword matching neither list. Only an
    unambiguous single-side match is fast-classified.
    """
    kw = (keyword or "").strip()
    if not kw:
        return "ambiguous", ("empty/whitespace-only keyword -- no deterministic "
                              "signal, routed to the LLM judge")

    pass_hit = next(((rx, reason) for rx, reason in FAST_PASS if rx.search(kw)), None)
    drop_hit = next(((rx, reason) for rx, reason in FAST_DROP if rx.search(kw)), None)

    if pass_hit and drop_hit:
        return "ambiguous", (
            f"conflicting deterministic signals -- matched FAST_PASS "
            f"({pass_hit[1]}) AND FAST_DROP ({drop_hit[1]}); routed to the LLM judge"
        )
    if pass_hit:
        return "pass", pass_hit[1]
    if drop_hit:
        return "drop", drop_hit[1]
    return "ambiguous", ("no deterministic FAST_PASS/FAST_DROP signal matched -- "
                          "routed to the LLM judge")
