"""
Agent Life Space — Deterministic Telegram intent handlers.

Several common Telegram requests must NOT fall through to the
generic LLM/provider flow:

  * presence pings (``are you there?``, ``hi``, ``si tu?``)
  * version queries (``what version?``, ``aká je verzia?``)
  * skills / capability queries (``what skills do you have?``)
  * self-update questions (``can you update yourself?``)
  * self-update imperatives (``update yourself``, ``nasad novú verziu``)
  * natural-language web open/read (``open obolo.tech``)

The classifier in :mod:`agent.brain.dispatcher` is intentionally
narrow — it caps detector inputs at ~6 words and skips entirely on
short follow-up messages because old detectors had too many false
positives. That conservative behavior is correct for status-style
requests but it lets the intents above leak into the LLM where they
either hallucinate, hang on a Claude CLI permission prompt, or spawn
an unsupported tool-use loop.

This module is the explicit, deterministic safety net. It runs
**before** the LLM in :mod:`agent.core.brain`, ignores history-length
heuristics, and runs whether or not the chat already has prior
turns. Each handler returns plain text and never calls the LLM
provider.

The default response language is English. The user is free to talk
to the agent in any language; multilingual phrasing is recognised
in the detection patterns (English + Slovak / Spanish where the
phrase is short and stable). The detection uses simple, explicit
pattern lists rather than fuzzy classification — false positives
here are far worse than false negatives because we are bypassing
the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────
# Intent enum (string values for logging clarity)
# ─────────────────────────────────────────────

PRESENCE = "presence"
VERSION = "version"
SKILLS = "skills"
CAPABILITY = "capability"
SELF_UPDATE_QUESTION = "self_update_question"
SELF_UPDATE_IMPERATIVE = "self_update_imperative"
WEB_OPEN = "web_open"
COMPARISON = "comparison"          # "how are you different from X?"
SELF_DESCRIPTION = "self_description"  # "what are your strengths/weaknesses?"
MEMORY_USAGE = "memory_usage"      # "do you actually use those memories?"
MEMORY_HORIZON = "memory_horizon"  # "how many turns back do you remember?"
MEMORY_LIST = "memory_list"        # "what are your memories?"
CONTEXT_RECALL = "context_recall"  # "why did you start this topic?"
AUTONOMY = "autonomy"              # "how autonomous are you?"
COMPLEX_TASK = "complex_task"      # "what kind of complex task can I give you?"
LIMITS = "limits"                  # "what can't you do?"
PROJECT_STATUS = "project_status"  # "what's the project state?", "aký je stav projektu?"
WEATHER_REPORT_SETUP = "weather_report_setup"  # "every morning send me weather in X"
WEATHER_REPORT_CITY_REPLY = "weather_report_city_reply"  # plain city after follow-up


@dataclass
class IntentMatch:
    """Result of intent detection."""

    intent: str
    payload: dict[str, Any]


# ─────────────────────────────────────────────
# Pattern tables
# ─────────────────────────────────────────────

# Presence pings — short keep-alive style messages. We accept these
# only as the entire message; appended noise is fine but a longer
# question turns into a real LLM request again.
_PRESENCE_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^\s*(hi|hello|hey|yo|hola|ahoj|čau|cau|zdravím|zdravim)[\s!\.\?]*$",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*are\s+you\s+(there|alive|here)\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*(you\s+there|still\s+there)\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*si\s+(tu|tam)\s*\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*nič\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*nic\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*ping\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*halo\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*haló\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*(how\s+alive|how\s+much\s+alive)\s+are\s+you\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*ako\s+(veľmi|velmi)\s+živý\s+si\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*ako\s+(veľmi|velmi)\s+zivy\s+si\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*(žiješ|zijes)\??\s*$", re.IGNORECASE),
)

# Version intent — operator wants to know what version is running.
#
# Patterns deliberately require an interrogative context so they
# don't grab imperatives like "stiahni novú verziu a nasaď". The
# self-update detector runs FIRST in detect_intent() but the bare
# "verziu" / "version" terminator regex was matching "stiahni novú
# verziu" too eagerly, before the imperative detector got its turn.
_VERSION_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwhat\s+version\b", re.IGNORECASE),
    re.compile(r"\bwhich\s+version\b", re.IGNORECASE),
    re.compile(r"\bcurrent\s+version\b", re.IGNORECASE),
    re.compile(r"^\s*version\s*\??\s*$", re.IGNORECASE),
    re.compile(r"\b(akej|aká|aka|ktorej|ktorá|ktora)\s+verzii?\b", re.IGNORECASE),
    re.compile(r"\b(aká|aka)\s+je\s+verzia\b", re.IGNORECASE),
    re.compile(r"\bna\s+akej\s+verzii\b", re.IGNORECASE),
    # Only match the bare noun if it is the WHOLE message (no
    # leading verbs like "stiahni" / "nasaď").
    re.compile(r"^\s*(verzia|verziu|verzie)\??\s*$", re.IGNORECASE),
)

# Skills query — user wants the list of declared skills.
_SKILLS_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(your|list\s+(of\s+)?your)\s+skills\b", re.IGNORECASE),
    re.compile(r"\b(what|which)\s+skills\s+do\s+you\s+have\b", re.IGNORECASE),
    re.compile(r"^\s*skills\s*\??\s*$", re.IGNORECASE),
    re.compile(r"\b(aké|ake|čo|co)\s+(máš|mas)\s+skills?\b", re.IGNORECASE),
    re.compile(r"\bzoznam\s+skills?\b", re.IGNORECASE),
    re.compile(r"\b(tvoje|tvoj)\s+skills?\b", re.IGNORECASE),
    re.compile(r"\bschopnost(i|í)\b", re.IGNORECASE),
)

# Capability overview — closely related to skills but answers in
# a more narrative form. The set of phrases is intentionally narrow.
_CAPABILITY_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwhat\s+can\s+you\s+do\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+are\s+you\s+capable\s+of\b", re.IGNORECASE),
    re.compile(r"\b(your|list\s+your)\s+capabilit(y|ies)\b", re.IGNORECASE),
    re.compile(r"\bčoho\s+si\s+schopn(ý|y)\b", re.IGNORECASE),
    re.compile(r"\bcoho\s+si\s+schopn(ý|y)\b", re.IGNORECASE),
    re.compile(r"\b(čo|co)\s+vieš\s+robi(ť|t)\b", re.IGNORECASE),
    re.compile(r"\b(čo|co)\s+všetko\s+vieš\b", re.IGNORECASE),
    re.compile(r"\b(aké|ake)\s+capabilit(y|ies)\b", re.IGNORECASE),
)

# Self-update **question** (just asking about the capability).
_SELF_UPDATE_QUESTION_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bcan\s+you\s+(deploy|install|update|upgrade)\s+(yourself|a\s+new\s+version)\b", re.IGNORECASE),
    re.compile(r"\bare\s+you\s+able\s+to\s+(update|upgrade|deploy|install)\s+(yourself|a\s+new\s+version)\b", re.IGNORECASE),
    re.compile(r"\bdo\s+you\s+have\s+(a\s+)?(self[\s-]?update|self[\s-]?deploy)\s+capabilit", re.IGNORECASE),
    re.compile(r"\bvieš\s+si\s+nasadi(ť|t)\s+nov", re.IGNORECASE),
    re.compile(r"\bvieš\s+sa\s+(aktualizov|update)", re.IGNORECASE),
    re.compile(r"\bvieš\s+si\s+(stiahnu|stiahnu(ť|t))\s+nov", re.IGNORECASE),
    re.compile(r"\bvieš\s+si\s+update", re.IGNORECASE),
    re.compile(r"\bmáš\s+capabilit(y|u)\s+(aktualizov|update|nasadi|deploy)", re.IGNORECASE),
    re.compile(r"\bmas\s+capabilit(y|u)\s+(aktualizov|update|nasadi|deploy)", re.IGNORECASE),
)

# ─────────────────────────────────────────────
# Heuristic fallback for paraphrased self-update *questions*
# ─────────────────────────────────────────────
#
# The regexes above match the canonical phrasings, but operators ask
# the same question in many ways:
#
#   * "vraj maš novu verziu kde si schopny si aj nasadit nove veci k sebe"
#   * "je pravda že sa už vieš sám aktualizovať?"
#   * "už si vieš nasadiť nové veci k sebe?"
#   * "máš capability aktualizovať sám seba?"
#
# A capability *question* about self-update has THREE signals that must
# all coexist:
#
#   1. A self-reference token  (sám / seba / sebe / k sebe / yourself / itself)
#   2. A deploy/update verb    (nasadiť / aktualizov / update / deploy / install /
#                                upgrade / stiahnu / pull / nahodiť)
#   3. A question marker       (ends with "?", or contains a question opener like
#                                "vieš", "je pravda", "vraj", "už", "can you",
#                                "are you", "do you", "is it true")
#
# Crucially this must NOT fire on imperatives ("nasad novú verziu u seba")
# because the imperative has its own dedicated detector that runs first.
# We also require that the message NOT start with a known imperative verb
# as a belt-and-braces guard.

_SELF_REFERENCE_TOKENS: tuple[str, ...] = (
    "sám", "sam", "seba", "sebe", "k sebe", "ku sebe",
    "yourself", "itself", "self",
)

_SELF_UPDATE_VERB_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bnasadi(ť|t)\b", re.IGNORECASE),
    re.compile(r"\bnasadit\b", re.IGNORECASE),
    re.compile(r"\bnasad(í|i)t\b", re.IGNORECASE),
    re.compile(r"\bnahodi(ť|t)\b", re.IGNORECASE),
    re.compile(r"\baktualizov", re.IGNORECASE),
    re.compile(r"\bupdate(?!\s*-?\s*ni\s+sa)\b", re.IGNORECASE),
    re.compile(r"\bupgrade\b", re.IGNORECASE),
    re.compile(r"\bdeploy\b", re.IGNORECASE),
    re.compile(r"\binstall\b", re.IGNORECASE),
    re.compile(r"\bstiahnu(ť|t)\b", re.IGNORECASE),
    re.compile(r"\bpull\s+(the\s+)?latest\b", re.IGNORECASE),
    re.compile(r"\bnov(ú|u|ej|e)\s+verziu\b", re.IGNORECASE),
    re.compile(r"\bnew\s+version\b", re.IGNORECASE),
)

_QUESTION_OPENERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bvieš\b", re.IGNORECASE),
    re.compile(r"\bvies\b", re.IGNORECASE),
    re.compile(r"\bmôžeš\b", re.IGNORECASE),
    re.compile(r"\bmozes\b", re.IGNORECASE),
    re.compile(r"\bje\s+pravda\b", re.IGNORECASE),
    re.compile(r"\bje\s+to\s+tak\b", re.IGNORECASE),
    re.compile(r"\bvraj\b", re.IGNORECASE),
    re.compile(r"\buž\s+", re.IGNORECASE),
    re.compile(r"\buz\s+", re.IGNORECASE),
    re.compile(r"\bcan\s+you\b", re.IGNORECASE),
    re.compile(r"\bare\s+you\b", re.IGNORECASE),
    re.compile(r"\bdo\s+you\b", re.IGNORECASE),
    re.compile(r"\bis\s+it\s+true\b", re.IGNORECASE),
    re.compile(r"\bmáš\b", re.IGNORECASE),
    re.compile(r"\bmas\b", re.IGNORECASE),
)


def _looks_like_self_update_question(text: str) -> bool:
    """Heuristic fallback for paraphrased self-update capability questions.

    Returns ``True`` only when ALL three signals coexist:
      1. self-reference token
      2. deploy/update verb
      3. question marker
    AND the message is not an imperative.
    """
    stripped = text.strip()
    if not stripped:
        return False

    lowered = stripped.lower()

    # Imperative guard: if any imperative regex matches, defer to that
    # branch instead.
    for imp in _SELF_UPDATE_IMPERATIVE_REGEXES:
        if imp.search(stripped):
            return False

    has_self_ref = any(token in lowered for token in _SELF_REFERENCE_TOKENS)
    if not has_self_ref:
        return False

    has_verb = any(p.search(stripped) for p in _SELF_UPDATE_VERB_PATTERNS)
    if not has_verb:
        return False

    is_question = (
        stripped.rstrip().endswith("?")
        or any(opener.search(stripped) for opener in _QUESTION_OPENERS)
    )
    if not is_question:
        return False

    return True


# ─────────────────────────────────────────────
# Heuristic fallback for paraphrased self-update *imperatives*
# ─────────────────────────────────────────────
#
# The regex tables above match the canonical phrasings, but operators
# write imperatives in many free forms:
#
#   * "stiahni si nový kód z githubu a nahoď ho"
#   * "vezmi si najnovšiu verziu a nasaď"
#   * "spusti deploy"
#   * "nahoď to čo je na main"
#   * "git pull a reštart"
#   * "naťahaj nový kód a aktualizuj sa"
#
# A self-update *imperative* has FOUR signals that must all coexist:
#
#   1. The first non-trivial token is an imperative deploy/fetch verb
#   2. There is at least one self-update target noun in the message
#      (verziu / version / kód / code / update / latest / main / release / ...)
#   3. The message is NOT a question (no `?`, no question opener)
#   4. The message does NOT mention an explicit non-self target
#      (obrázok / image / film / video / pdf / súbor / file / fotku / ...)
#
# Precision matters more than recall: false positives here would
# trigger an actual `git pull --ff-only` + (when supervisor + flag
# are set) a process restart. The negative test set includes
# everything we explicitly do NOT want to match.

_IMPERATIVE_VERBS_LEAD: tuple[str, ...] = (
    # Slovak / Czech
    "stiahni", "stiahnite", "stiahnúť", "stiahnut", "stiahnime",
    "nasaď", "nasad", "nasadit", "nasadiť", "nasaďte", "nasadte",
    "nahoď", "nahod", "nahodit", "nahoďte", "nahodte",
    "aktualizuj", "aktualizujme", "aktualizujte",
    "vezmi", "vezmite", "vezmime",
    "naťahaj", "natahaj",
    "stiahnime", "natiahnime", "natiahni",
    "spusti", "spustite", "spravme", "sprav",
    "rozbeh", "rozbehni", "rozbehnime",
    "nainštaluj", "nainstaluj", "nainštalujme", "nainstalujme",
    # English
    "update", "deploy", "install", "pull", "fetch", "download",
    "redeploy", "rerun", "rebuild", "release",
    "get", "grab",
    # git
    "git",
)

_SELF_UPDATE_TARGET_NOUNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bnov(?:ú|u|ej|e|ý|y)\s+(?:verziu|verzia|verzie|kód|kod|code|veci)\b", re.IGNORECASE),
    re.compile(r"\bnajnov(?:ši|si|šiu|siu|šie|sie|šia|sia)\b", re.IGNORECASE),
    re.compile(r"\b(?:verziu|verzie|verzia)\b", re.IGNORECASE),
    re.compile(r"\b(?:version|versions)\b", re.IGNORECASE),
    re.compile(r"\b(?:kód|kod|code|sources?)\b", re.IGNORECASE),
    re.compile(r"\b(?:update|updates|upgrade|upgrades|patch|patches)\b", re.IGNORECASE),
    re.compile(r"\b(?:latest|newest|new\s+stuff)\b", re.IGNORECASE),
    re.compile(r"\bnovink(?:y|u|ami)\b", re.IGNORECASE),
    re.compile(r"\b(?:main|master|hlavn[áae]j?\s*(?:vetv[aey]|branch))\b", re.IGNORECASE),
    re.compile(r"\b(?:github|gitlab|git|repo|repository|remote)\b", re.IGNORECASE),
    re.compile(r"\b(?:release|releases|deploy|deployment|prod|production)\b", re.IGNORECASE),
)

_NON_SELF_TARGETS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:obrázok|obrazok|obrázky|obrazky|fotk(?:u|y|a))\b", re.IGNORECASE),
    re.compile(r"\b(?:súbor|subor|súbory|subory|file|files)\b", re.IGNORECASE),
    re.compile(r"\b(?:pdf|docx?|xlsx?|csv|json|yaml|yml|txt|zip)\b", re.IGNORECASE),
    re.compile(r"\b(?:film|filmy|movie|movies|video|videá|videa)\b", re.IGNORECASE),
    re.compile(r"\b(?:image|images|picture|pictures|photo|photos)\b", re.IGNORECASE),
    re.compile(r"\b(?:song|songs|music|hudba|skladbu|skladby|album|albumy)\b", re.IGNORECASE),
    re.compile(r"\b(?:milk|mlieko|chlieb|bread|food|jedlo|fridge|chladničk)\b", re.IGNORECASE),
)


def _looks_like_self_update_imperative(text: str) -> bool:
    """Heuristic fallback for paraphrased self-update imperatives.

    Returns ``True`` only when ALL four signals coexist:
      1. First non-trivial token is an imperative deploy/fetch verb.
      2. The message contains a self-update target noun.
      3. The message is NOT a question.
      4. The message does NOT mention an explicit non-self target.

    The heuristic is intentionally precision-first because a false
    positive triggers a real `git pull --ff-only` (and, with the
    self-restart flag, a process restart).
    """
    stripped = text.strip()
    if not stripped:
        return False

    # Strip leading interjections like "ok," / "hej," / "prosim," so
    # the first-token check can still see the imperative verb.
    cleaned = re.sub(
        r"^\s*(?:ok|hej|prosím|prosim|please|hey|prosímťa|prosimta)\s*[,:]?\s*",
        "",
        stripped,
        flags=re.IGNORECASE,
    )
    if not cleaned:
        return False

    # Tokenize the first word; tolerate Slovak diacritics.
    first_token = re.split(r"\s+", cleaned, maxsplit=1)[0].lower()
    first_token_clean = first_token.rstrip(",.:;!?")
    # Strip pronoun particles like "stiahni si" / "nahoď ho" — only
    # the leading verb counts for matching.
    if first_token_clean not in _IMPERATIVE_VERBS_LEAD:
        return False

    # Question guard: anything that ends with "?" or starts with a
    # question opener is NOT an imperative.
    if cleaned.rstrip().endswith("?"):
        return False
    for opener in _QUESTION_OPENERS:
        if opener.search(cleaned):
            return False

    # Exclude messages that name an explicit non-self target.
    for excl in _NON_SELF_TARGETS:
        if excl.search(cleaned):
            return False

    # Require at least one self-update target noun.
    has_target = any(p.search(cleaned) for p in _SELF_UPDATE_TARGET_NOUNS)
    if not has_target:
        # Special case: "git pull" / "git fetch" / "git fetch + restart"
        # are unambiguous self-update commands even without an explicit
        # target noun.
        if re.match(r"^\s*git\s+(pull|fetch|reset|checkout)\b", cleaned, re.IGNORECASE):
            return True
        return False

    return True

# Self-update **imperative** — operator is telling the agent to do it.
#
# Three families:
#   1. Single-verb imperatives ("update yourself", "deploy latest",
#      "nasad novú verziu", "aktualizuj sa", ...)
#   2. Download+deploy combos where the operator chains two verbs
#      ("stiahni si novú verziu a nasaď to", "pull and deploy",
#      "download and deploy latest"). These are the most natural
#      operator phrasings and we treat the whole thing as a single
#      self-update intent.
#   3. Standalone "stiahni" / "download" / "pull" against the agent
#      itself — implicit deploy because the agent only knows how to
#      ff-only update its own code, there is no "download but don't
#      install" mode.
_SELF_UPDATE_IMPERATIVE_REGEXES: tuple[re.Pattern[str], ...] = (
    # Family 1: single-verb canonical imperatives.
    re.compile(r"^\s*update\s+yourself\b", re.IGNORECASE),
    re.compile(r"^\s*deploy\s+(the\s+)?latest(\s+version)?\b", re.IGNORECASE),
    re.compile(r"^\s*self[\s-]?update\b", re.IGNORECASE),
    re.compile(r"^\s*pull\s+(the\s+)?latest\b", re.IGNORECASE),
    re.compile(r"^\s*nasa(ď|d)\s+(nov(ú|u)\s+verziu|update)", re.IGNORECASE),
    re.compile(r"^\s*aktualizuj\s+sa\b", re.IGNORECASE),
    re.compile(r"^\s*update[\s-]?ni\s+sa\b", re.IGNORECASE),

    # Family 2: download + deploy combos. Order: stiahni / pull /
    # download somewhere AND nasad / deploy / nahod / install
    # somewhere (not necessarily adjacent).
    re.compile(
        r"^\s*stiahn(?:i|ime|úť)\b.*\b(?:nasa(?:ď|d|dit|díme)|"
        r"nahod(?:|i|ime)|nainštaluj|nainstaluj|install|deploy)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:pull|fetch|download)\b.*\b(?:deploy|install|run|"
        r"and\s+restart|restart)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:stiahni|stiahnite)\s+(?:si\s+)?(?:najnov(?:ši|si|šiu)|"
        r"nov(?:ú|u))\s+(?:verziu|kód|kod|veci)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*download\s+(?:the\s+)?(?:latest|new(?:est)?)\s+"
        r"(?:version|code)\b",
        re.IGNORECASE,
    ),

    # Family 3: standalone "stiahni" against the agent itself.
    # We require a noun that ties it to the agent ("verziu", "kód",
    # "update", "novinky") so we don't grab random "stiahni súbor".
    re.compile(
        r"^\s*stiahn(?:i|ite|úť|ime)(?:\s+si)?\s+(?:novú\s+verziu|"
        r"nov(?:ú|u)\s+verziu|najnov(?:šiu|siu)\s+verziu|update|"
        r"novinky|new(?:est)?\s+version)\b",
        re.IGNORECASE,
    ),
)

# Web open/read — natural language. We require an explicit verb so
# that "moja stránka padá" doesn't get re-routed.
_WEB_VERBS = (
    r"(?:open|read|fetch|visit|browse|"
    r"otvor|otvori(?:ť|t)|pozri(?:i)?|pozri\s+sa(?:\s+na)?|"
    r"pre(?:č|c)(?:í|i)taj(?:\s+stránku|\s+web)?|nahliadni(?:\s+do)?|"
    r"načítaj|nacitaj|navštív|navstiv|"
    r"vieš\s+si\s+naštudova(?:ť|t)|vieš\s+si\s+pozrieť)"
)
_WEB_REGEX = re.compile(
    r"\b" + _WEB_VERBS + r"\s+(?:the\s+)?(?:page|site|website|"
    r"stránku\s+|stranku\s+|web\s+|webovú\s+stránku\s+|webovu\s+stranku\s+)?"
    r"(?P<target>[A-Za-z0-9][\w\-\.]+(?:\.[A-Za-z]{2,})(?:/\S*)?|https?://\S+)",
    re.IGNORECASE,
)

# Comparison intent — "how are you different from X" / "are you better than X".
# We capture the *subject* being compared so we can fail-safe on unknown
# external systems instead of hallucinating facts about them.
_COMPARISON_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(how|in\s+what(?:\s+way)?|in\s+which\s+way)\s+(are|is)\s+you\s+"
        r"(different|better|worse|distinct|unique)\s+(from|than|to)\s+"
        r"(?P<subject>[A-Za-z0-9][\w\.\- ]{0,40})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(better|worse|different)\s+than\s+(?P<subject>[A-Za-z0-9][\w\.\- ]{0,40})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bv\s+čom\s+si\s+(iný|iny|lepší|lepsi|horší|horsi|odlišný|odlisny|"
        r"unikátny|unikatny)\s+(?:ako|než|nez)\s+(?P<subject>[A-Za-z0-9][\w\.\- ]{0,40})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bv\s+čom\s+si\s+(lepší|lepsi|horší|horsi)\s+(?:ako|než|nez)\s+"
        r"(?P<subject>iní\s+agenti|ini\s+agenti|other\s+agents|iní)",
        re.IGNORECASE,
    ),
)

# Pure self-description (no external subject) — "what's your advantage?"
_SELF_DESCRIPTION_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(what'?s|tell\s+me)\s+your\s+(advantage|edge|strengths?)\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+(should\s+i\s+use\s+you|you|use\s+you)\b", re.IGNORECASE),
    re.compile(r"\bhonest\s+(self|answer|opinion)\b", re.IGNORECASE),
    re.compile(r"\bdescribe\s+yourself\b", re.IGNORECASE),
    re.compile(r"\b(čo|co)\s+je\s+tvoja\s+(výhoda|vyhoda|prednosť|prednost)\b", re.IGNORECASE),
    re.compile(r"\b(úprimná|uprimna|úprimne|uprimne)\s+(odpov|seba)", re.IGNORECASE),
    re.compile(r"\b(tvoje|tvoja)\s+(silné|silne)\s+stránk(y|a)\b", re.IGNORECASE),
)

# Limits — "what can't you do?", "what are your limits?"
_LIMITS_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwhat\s+can'?t\s+you\s+do\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(are\s+)?your\s+limit(s|ations)?\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+do\s+you\s+(not|n'?t)\s+(know|do|support)\b", re.IGNORECASE),
    re.compile(r"\b(čo|co)\s+nevieš\b", re.IGNORECASE),
    re.compile(r"\baké\s+máš\s+limit(y|ov)?\b", re.IGNORECASE),
    re.compile(r"\bake\s+mas\s+limit(y|ov)?\b", re.IGNORECASE),
    re.compile(r"\b(tvoje|tvoja)\s+(slabšie|slabsie)\s+(stránky|stranky|miesta)\b", re.IGNORECASE),
)

# Memory usage — "do you actually use those memories?", "how does memory work?"
_MEMORY_USAGE_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bdo\s+you\s+(actually\s+)?use\s+(those\s+|your\s+)?memori", re.IGNORECASE),
    re.compile(r"\bhow\s+does\s+(your\s+)?memory\s+work\b", re.IGNORECASE),
    re.compile(r"\bcan\s+you\s+(remember|recall)\b", re.IGNORECASE),
    re.compile(r"\bvieš\s+(tie\s+)?spomienky\s+(aj\s+)?použ", re.IGNORECASE),
    re.compile(r"\bako\s+(funguje|používaš|pouzivas)\s+(tvoja\s+|svoju\s+)?pamä(ť|t)\b", re.IGNORECASE),
    re.compile(r"\bvieš\s+si\s+(spomenú|spomenu|pamäta|pamata)", re.IGNORECASE),
)

# Memory list — "what are your memories?", "list your memories"
_MEMORY_LIST_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwhat\s+(are\s+)?your\s+memori", re.IGNORECASE),
    re.compile(r"\blist\s+(your\s+)?memori", re.IGNORECASE),
    re.compile(r"\bshow\s+(me\s+)?(your\s+)?memori", re.IGNORECASE),
    re.compile(r"\b(aké|ake)\s+(sú|su)\s+tvoje\s+spomien", re.IGNORECASE),
    re.compile(r"\b(aké|ake)\s+máš\s+spomien", re.IGNORECASE),
    re.compile(r"\b(zoznam|ukáž|ukaz)\s+(tvoje\s+|svoje\s+)?spomien", re.IGNORECASE),
    re.compile(r"\b(tvoje|tvoja)\s+spomien", re.IGNORECASE),
    re.compile(r"\b(co|čo)\s+si\s+(toho\s+)?(zapamätal|zapamatal)", re.IGNORECASE),
)

# Context recall — "why did you start this topic?", "what were we
# talking about?", "remind me what I said before"
_CONTEXT_RECALL_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwhy\s+(did|are)\s+you\s+(start|talking|on\s+about)", re.IGNORECASE),
    re.compile(r"\bwhat\s+(were|are)\s+we\s+talking\s+about\b", re.IGNORECASE),
    re.compile(r"\bremind\s+me\s+what\s+(i|we)\s+(said|wrote|asked)", re.IGNORECASE),
    re.compile(r"\bwhat\s+did\s+(i|we)\s+(just\s+)?(say|ask|talk\s+about)", re.IGNORECASE),
    re.compile(r"\bcontext\s+of\s+this\s+(chat|conversation)", re.IGNORECASE),
    re.compile(r"\b(prečo|preco)\s+si\s+za(č|c)al\s+(s\s+)?(touto\s+|s\s+touto\s+)?(t(é|e)mou|t(é|e)my)\b", re.IGNORECASE),
    re.compile(r"\b(o\s+čom|o\s+com)\s+sme\s+sa\s+bavili\b", re.IGNORECASE),
    re.compile(r"\b(o\s+čom|o\s+com)\s+(je|bola|je\s+to|to\s+je)\s+(táto\s+|tato\s+)?(rozhovor|konverz|debat)", re.IGNORECASE),
    re.compile(r"\b(čo|co)\s+(som\s+ti|sme\s+ti|som\s+(mu|jej))\s+(písal|napísal|pisal|napisal)\b", re.IGNORECASE),
    re.compile(r"\b(čo|co)\s+sme\s+riešili\b", re.IGNORECASE),
    re.compile(r"\bpripomeň\s+mi\s+(čo|co)\s+sme\b", re.IGNORECASE),
    re.compile(r"\b(z\s+akého|z\s+akeho)\s+dôvodu\s+(si|sme)\b", re.IGNORECASE),
)

# Memory horizon — "how many messages back do you remember?"
_MEMORY_HORIZON_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bhow\s+(many|much)\s+(messages|turns|replies?|back)\s+do\s+you\s+remember\b", re.IGNORECASE),
    re.compile(r"\bhow\s+far\s+back\s+(do\s+you|can\s+you)\s+remember\b", re.IGNORECASE),
    re.compile(r"\bmemory\s+(horizon|window|window\s+size|context\s+size)\b", re.IGNORECASE),
    re.compile(r"\bcontext\s+window\b", re.IGNORECASE),
    re.compile(r"\b(koľko|kolko)\s+(odpovedí|správ|sprav|repli(es|y)|tokens?|turns?|znakov|krokov)\s+dozadu\s+si\s+pamä", re.IGNORECASE),
    re.compile(r"\b(koľko|kolko)\s+si\s+pamätáš\b", re.IGNORECASE),
    re.compile(r"\b(koľko|kolko)\s+si\s+(toho\s+)?pamä", re.IGNORECASE),
    re.compile(r"\baký\s+(je\s+)?(memory|context)\s+(horizon|window)\b", re.IGNORECASE),
)

# Autonomy — "how autonomous are you?", "what can you do on your own?"
_AUTONOMY_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bhow\s+autonomous\s+are\s+you\b", re.IGNORECASE),
    re.compile(r"\b(level\s+of\s+)?autonomy\b", re.IGNORECASE),
    re.compile(r"\b(what|how\s+much)\s+can\s+you\s+do\s+(on\s+your\s+own|by\s+yourself|alone)\b", re.IGNORECASE),
    re.compile(r"\b(akú|aku)\s+(veľkú|velku)?\s*autonómiu\b", re.IGNORECASE),
    re.compile(r"\bautonómi(u|a)\b", re.IGNORECASE),
    re.compile(r"\bautonomi(u|a)\b", re.IGNORECASE),
    re.compile(r"\b(čo|co)\s+(môžeš|mozes|vieš)\s+(robi(ť|t)\s+)?sám\b", re.IGNORECASE),
)

# Weather report setup — "every morning send me weather in Bratislava"
# We capture the city name when present so the handler can finalize
# the setup in one shot. If absent, the handler asks a follow-up.
_WEATHER_SETUP_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:^|\b)(?:every\s+morning|each\s+morning|daily(?:\s+in\s+the)?\s+morning)\s+"
        r"(?:send\s+me|tell\s+me|give\s+me|show\s+me)\s+(?:the\s+)?weather"
        r"(?:\s+(?:in|for|of)\s+(?P<city>[A-Za-zÀ-ž][\w\- ]{1,40}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\b)set\s+up\s+(?:a\s+)?(?:daily\s+|morning\s+)?weather\s+report"
        r"(?:\s+(?:for|in|of)\s+(?P<city>[A-Za-zÀ-ž][\w\- ]{1,40}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\b)i\s+want\s+(?:to\s+know\s+)?(?:the\s+)?weather\s+(?:every\s+|each\s+)?morning"
        r"(?:\s+(?:in|for|of)\s+(?P<city>[A-Za-zÀ-ž][\w\- ]{1,40}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\b)(?:každé\s+ráno|kazde\s+rano|denne\s+ráno|denne\s+rano|"
        r"každý\s+deň\s+ráno|kazdy\s+den\s+rano)\s+"
        r"(?:mi\s+)?(?:pošli\s+|posli\s+|povedz\s+|daj\s+|napíš\s+|napis\s+)?"
        r"(?:po(?:č|c)asie|weather)"
        r"(?:\s+(?:v|vo|pre|do|na)\s+(?P<city>[A-Za-zÀ-ž][\w\- ]{1,40}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\b)nastav(?:\s+(?:mi|si))?\s+(?:ranný\s+|ranny\s+|denný\s+|denny\s+)?"
        r"weather\s+report(?:\s+(?:pre|v|vo|do|na)\s+(?P<city>[A-Za-zÀ-ž][\w\- ]{1,40}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\b)(?:chcem|chceme)\s+(?:každé\s+|kazde\s+|denne\s+|denný\s+|denny\s+)?"
        r"(?:ráno\s+|rano\s+)?(?:vedie(?:ť|t)\s+)?(?:po(?:č|c)asie|weather)"
        r"(?:\s+(?:v|vo|pre|do|na)\s+(?P<city>[A-Za-zÀ-ž][\w\- ]{1,40}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\b)vytvor(?:\s+si)?\s+(?:program\s+(?:čo|co)\s+|job\s+(?:čo|co)\s+|workflow\s+(?:čo|co)\s+)?"
        r"(?:mi\s+)?(?:ráno\s+|rano\s+)?(?:povie\s+|pošle\s+|posle\s+|napíše\s+|napise\s+)?"
        r"(?:po(?:č|c)asie|weather)"
        r"(?:\s+(?:v|vo|pre|do|na)\s+(?P<city>[A-Za-zÀ-ž][\w\- ]{1,40}))?",
        re.IGNORECASE,
    ),
)

# Complex task — "what kind of complex task can I give you?"
_COMPLEX_TASK_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwhat\s+(kind\s+of\s+|sort\s+of\s+)?complex\s+task", re.IGNORECASE),
    re.compile(r"\bwhat\s+can\s+i\s+(give|ask|delegate)\s+(you|to\s+you)\b", re.IGNORECASE),
    re.compile(r"\bbiggest\s+task\b", re.IGNORECASE),
    re.compile(r"\bchallenge\s+you\b", re.IGNORECASE),
    re.compile(r"\baký\s+(komplexný|komplexny)\s+task", re.IGNORECASE),
    re.compile(r"\baky\s+(komplexny)\s+task", re.IGNORECASE),
    re.compile(r"\b(akú|aku)\s+(úlohu|ulohu)\s+(ti\s+)?môžem\s+(da|dať)\b", re.IGNORECASE),
    re.compile(r"\b(co|čo)\s+(ti\s+)?môžem\s+(zadať|zadat|dať|dat)\b", re.IGNORECASE),
)

# Project-status / project-state questions — the kind that otherwise
# fall through to an expensive CLI/Opus LLM call and time out.
# Patterns match Slovak + English variants of "what's the project state",
# "what tests pass", "what's done", "what's not finished", "open problems".
_PROJECT_STATUS_REGEXES: tuple[re.Pattern[str], ...] = (
    # SK: "aký je (aktuálny) stav projektu / ALS / na serveri"
    re.compile(
        r"\b(aký|aky|jaky|jaký)\s+(je\s+)?(aktuálny\s+|aktualny\s+)?"
        r"stav\s+(projektu|als|agenta|servera|na\s+server)",
        re.IGNORECASE,
    ),
    # SK: "čo je hotové / čo ešte nie je hotové / dokončené"
    re.compile(
        r"\b(čo|co)\s+(je\s+)?(dnes\s+)?(hotov[éeá]|dokončen[éeá]|dokonc)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(čo|co)\s+(ešte\s+|este\s+)?(nie\s+je|neni)\s+(hotov|dokončen|dokonc|implemen)",
        re.IGNORECASE,
    ),
    # SK: "koľko testov prechádza"
    re.compile(
        r"\b(koľko|kolko)\s+testov\s+(prechádza|prechadza|prejde|pass)",
        re.IGNORECASE,
    ),
    # SK: "aké sú najväčšie otvorené problémy"
    re.compile(
        r"\b(aké|ake)\s+(sú|su)\s+.{0,20}(problém|problem|bug|issue|otvor)",
        re.IGNORECASE,
    ),
    # EN equivalents
    re.compile(r"\bwhat('s|\s+is)\s+(the\s+)?(current\s+)?project\s+stat", re.IGNORECASE),
    re.compile(r"\bwhat('s|\s+is)\s+(done|finished|completed|ready)\b", re.IGNORECASE),
    re.compile(r"\bwhat('s|\s+is)\s+not\s+(done|finished|completed)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+many\s+tests\s+(pass|fail|run)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(are\s+)?(the\s+)?(biggest|open|main)\s+(problem|issue|bug)", re.IGNORECASE),
    re.compile(r"\bproject\s+status\b", re.IGNORECASE),
    re.compile(r"\bproject\s+state\b", re.IGNORECASE),
)


# ─────────────────────────────────────────────
# Detection
# ─────────────────────────────────────────────


def detect_intent(text: str) -> IntentMatch | None:
    """Return the first matching deterministic intent for *text*.

    The detection order matters: we test the more specific intents
    (self-update imperative, web open) before the generic ones
    (presence, version) so that, e.g., ``aktualizuj sa`` does not get
    classified as a presence ping.
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None

    # 1. Self-update imperative — canonical regexes first, then a
    #    paraphrase heuristic so the user does not have to know the
    #    exact phrasing. Both run before web/version because "update"
    #    is also a generic word.
    if _matches_any(stripped, _SELF_UPDATE_IMPERATIVE_REGEXES):
        return IntentMatch(intent=SELF_UPDATE_IMPERATIVE, payload={})
    if _looks_like_self_update_imperative(stripped):
        return IntentMatch(intent=SELF_UPDATE_IMPERATIVE, payload={})

    # 2. Self-update question — canonical regexes first, then a
    #    paraphrase heuristic so the user does not get a 180s CLI
    #    timeout for "vraj máš novú verziu kde si schopný si aj
    #    nasadiť nové veci k sebe je to tak ?".
    if _matches_any(stripped, _SELF_UPDATE_QUESTION_REGEXES):
        return IntentMatch(intent=SELF_UPDATE_QUESTION, payload={})
    if _looks_like_self_update_question(stripped):
        return IntentMatch(intent=SELF_UPDATE_QUESTION, payload={})

    # 3. Natural-language web open/read.
    web_match = _WEB_REGEX.search(stripped)
    if web_match:
        target = web_match.group("target").strip().rstrip(".,;:!?")
        url = _normalize_url(target)
        if url:
            return IntentMatch(
                intent=WEB_OPEN,
                payload={"url": url, "raw": target},
            )

    # 3.5 Weather report setup — explicit recurring intent. Must run
    #     before the generic comparison/limits/version detectors so
    #     that "every morning send me weather in X" doesn't get
    #     swallowed by anything else.
    for pattern in _WEATHER_SETUP_REGEXES:
        m = pattern.search(stripped)
        if m:
            try:
                city = (m.group("city") or "").strip()
            except (IndexError, KeyError):
                city = ""
            city = city.rstrip(".,;:!?").strip()
            return IntentMatch(
                intent=WEATHER_REPORT_SETUP,
                payload={"city": city},
            )

    # 4. Version.
    if _matches_any(stripped, _VERSION_REGEXES):
        return IntentMatch(intent=VERSION, payload={})

    # 5. Comparison — capture the subject so the handler can fail-safe
    #    on unknown external systems.
    for pattern in _COMPARISON_REGEXES:
        m = pattern.search(stripped)
        if m:
            try:
                subject = (m.group("subject") or "").strip()
            except (IndexError, KeyError):
                subject = ""
            return IntentMatch(
                intent=COMPARISON,
                payload={"subject": subject},
            )

    # 6. Self-description (no external subject).
    if _matches_any(stripped, _SELF_DESCRIPTION_REGEXES):
        return IntentMatch(intent=SELF_DESCRIPTION, payload={})

    # 7. Limits / weaknesses.
    if _matches_any(stripped, _LIMITS_REGEXES):
        return IntentMatch(intent=LIMITS, payload={})

    # 8. Context recall — "why did you start this topic?" / "what
    #    were we talking about?". Must run before the memory family
    #    so the question doesn't bleed into the generic memory
    #    handlers.
    if _matches_any(stripped, _CONTEXT_RECALL_REGEXES):
        return IntentMatch(intent=CONTEXT_RECALL, payload={})

    # 9. Memory list — "what are your memories?" / "list your memories"
    if _matches_any(stripped, _MEMORY_LIST_REGEXES):
        return IntentMatch(intent=MEMORY_LIST, payload={})

    # 10. Memory horizon (specific) before generic memory usage.
    if _matches_any(stripped, _MEMORY_HORIZON_REGEXES):
        return IntentMatch(intent=MEMORY_HORIZON, payload={})

    # 11. Memory usage.
    if _matches_any(stripped, _MEMORY_USAGE_REGEXES):
        return IntentMatch(intent=MEMORY_USAGE, payload={})

    # 12. Autonomy.
    if _matches_any(stripped, _AUTONOMY_REGEXES):
        return IntentMatch(intent=AUTONOMY, payload={})

    # 13. Complex task examples.
    if _matches_any(stripped, _COMPLEX_TASK_REGEXES):
        return IntentMatch(intent=COMPLEX_TASK, payload={})

    # 13.5. Project status / state questions — catch these before they
    #    fall through to an expensive LLM call that times out.
    if _matches_any(stripped, _PROJECT_STATUS_REGEXES):
        return IntentMatch(intent=PROJECT_STATUS, payload={})

    # 14. Skills query.
    if _matches_any(stripped, _SKILLS_REGEXES):
        return IntentMatch(intent=SKILLS, payload={})

    # 15. Capability overview.
    if _matches_any(stripped, _CAPABILITY_REGEXES):
        return IntentMatch(intent=CAPABILITY, payload={})

    # 16. Presence — last because the patterns are the loosest.
    if _matches_any(stripped, _PRESENCE_REGEXES):
        return IntentMatch(intent=PRESENCE, payload={})

    return None


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(text) for p in patterns)


def _normalize_url(target: str) -> str | None:
    """Normalize a possibly-bare domain into a full URL.

    Returns ``None`` if the input clearly is not a URL/domain (e.g.
    just an English word). We only accept inputs that have at least
    one ``.`` and a TLD-looking suffix, or already have a scheme.
    """
    target = target.strip()
    if not target:
        return None
    if target.startswith(("http://", "https://")):
        try:
            parsed = urlparse(target)
        except Exception:
            return None
        if not parsed.netloc:
            return None
        return target

    if "." not in target:
        return None
    # Reject "abc.txt" style — we want a TLD, not a file extension.
    last = target.split("/", 1)[0].rsplit(".", 1)[-1]
    if not last.isalpha() or len(last) < 2:
        return None
    return f"https://{target}"


# ─────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────


async def handle_presence() -> str:
    """Short deterministic presence reply.

    No provider, no LLM, no tool-use. We intentionally do not include
    runtime stats here because the user just wants to confirm we are
    alive — pinging the agent should not consume tokens or run a
    full health check.
    """
    return "I'm here. ✅"


def handle_version() -> str:
    """Return the runtime package version (canonical source of truth)."""
    try:
        from agent import __version__
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("version_lookup_failed", error=str(exc))
        return "Could not determine version (see agent/__init__.py)."
    return f"Running version {__version__}."


def handle_skills() -> str:
    """Read skills.json locally and produce a Telegram-friendly summary.

    No web tools, no LLM. The agent's source of truth lives in the
    repository itself; if the file is missing we say so explicitly
    instead of guessing.
    """
    try:
        from agent.brain.skills import SkillRegistry
        from agent.core.paths import get_project_root

        registry = SkillRegistry(f"{get_project_root()}/agent/brain/skills.json")
        summary = registry.summary()
    except FileNotFoundError:
        return (
            "Skills registry not initialised yet — agent.brain.skills "
            "populates itself after the first successful action."
        )
    except Exception as exc:
        logger.error("skills_lookup_failed", error=str(exc))
        return f"Could not read skills: {exc}"

    total = summary.get("total", 0)
    mastered = summary.get("mastered", []) or []
    known = summary.get("known", []) or []
    unknown = summary.get("unknown", []) or []

    lines = [f"*Skills* ({total} total)"]
    if mastered:
        lines.append(f"  • Mastered ({len(mastered)}): {', '.join(sorted(mastered))}")
    if known:
        lines.append(f"  • Known ({len(known)}): {', '.join(sorted(known))}")
    if unknown:
        lines.append(f"  • Unknown ({len(unknown)}): {', '.join(sorted(unknown))}")
    if not (mastered or known or unknown):
        lines.append("  (no skills stored yet)")
    return "\n".join(lines)


def handle_capability() -> str:
    """High-level capability summary read from a deterministic source.

    We do not call the LLM. The summary lists the agent's broad
    capability areas — operators who want the per-skill breakdown
    can ask for ``skills``.
    """
    return (
        "I'm an autonomous agent (Agent Life Space). Quick capability overview:\n"
        "  • Conversation + memory (per-chat history, persistent SQLite, RAG over the knowledge base)\n"
        "  • Code review (`/review <path>`) — job-centric pipeline with artifacts\n"
        "  • Build pipeline (`/build <task>`) — codegen → Docker sandbox → verification\n"
        "  • Web read (`/web <url>` or natural language: \"open X\")\n"
        "  • Finance ledger (proposals → approvals → cost ledger)\n"
        "  • Tasks queue, watchdog, health, cron loops\n"
        "  • Self-update from public GitHub repo (owner-only, fast-forward, fail-closed)\n"
        "  • LLM runtime control (CLI ↔ API), tiered logging, vault\n\n"
        "For the per-skill breakdown ask: _what skills do you have?_"
    )


def handle_self_update_question() -> str:
    """Answer the *question* — do not run anything."""
    return (
        "Yes — I have an explicit self-update capability:\n"
        "  • Owner-only.\n"
        "  • Requires the project to be a git repo with a configured remote.\n"
        "  • `git fetch` + check for a newer fast-forward commit.\n"
        "  • Worktree must be clean — otherwise fail-closed.\n"
        "  • No destructive git operations, no self-kill.\n"
        "  • After a successful pull a restart through the existing ops "
        "mechanism (systemd / supervisor / watchdog) is required.\n\n"
        "To run it use the imperative: _update yourself_, "
        "_deploy latest_, or _nasad novú verziu u seba_."
    )


async def handle_web_open(url: str, agent: Any) -> str:
    """Run the same code path as ``/web`` but for natural-language input.

    Reuses :class:`agent.core.web.WebAccess`. Errors are normalized to
    a short human sentence — we never echo raw provider or tool JSON
    to the user.
    """
    from agent.core.web import WebAccess

    web = WebAccess()
    try:
        result = await web.scrape_text(url, max_chars=3000)
    except Exception as exc:  # network/DNS/etc → human-friendly line
        logger.error("nl_web_open_failed", url=url, error=str(exc))
        return _friendly_web_error(url, str(exc))
    finally:
        try:
            await web.close()
        except Exception:
            pass

    if "error" in result:
        return _friendly_web_error(url, str(result.get("error", "")))

    status = result.get("status", "?")
    text = result.get("text") or ""

    # Best-effort: store the read in episodic memory if the agent
    # has a memory module. Failures here must not break the reply.
    try:
        from agent.memory.store import MemoryEntry, MemoryType

        await agent.memory.store(MemoryEntry(
            content=f"Read {url}: {text[:200]}",
            memory_type=MemoryType.EPISODIC,
            tags=["web", "scraping", urlparse(url).netloc or "web"],
            source="web",
            importance=0.4,
        ))
    except Exception:
        pass

    if not text.strip():
        return f"{url} (status {status}) — empty content."
    return f"*{url}* (status {status})\n\n{text[:3000]}"


# ─────────────────────────────────────────────
# Grounded introspection handlers
# ─────────────────────────────────────────────


def _safe_runtime_facts(agent: Any) -> dict[str, Any]:
    """Read live runtime facts from the agent. Returns an empty dict
    on failure — handlers must NOT fabricate counts when this is empty.

    The keys are stable so handlers can do simple ``.get(...)`` lookups.
    """
    facts: dict[str, Any] = {}
    if agent is None:
        return facts
    try:
        mem_stats = agent.memory.get_stats()
        if isinstance(mem_stats, dict):
            facts["memories_total"] = mem_stats.get("total_memories")
            facts["memories_by_type"] = mem_stats.get("by_type", {})
    except Exception:
        pass
    try:
        task_stats = agent.tasks.get_stats()
        if isinstance(task_stats, dict):
            facts["tasks_total"] = task_stats.get("total_tasks")
    except Exception:
        pass
    try:
        finance = agent.finance.get_stats()
        if isinstance(finance, dict):
            facts["finance_pending"] = finance.get("pending_proposals")
    except Exception:
        pass
    try:
        from agent.core.paths import get_project_root

        kb = f"{get_project_root()}/agent/brain/knowledge"
        import os as _os

        if _os.path.isdir(kb):
            files = [
                _os.path.join(root, f)
                for root, _, fs in _os.walk(kb)
                for f in fs if f.endswith(".md")
            ]
            facts["knowledge_files"] = len(files)
    except Exception:
        pass
    return facts


def handle_memory_usage(agent: Any) -> str:
    """Grounded answer to "do you actually use those memories?".

    Distinguishes between:
      * memory store (SQLite-backed, episodic + semantic + procedural)
      * persistent conversation history (per-chat SQLite)
      * RAG over the knowledge base (markdown files in
        ``agent/brain/knowledge``)
      * skills registry (``agent/brain/skills.json``)

    Numbers are only included if they come from the live runtime
    stats. We never invent counts. We never claim "memory equals
    markdown files".
    """
    facts = _safe_runtime_facts(agent)
    lines = [
        "Yes — memory is used in three distinct subsystems, and they are not the same thing:",
        "",
        "*1. Memory store* (SQLite, `agent/memory/store.py`)",
        "   • Episodic / semantic / procedural entries with provenance + decay.",
        "   • Consulted on every message via keyword + provenance filter; "
        "only OBSERVED / USER_ASSERTED / VERIFIED entries are injected into the prompt.",
    ]
    if facts.get("memories_total") is not None:
        lines.append(f"   • Currently stored: {facts['memories_total']} entries.")
    lines.extend([
        "",
        "*2. Persistent per-chat conversation* (SQLite, `agent/memory/persistent_conversation.py`)",
        "   • Stores prior turns of *this* chat so a follow-up like \"yes\" still has context after a restart.",
        "   • In-RAM tail keeps the last ~10 turns; older context is fetched from the DB.",
        "",
        "*3. RAG over knowledge base* (`agent/brain/knowledge/*.md`)",
        "   • Markdown files curated by the operator, embedded with sentence-transformers.",
        "   • A *direct* hit (>0.85 sim) returns the KB answer with no LLM call; an *augment* hit (>0.65) "
        "is injected into the prompt.",
    ])
    if facts.get("knowledge_files") is not None:
        lines.append(f"   • Currently indexed: {facts['knowledge_files']} markdown files.")
    lines.extend([
        "",
        "Skills are *not* memories — they live in `agent/brain/skills.json` and track success/failure of capabilities, not facts.",
    ])
    return "\n".join(lines)


async def handle_memory_list(agent: Any, *, limit: int = 10) -> str:
    """Read the memory store and list the most recent / important
    entries.

    Grounded: numbers come from the live store, not from a hardcoded
    string. Falls back gracefully if the store is unavailable.
    """
    if agent is None:
        return "Memory store is not wired up in this brain instance."
    try:
        store = agent.memory
        stats = store.get_stats() if hasattr(store, "get_stats") else {}
        total = stats.get("total_memories") if isinstance(stats, dict) else None
    except Exception as exc:
        logger.error("memory_list_stats_failed", error=str(exc))
        stats = {}
        total = None

    entries: list[Any] = []
    try:
        # Prefer the public query method if it exists with no kwargs.
        if hasattr(store, "query"):
            entries = await store.query(limit=limit)
    except Exception as exc:
        logger.warning("memory_list_query_failed", error=str(exc))
        entries = []

    lines = ["*Recent memory entries*"]
    if total is not None:
        lines[0] = f"*Recent memory entries* ({total} total)"
    if isinstance(stats, dict):
        by_type = stats.get("by_type") or {}
        if by_type:
            type_summary = ", ".join(f"{k}: {v}" for k, v in by_type.items())
            lines.append(f"  by type: {type_summary}")
    if not entries:
        lines.append("")
        lines.append(
            "No entries returned by the live query. Memory may be "
            "empty, or the store doesn't expose a no-arg query — try "
            "`/memory <keyword>` for keyword search."
        )
        return "\n".join(lines)

    lines.append("")
    for i, entry in enumerate(entries[:limit], start=1):
        try:
            content = getattr(entry, "content", None) or str(entry)
            kind = getattr(entry, "memory_type", "")
            kind_str = kind.value if hasattr(kind, "value") else str(kind)
            line = f"  {i}. [{kind_str}] {str(content)[:140]}"
        except Exception:
            line = f"  {i}. {str(entry)[:140]}"
        lines.append(line)
    return "\n".join(lines)


def handle_context_recall(
    chat_conv: list[dict[str, str]],
) -> str:
    """Read the in-RAM chat tail and explain what we have been
    talking about.

    Pure: looks only at the per-chat conversation buffer the brain
    already maintains, no LLM call. Reports the last few exchanges
    factually so the operator can verify the agent has the context.
    """
    if not chat_conv:
        return (
            "I don't have any earlier turns recorded for this chat in "
            "the in-RAM tail. Either this is a fresh chat after a "
            "process restart (the persistent SQLite store still has "
            "the older history — it just wasn't hydrated yet), or no "
            "prior exchange happened."
        )

    # Pull the last ~6 entries (3 user/assistant pairs).
    tail = list(chat_conv[-6:])
    lines = [
        f"Here are the last {len(tail)} entries I see in this chat's "
        "in-RAM tail (no LLM, this is the literal buffer):",
        "",
    ]
    for entry in tail:
        role = entry.get("role", "?")
        content = str(entry.get("content", ""))[:200]
        sender = entry.get("sender", "")
        if role == "user":
            label = f"you ({sender})" if sender else "you"
        else:
            label = "me"
        lines.append(f"  • {label}: {content}")
    lines.append("")
    lines.append(
        "If this looks unrelated to your question, the conversation "
        "may have rolled out of the in-RAM tail — the persistent "
        "SQLite store has more, but only the most recent N turns are "
        "kept hot."
    )
    return "\n".join(lines)


def handle_memory_horizon(agent: Any) -> str:
    """Truthful answer to "how many turns back do you remember?".

    Reports the actual configured tail size of the in-RAM buffer
    plus a note about persistent conversation context. We do **not**
    cite invented paths like ``.claude/projects`` — that storage
    belongs to the user's local Claude Code installation, not to
    this agent.
    """
    in_ram = 10  # AgentBrain._max_conversation default
    try:
        # If the brain instance is reachable through the agent, prefer
        # the live value over the constant.
        brain_obj = getattr(agent, "_brain", None)
        if brain_obj is not None:
            in_ram = int(getattr(brain_obj, "_max_conversation", in_ram))
    except Exception:
        pass

    return (
        "Memory horizon (truthful, not invented):\n"
        f"  • In-RAM per-chat tail: last {in_ram} turns are kept verbatim "
        "for the immediate prompt.\n"
        "  • Persistent conversation DB: older turns are restored from "
        "SQLite (`agent/memory/persistent_conversation.py`) when the "
        "chat resumes after a restart.\n"
        "  • Memory store: arbitrary facts/episodes are recalled by "
        "keyword + provenance filter, not by raw turn count.\n"
        "  • The single LLM call itself is bounded by the model's "
        "context window — not by an agent-side cap."
    )


def handle_autonomy(agent: Any) -> str:
    """Mode-aware autonomy answer.

    Reads the relevant runtime flags and explains what is allowed
    *right now*, with explicit guardrails. We never claim absolute
    capabilities like "I can move money" or "I can deploy anything".
    """
    import os as _os

    sandbox_only = _os.environ.get("AGENT_SANDBOX_ONLY", "1") != "0"
    backend = _os.environ.get("LLM_BACKEND", "cli").strip() or "cli"
    try:
        from agent.control.llm_runtime import resolve_llm_runtime_state

        runtime = resolve_llm_runtime_state(environ=_os.environ)
        backend = str(runtime.get("effective_backend", backend))
    except Exception:
        pass

    lines = [
        "Autonomy is conditional, not absolute. Here is what I can do *right now* and the conditions:",
        "",
        "*Reliable, no approval needed:*",
        "  • Read memory / knowledge base / skills / runtime status.",
        "  • Run deterministic local commands (`/status`, `/health`, `/skills`, `/budget`, …).",
        "  • Read web pages (`/web` or natural-language \"open X\").",
        "  • Code review (`/review <path>`) — produces an artifact, not a merge.",
        "",
        "*Allowed only with explicit approval / mode:*",
        "  • Build pipeline (`/build <task>`) runs codegen + Docker sandbox; "
        "the result is a delivery artifact, never an auto-merge.",
        "  • Finance proposals require operator approval before completion.",
        "  • Programming tasks via Telegram + CLI backend are *blocked* in "
        "sandbox-only mode (interactive permission prompt is unreachable from Telegram).",
        "",
        "*Never, by design:*",
        "  • Send money. Wallets are read-only inside the agent — no `send` method exists.",
        "  • Auto-merge to main. Builds produce delivery packages; merging is human-only.",
        "  • Rewrite host files outside the project root, install packages with sudo, "
        "or bypass the budget caps.",
        "",
        "*Current effective runtime:*",
        f"  • LLM backend: `{backend}`",
        f"  • Sandbox-only: `{sandbox_only}` (host file access {'OFF' if sandbox_only else 'ON — operator opt-in'})",
    ]
    return "\n".join(lines)


async def handle_project_status(agent: Any) -> str:
    """Grounded project-status answer from ``agent.get_status()``.

    Each section is individually guarded so a failure in one doesn't
    suppress the others.
    """
    import agent as _agent_pkg

    parts: list[str] = []
    parts.append(f"*Project status — Agent Life Space v{_agent_pkg.__version__}*\n")

    # Pull the canonical status dict (single call, many sub-dicts).
    try:
        status = agent.get_status()
    except Exception:
        status = {}

    # Runtime
    try:
        parts.append(f"Runtime: {'running' if status.get('running') else 'idle'}")
    except Exception:
        parts.append("Runtime: unknown")

    # Memory
    try:
        mem = status.get("memory", {})
        parts.append(f"Memory: {mem.get('total', 0)} entries")
    except Exception:
        pass

    # Tasks
    try:
        tasks = status.get("tasks", {})
        parts.append(
            f"Tasks: {tasks.get('pending', 0)} pending, "
            f"{tasks.get('completed', 0)} completed"
        )
    except Exception:
        pass

    # Skills (from brain stats, not separate registry)
    try:
        brain = status.get("brain", {})
        skills_count = brain.get("skills_total") or brain.get("skills_count", 0)
        if skills_count:
            parts.append(f"Skills: {skills_count}")
    except Exception:
        pass

    # Build pipeline
    try:
        build = status.get("build", {})
        total_builds = build.get("total_jobs", build.get("total", 0))
        if total_builds:
            completed = build.get("completed", 0)
            failed = build.get("failed", 0)
            parts.append(f"Builds: {total_builds} total ({completed} completed, {failed} failed)")
        else:
            parts.append("Builds: none")
    except Exception:
        pass

    # Review
    try:
        review = status.get("review", {})
        total_reviews = review.get("total_jobs", review.get("total", 0))
        if total_reviews:
            parts.append(f"Reviews: {total_reviews} total")
    except Exception:
        pass

    parts.append(
        "\nFor detailed reports use `/status`, `/health`, `/jobs`, or `/skills`."
    )
    return "\n".join(parts)


def handle_complex_task(agent: Any) -> str:
    """Grounded examples of complex tasks the agent can actually run."""
    return (
        "Practical complex tasks I can take on (grounded in implemented capabilities):\n\n"
        "*Code review*\n"
        "  • `/review agent/core/router.py` — focused file audit\n"
        "  • `/review .` — full repo audit (job-centric, artifact-first)\n\n"
        "*Build pipeline (codegen → Docker sandbox → verify)*\n"
        "  • `/build write a small CLI that converts CSV to JSON` — produces a delivery artifact\n"
        "  • `/build add a /metrics endpoint to the agent API` — sandboxed, never auto-merged\n\n"
        "*Operator-style work*\n"
        "  • Deep memory / knowledge introspection (`/memory <keyword>`, `/consolidate`)\n"
        "  • Budget posture + cost ledger reports (`/report budget`, `/report cost`)\n"
        "  • Settlement workflow for 402/top-up situations (`/settlement`)\n\n"
        "*Things I will refuse / require operator action for*\n"
        "  • Anything that would send money or auto-merge to main.\n"
        "  • Programming tasks via Telegram + CLI backend in sandbox-only mode "
        "(switch the LLM runtime to API or run with `AGENT_SANDBOX_ONLY=0` first).\n"
        "  • Compromising sources (PII export, deleting logs).\n\n"
        "If you have a specific task in mind, paste it and I'll tell you "
        "exactly which path it would run through and where the approval gates are."
    )


def handle_limits() -> str:
    """Honest, non-marketing list of things this agent does NOT do."""
    return (
        "Honest list of things I do *not* do:\n\n"
        "*Hard limits (by design)*\n"
        "  • No autonomous money sending. Wallets are read-only.\n"
        "  • No auto-merge to `main`. Builds produce delivery packages, humans merge.\n"
        "  • No DeFi / trading / smart contracts.\n"
        "  • No SaaS hosting, telemetry, or call-home.\n"
        "  • No managed multi-tenant identity (single operator per instance today).\n\n"
        "*Soft limits (mode-dependent)*\n"
        "  • Programming tasks via Telegram + CLI backend in sandbox-only mode are blocked "
        "(no interactive approval channel from Telegram).\n"
        "  • Build pipeline jobs require Docker on the host.\n"
        "  • RAG depends on the local sentence-transformers model (~1.5 GB RAM).\n\n"
        "*Things I cannot reliably do*\n"
        "  • Compare myself to external products I have no verified information about.\n"
        "  • Promise behavior of any third-party API or schema I cannot read locally.\n"
        "  • Replace the operator. I'm a power tool, not a substitute for human judgment."
    )


def handle_self_description(agent: Any) -> str:
    """Balanced self-description — strengths AND weaknesses, no marketing."""
    return (
        "Honest, balanced self-description (this project, not a generic agent):\n\n"
        "*Where I have a real edge in this project*\n"
        "  • Channel-agnostic brain pipeline with a deterministic safety net for common intents "
        "(no model call for presence/version/skills/capability/web/comparison/limits/etc.).\n"
        "  • Vault v2 single-file format (atomic, crash-safe), tiered logging with deterministic retention, "
        "runtime LLM control (`cli ↔ api` flip without restart).\n"
        "  • Build + review pipelines that always produce artifacts, never silent merges.\n"
        "  • Anti-confabulation discipline: provenance-tagged memory, only OBSERVED / USER_ASSERTED / VERIFIED "
        "facts get injected into the LLM prompt.\n"
        "  • Self-update is an explicit fast-forward owner-only capability, not a freeform shell trick.\n\n"
        "*Where I am NOT better than other agents*\n"
        "  • Single operator per instance — no multi-tenant identity.\n"
        "  • Telegram is the production channel; Discord/email are stubs.\n"
        "  • RAG is keyword + embedding, not a full document search engine.\n"
        "  • The CLI backend (Claude Code) adds ~26k tokens of overhead per call — for cost-sensitive "
        "workloads the API backend is the better choice.\n"
        "  • Model failure tracking is per-process and resets on restart.\n\n"
        "*What I will not do, on purpose*\n"
        "  • Send money, auto-merge, run trading strategies, or replace the operator.\n\n"
        "If you want the per-skill breakdown ask: _what skills do you have?_"
    )


def handle_weather_report_setup(city: str, agent: Any) -> str:
    """Honest, grounded answer for "set me up a daily weather report".

    The operator's intent is "build me a small program that runs on
    your own host and pings me every morning". The agent does NOT
    pre-bake a weather scheduler — that would be a hidden hardcoded
    feature. Instead it surfaces:

      * the existing build pipeline (codegen → Docker sandbox → artifact)
      * the current limitation: there is no self-install / self-run
        capability that would auto-promote a built artifact onto the
        host process and register it with the cron loop
      * a deterministic next step the operator can take

    This keeps the agent honest. If/when a self-install pipeline lands
    (Phase 8 / earning roadmap), this handler is the obvious surface
    to upgrade.
    """
    has_city = bool(city.strip())
    target = city.strip() or "(city not specified yet)"
    lines = [
        "I read this as: \"build a small program that runs on my own host and pings me every morning with weather for *X*\".",
        "",
        f"  • City: {target}",
        "  • Schedule: daily, ~07:00 local time",
        "",
        "*Honest status of what I can actually do here:*",
        "",
        "  1. *Codegen via build pipeline.* I can run `/build write a small "
        "Python program that fetches weather for {city} and posts it to a "
        "Telegram chat at 07:00` — that runs codegen → Docker sandbox → "
        "verification, and produces a delivery artifact. No interactive "
        "approval, no LLM tool-use chaos.",
        "",
        "  2. *No self-install / self-run yet.* I do **not** have a "
        "self-deployment capability that would take a built artifact, "
        "install it onto my own host process and register it with my "
        "cron loop. That would be a real Phase 8 feature; pre-baking a "
        "weather scheduler in core would be a hidden hardcoded feature, "
        "which I won't do without an explicit operator decision.",
        "",
        "  3. *What the operator can do today:*",
        "     • Run `/build` with the prompt above to get the artifact.",
        "     • Review the generated code, install it (e.g. systemd timer "
        "or a small recurring job in `agent/control/recurring.py`), and "
        "I'll then surface its runs through the cron loop.",
        "",
        "  4. *If you want me to do this end-to-end automatically*, the "
        "right move is to open an issue scoped as: \"Phase 8: self-install "
        "+ self-run pipeline for agent-built artifacts\". I'll route the "
        "weather job through that pipeline once it exists.",
    ]
    if not has_city:
        lines.append("")
        lines.append(
            "If you want me to start the codegen step right now, "
            "tell me which city — e.g. _Bratislava_, _Prague_, _Košice_."
        )
    return "\n".join(lines)


def handle_comparison(subject: str, agent: Any) -> str:
    """Fail-safe comparison handler.

    For any external subject I do not have a verified internal source
    of truth about, I refuse to invent comparison facts. I describe
    my own verified properties and ask for a link if the user wants
    a real comparison.
    """
    cleaned = (subject or "").strip().lower()
    # Strip trailing punctuation / language particles.
    cleaned = re.sub(r"[\?\.,!;:]+$", "", cleaned).strip()
    cleaned = re.sub(
        r"\s+(yourself|systems?|agents?|products?|tools?|frameworks?)$",
        "",
        cleaned,
    ).strip()
    if cleaned in {"other", "others", "other agents", "iní agenti", "ini agenti"}:
        return (
            "Honest answer: \"better than other agents\" is not something I can claim "
            "without context. Here is what I am confident about in this project, "
            "and where I'm not.\n\n"
            + handle_self_description(agent)
        )

    name = subject.strip() or "that system"
    return (
        f"I do not have a verified internal source of truth about *{name}*, so I "
        f"will not invent comparison facts. What I can do honestly:\n\n"
        f"  • Describe my own verified capabilities and limits (see below).\n"
        f"  • Read a public page about *{name}* if you give me the URL — try "
        f"`open {name.lower().replace(' ', '')}` (or any URL) and I'll fetch it "
        f"with the deterministic web handler, no LLM hallucination.\n\n"
        + handle_self_description(agent)
    )


def _friendly_web_error(url: str, raw: str) -> str:
    """Map a noisy web error string to a short human sentence."""
    msg = (raw or "").lower()
    if not msg:
        return f"Could not load {url}."
    if "rate limit" in msg:
        return f"Web access rate limit — try {url} in a moment."
    if "timeout" in msg or "timed out" in msg:
        return f"{url} did not respond in time (timeout)."
    if "name or service not known" in msg or "nodename nor servname" in msg:
        return f"Could not resolve {url} (DNS lookup failed)."
    if "connection refused" in msg or "econnrefused" in msg:
        return f"Connection to {url} was refused."
    if "ssl" in msg or "certificate" in msg:
        return f"{url} has an SSL/certificate problem."
    if "404" in msg:
        return f"{url} does not exist (404)."
    if any(code in msg for code in ("500", "502", "503", "504")):
        return f"Target server {url} is unhealthy right now (5xx)."
    # Fallback — keep it short, never dump raw blobs.
    return f"Could not load {url}: {raw[:120]}"


__all__ = [
    "AUTONOMY",
    "CAPABILITY",
    "COMPARISON",
    "COMPLEX_TASK",
    "CONTEXT_RECALL",
    "LIMITS",
    "MEMORY_HORIZON",
    "MEMORY_LIST",
    "MEMORY_USAGE",
    "PRESENCE",
    "PROJECT_STATUS",
    "SELF_DESCRIPTION",
    "SELF_UPDATE_IMPERATIVE",
    "SELF_UPDATE_QUESTION",
    "SKILLS",
    "VERSION",
    "WEATHER_REPORT_CITY_REPLY",
    "WEATHER_REPORT_SETUP",
    "WEB_OPEN",
    "IntentMatch",
    "detect_intent",
    "handle_autonomy",
    "handle_capability",
    "handle_comparison",
    "handle_complex_task",
    "handle_context_recall",
    "handle_limits",
    "handle_memory_horizon",
    "handle_memory_list",
    "handle_memory_usage",
    "handle_presence",
    "handle_project_status",
    "handle_self_description",
    "handle_self_update_question",
    "handle_skills",
    "handle_version",
    "handle_weather_report_setup",
    "handle_web_open",
]
