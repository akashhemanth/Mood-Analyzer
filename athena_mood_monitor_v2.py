# athena_mood_monitor_v1.py
# ---------------------------------------------
# v2: Rule-based analysis of Whisper transcripts
# Outputs:
#   Desktop\Athena_Logs_Analysis\daily\*.json
#   Desktop\Athena_Logs_Analysis\weekly\*.json
#
# Notes:
# - This is intentionally explainable + tunable.
# - It does NOT diagnose or auto-escalate; it produces recommendations/flags.
# - Self-harm language triggers a "safety_followup_needed" flag with cooldowns
#   to avoid repeated nagging for chronic passive SI.
#
# Windows-friendly paths + no shell-specific tricks.

import sys
import os
import re
import json
import glob
import datetime as dt
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional

# -------------------------
# CONFIG
# -------------------------
BASE_DIR = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", "Athena_Logs")
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", "Athena_Logs_Analysis")

PATIENT_ID = "self"

# Optional: if you later add daily binary PHQ/mania checkins, put them here as YYYY-MM-DD.json
DAILY_CHECKIN_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "Athena_Checkins")

# Cooldowns (avoid repeated alerts)
PASSIVE_SI_COOLDOWN_HOURS = 24
CLINICIAN_NOTIFY_COOLDOWN_HOURS = 72

# Deterioration thresholds
RECOMMEND_APPT_SCORE = 60
SUSTAINED_SCORE = 45
SUSTAINED_DAYS = 3

# Repetition bonus settings
REPEAT_BONUS_2 = 1      # add after 2 total mentions
REPEAT_BONUS_4 = 1      # add after 4 total mentions
REPEAT_CAP = 2          # max extra count added to valence hits

DET_REPEAT_BONUS_2 = 5  # for deterioration categories
DET_REPEAT_BONUS_4 = 5

# Valence thresholds (labeling only)
VAL_NEG = -0.25
VAL_POS = 0.25

# Transcript quality thresholds
QUALITY_MIN_WORDS = 20
QUALITY_WEIRD_TOKEN_RATIO_POOR = 0.35
QUALITY_WEIRD_TOKEN_RATIO_OK = 0.20

# Optional fuzzy matching
try:
    from rapidfuzz import fuzz  # type: ignore
    HAVE_RAPIDFUZZ = True
except Exception:
    HAVE_RAPIDFUZZ = False


# -------------------------
# Lexicons (v1)
# Tune these over time.
# -------------------------

NEG_PHRASES = [
    "what's the point", "whats the point", "no point", "hopeless",
    "can't do this", "cant do this", "overwhelmed", "worthless", "a burden",
    "hate myself", "i suck", "nothing matters", "empty", "numb",
    "i give up", "can't cope", "cant cope", "panic", "anxious", "depressed",
    "no motivation", "don't care", "dont care", "lost interest",
]

POS_PHRASES = [
    "grateful", "thankful", "happy", "excited", "proud",
    "made progress", "productive", "felt better", "relieved",
    "calm", "content", "motivated", "good day", "went well",
]

IMPAIRMENT_PHRASES = [
    # --- Functional impairment ---
    "couldn't get out of bed", "couldnt get out of bed",
    "couldn't get up", "couldnt get up",
    "skipped work", "missed work", "missed class", "skipped class",
    "called in sick", "couldn't go in", "couldnt go in",
    "didn't shower", "didnt shower", "haven't showered", "havent showered",
    "didn't eat", "didnt eat", "haven't eaten", "havent eaten", "forgot to eat",
    "couldn't do anything", "couldnt do anything",
    "didn't do anything", "didnt do anything",
    "barely functioned", "can't function", "cant function",

    # --- Energy / Fatigue ---
    "no energy", "zero energy", "drained", "exhausted", "wiped out",
    "worn out", "burnt out", "burned out", "running on empty",
    "so tired", "really tired", "beyond tired", "dead tired",
    "can't keep my eyes open", "cant keep my eyes open",
    "too tired to", "too exhausted to",
    "fatigued", "fatigue",
    "felt heavy", "body feels heavy",
    "couldn't move", "couldnt move", "didn't want to move", "didnt want to move",
    "glued to the couch", "glued to the bed", "stuck in bed",

    # --- Motivation ---
    "no motivation", "zero motivation", "can't motivate", "cant motivate",
    "no drive", "no will", "can't make myself", "cant make myself",
    "don't want to do anything", "dont want to do anything",
    "can't be bothered", "cant be bothered",
    "can't start anything", "cant start anything",
    "procrastinating everything", "putting everything off",
    "no point in trying", "what's the point in trying", "whats the point in trying",

    # --- Interest / Anhedonia ---
    "lost interest", "no interest", "don't care anymore", "dont care anymore",
    "nothing sounds good", "nothing appeals",
    "can't enjoy", "cant enjoy", "don't enjoy", "dont enjoy",
    "stopped enjoying", "don't find it fun", "dont find it fun",
    "nothing excites me", "nothing feels good",
    "feel flat", "feeling flat", "everything feels flat",
    "can't feel pleasure", "cant feel pleasure",

    # --- Guilt ---
    "feel guilty", "feeling guilty", "so much guilt",
    "i should have", "i shouldn't have", "i shouldnt have",
    "i failed", "i let everyone down", "let people down", "let myself down",
    "i'm a bad", "im a bad", "i'm terrible", "im terrible",
    "i'm so selfish", "im so selfish",
    "hate that i", "ashamed", "feel ashamed", "feeling ashamed",
    "embarrassed by myself", "disgusted with myself",

    # --- Focus / Concentration ---
    "can't focus", "cant focus", "couldn't focus", "couldnt focus",
    "can't concentrate", "cant concentrate", "couldn't concentrate", "couldnt concentrate",
    "brain fog", "foggy brain", "mind is foggy", "can't think straight",
    "cant think straight", "losing my train of thought",
    "zoning out", "zoned out", "spacing out", "spaced out",
    "can't retain", "cant retain", "can't remember", "cant remember",
    "forgetting everything", "can't think", "cant think",
    "mind is blank", "mind went blank",

    # --- Appetite ---
    "no appetite", "lost my appetite", "not hungry", "can't eat", "cant eat",
    "eating too much", "overeating", "stress eating", "binge eating", "binged",
    "ate nothing", "barely ate", "skipped meals", "skipped breakfast",
    "skipped lunch", "skipped dinner", "didn't have dinner", "didnt have dinner",
    "couldn't stop eating", "couldnt stop eating",
]

SLEEP_PHRASES = [
    "didn't sleep", "didnt sleep", "no sleep", "slept all day",
    "couldn't fall asleep", "couldnt fall asleep", "woke up all night",
]

SUBSTANCE_PHRASES = [
    "alcohol", "beer", "wine", "liquor",
    "weed", "marijuana", "thc",
    "smoked", "nicotine", "vaping",
    "took extra", "used again", "relapsed",
    "pills", "opioids", "benzos",
]

PROTECTIVE_PHRASES = [
    "called my therapist", "therapy", "therapist",
    "reached out", "talked to someone", "talked to",
    "asked for help", "got help",
    "support", "support system",
    "coping skill", "coping skills", "used my coping",
    "meditated", "meditation", "breathwork", "deep breathing",
    "i am safe", "i'm safe", "im safe",
    "i will be safe", "i'll be safe",
    "won't act", "wont act",
    "safety plan", "used my safety plan",
    "crisis line", "hotline",
]

# Patient-specific delusion recovery profile.
#
# Edit this block when the patient's delusion theme changes. The analyzer does
# not decide whether a belief is true or false. It only scores cognitive process:
# reality testing, uncertainty tolerance, alternative explanations, insight,
# non-engagement, and concern markers when they occur near this patient's known
# delusion-context language.
DELUSION_RECOVERY_PROFILE = {
    "patient_id": PATIENT_ID,
    "delusion_theme_name": "TV show / referential delusion",
    "window_radius_sentences": 2,
    "delusion_context_terms": [
    "the show",
    "cameras",
    "talking about me",
    "references",
    "connected to me",
    "meant for me",
    "they said",
    "episode",
    "being watched",
    "i'm being watched",
    "im being watched",
    "i feel like i'm being watched",
    "i feel like im being watched",
],
    "recovery_markers": {
        "reality_testing": [
            "i don't actually know",
            "i dont actually know",
            "i can't prove it",
            "i cant prove it",
            "i do not know for sure",
            "i don't know for sure",
            "i dont know for sure",
            "i checked the facts",
            "i reality checked",
            "reality check",
        ],
        "uncertainty_tolerance": [
            "maybe",
            "might not",
            "there is a chance",
            "there's a chance",
            "i am not sure",
            "i'm not sure",
            "im not sure",
            "i can sit with not knowing",
            "i can tolerate not knowing",
        ],
        "alternative_explanation": [
            "they might not have meant it like that",
            "there is another possibility",
            "there's another possibility",
            "it could be unrelated",
            "it might be unrelated",
            "could just be a coincidence",
            "might just be a coincidence",
            "they just said it",
            "it is unrelated to me",
            "it's unrelated to me",
            "its unrelated to me",
        ],
        "non_engagement": [
            "i moved on",
            "i move on",
            "i went to the next thing",
            "i go to the next thing",
            "i let it go",
            "i did not spiral",
            "i didn't spiral",
            "i didnt spiral",
            "i redirected",
            "i changed activities",
        ],
        "insight": [
            "the disease",
            "the illness",
            "the delusion",
            "my brain is making connections",
            "my brain makes connections",
            "i become convinced",
            "i get convinced",
            "this is a symptom",
            "part of my illness",
        ],
    },
    "concern_markers": {
        "high_conviction": [
            "i know they are talking about me",
            "i know it is about me",
            "i know it's about me",
            "i know its about me",
            "it has to be about me",
            "there is no other explanation",
            "there's no other explanation",
            "i am sure they meant me",
            "i'm sure they meant me",
            "im sure they meant me",
            "they definitely meant me",
        ],
        "referential_distress": [
            "it scared me",
            "it terrified me",
            "i panicked",
            "i felt targeted",
            "it felt targeted",
            "they were mocking me",
            "they were threatening me",
            "i felt watched",
            "i felt exposed",
        ],
        "spiraling_or_preoccupation": [
            "i spiraled",
            "i kept thinking about it",
            "i couldn't stop thinking about it",
            "i couldnt stop thinking about it",
            "i obsessed over it",
            "i replayed it",
            "i spent hours",
            "all day thinking about it",
        ],
        "checking_or_reassurance": [
            "i checked again",
            "i kept checking",
            "i rewatched it",
            "i searched for proof",
            "i looked for proof",
            "i asked if they meant me",
            "i needed reassurance",
        ],
        "behavior_change_from_belief": [
            "i avoided the show",
            "i stopped watching",
            "i changed my plans because of it",
            "i cancelled because of it",
            "i hid from the cameras",
            "i covered the camera",
        ],
    },
}

# Bounded deterioration contribution for delusion-theme concern markers.
# Protective/recovery markers are reported separately and do not subtract from
# deterioration here; this keeps deterioration and recovery readable side by side.
DELUSION_CONCERN_DETERIORATION_POINTS = {
    "low": 5,
    "moderate": 10,
    "high": 20,
}

# Self-harm lexicon: keep tight and explicit.
SELF_HARM_TERMS = [
    "suicide", "suicidal", "kill myself", "killing myself", "end my life", "hurt myself",
    "harm myself", "self harm", "self-harm", "overdose", "cut myself",
    "better off dead", "wish i was dead", "want to die"
]

DENIAL_TERMS = [
        "not going to", "wouldn't", "wouldnt", "no plan", "no intent",
    "i won't", "i wont", "i would not", "i'm safe", "im safe",
    "i don't want to", "i do not want to",
    "i don't actually", "i dont actually",
    "i'm not going to", "im not going to",
    "not like i want to", "not like i'm going to", "not like im going to",
    "i would never", "i'd never",
    # Feel like variants
    "don't feel like", "dont feel like",
    "do not feel like",
    "i don't feel like", "i dont feel like",
    "i do not feel like",
    "didn't feel like", "didnt feel like",
    "did not feel like",
]

FIGURATIVE_EXCLUSIONS = [
    "beat myself up",
    "beating myself up",
    "beat myself up about",
    "kill it",          # "we killed it", "gonna kill it"
    "killing it",
    "killed it",
    "dying of laughter",
    "dying laughing",
    "i'm dying",        # casual hyperbole
    "im dying",
    "could have died",  # embarrassment
    "almost died laughing",
    "cut it out",       # "just cut it out" meaning stop
    "cut it short",
    "cut myself some slack",
    "hurt my feelings",  # emotional, not physical
    "overdressed",       # common "over" word that isn't overdose
    "overdue",
    "overslept",
    "overlooked",
    "overcome",
    "overwhelmed myself",  # this one IS negative but not self-harm
]


# -------------------------
# Utilities
# -------------------------

def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().strip()

def write_json(path: str, obj: dict) -> None:
    ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[\u2019\u2018]", "'", text)
    text = re.sub(r"[^a-z0-9'\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def parse_date_from_path(path: str) -> Optional[dt.date]:
    """
    Infer date from:
      - filename prefix: YYYY-MM-DD or YYYY-MM-DD_HH-MM-SS
      - folder: MM-DD-YYYY (your log structure)
    """
    base = os.path.basename(path)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", base)
    if m:
        try:
            return dt.datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except Exception:
            pass

    parts = path.split(os.sep)
    for part in reversed(parts):
        if re.match(r"\d{2}-\d{2}-\d{4}", part):
            try:
                return dt.datetime.strptime(part, "%m-%d-%Y").date()
            except Exception:
                continue

    return None

def week_key(d: dt.date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# -------------------------
# Transcript quality
# -------------------------

def transcript_quality(text: str) -> Dict[str, object]:
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    wc = len(words)
    if wc == 0:
        return {"quality": "poor", "word_count": 0, "weird_ratio": 1.0}

    weird = 0
    for w in words:
        if not re.match(r"^[A-Za-z']+$", w):
            weird += 1

    weird_ratio = weird / max(wc, 1)

    if wc < QUALITY_MIN_WORDS or weird_ratio >= QUALITY_WEIRD_TOKEN_RATIO_POOR:
        q = "poor"
    elif weird_ratio >= QUALITY_WEIRD_TOKEN_RATIO_OK:
        q = "ok"
    else:
        q = "good"

    return {"quality": q, "word_count": wc, "weird_ratio": round(weird_ratio, 3)}


# -------------------------
# Matching helpers
# -------------------------

def contains_phrase(norm_text: str, phrase: str) -> bool:
    return phrase in norm_text

def fuzzy_hit(norm_text: str, phrase: str, threshold: int = 90) -> bool:
    """
    Conservative fuzzy match using rapidfuzz if available.
    """
    if not HAVE_RAPIDFUZZ:
        return False

    phrase = normalize(phrase)
    if not phrase:
        return False

    if phrase in norm_text:
        return True

    tokens = norm_text.split()
    ptoks = phrase.split()
    win = max(len(ptoks), 1)

    for i in range(0, max(len(tokens) - win + 1, 1)):
        chunk = " ".join(tokens[i:i + win])
        score = fuzz.token_set_ratio(chunk, phrase)
        if score >= threshold:
            return True
    return False

def multi_hit(norm_text: str, terms: List[str], min_hits: int = 2) -> Tuple[bool, List[str]]:
    hits = []
    for t in terms:
        t_norm = normalize(t)
        if contains_phrase(norm_text, t_norm):
            hits.append(t)
        elif HAVE_RAPIDFUZZ and fuzzy_hit(norm_text, t, threshold=90):
            hits.append(t)
    return (len(hits) >= min_hits, hits)

def contains_phrase_boundary(norm_text: str, phrase: str) -> bool:
    """
    Match phrase as whole words only.
    Uses \\b word boundaries so 'od' won't hit inside 'today' or 'model'.
    Works for both single-word and multi-word phrases.
    """
    pattern = r'\b' + re.escape(phrase) + r'\b'
    return bool(re.search(pattern, norm_text))

def count_phrase_occurrences(norm_text: str, phrases: List[str]) -> Tuple[List[str], int]:
    """
    Returns:
      - list of distinct phrases that appeared at least once
      - total number of occurrences across all phrases
    """
    hits = []
    total_count = 0

    for p in phrases:
        p_norm = normalize(p)
        count = norm_text.count(p_norm)
        if count > 0:
            hits.append(p)
            total_count += count

    return hits, total_count
# -------------------------
# Scoring
# -------------------------

def compute_valence(norm_text: str) -> Dict[str, object]:
    neg_hits, neg_total = count_phrase_occurrences(norm_text, NEG_PHRASES)
    pos_hits, pos_total = count_phrase_occurrences(norm_text, POS_PHRASES)

    neg = len(neg_hits)
    pos = len(pos_hits)

    # Small capped repetition bonus
    neg_bonus = 0
    pos_bonus = 0

    if neg_total >= 2:
        neg_bonus += REPEAT_BONUS_2
    if neg_total >= 4:
        neg_bonus += REPEAT_BONUS_4

    if pos_total >= 2:
        pos_bonus += REPEAT_BONUS_2
    if pos_total >= 4:
        pos_bonus += REPEAT_BONUS_4

    neg_bonus = min(neg_bonus, REPEAT_CAP)
    pos_bonus = min(pos_bonus, REPEAT_CAP)

    neg_effective = neg + neg_bonus
    pos_effective = pos + pos_bonus

    score = (pos_effective - neg_effective) / (pos_effective + neg_effective + 3.0)
    score = max(-1.0, min(1.0, score))

    return {
        "valence": round(score, 3),
        "pos_hits": pos_hits[:10],
        "neg_hits": neg_hits[:10],
        "pos_count": pos,
        "neg_count": neg,
        "pos_total_mentions": pos_total,
        "neg_total_mentions": neg_total,
    }
def compute_deterioration(norm_text: str) -> Dict[str, object]:
    score = 0
    drivers = []
    protective = []

    imp_hits, imp_total = count_phrase_occurrences(norm_text, IMPAIRMENT_PHRASES)
    if imp_hits:
        score += 25
        if imp_total >= 2:
            score += DET_REPEAT_BONUS_2
        if imp_total >= 4:
            score += DET_REPEAT_BONUS_4
        drivers.append("impairment_markers")

    hop_candidates = ["hopeless", "what's the point", "whats the point", "no point", "i give up"]
    hop_hits, hop_total = count_phrase_occurrences(norm_text, hop_candidates)
    if hop_hits:
        score += 20
        if hop_total >= 2:
            score += DET_REPEAT_BONUS_2
        if hop_total >= 4:
            score += DET_REPEAT_BONUS_4
        drivers.append("hopelessness_language")

    sleep_hits, sleep_total = count_phrase_occurrences(norm_text, SLEEP_PHRASES)
    if sleep_hits:
        score += 10
        if sleep_total >= 2:
            score += DET_REPEAT_BONUS_2
        if sleep_total >= 4:
            score += DET_REPEAT_BONUS_4
        drivers.append("sleep_disruption")

    subs_hits, subs_total = count_phrase_occurrences(norm_text, SUBSTANCE_PHRASES)
    if subs_hits:
        score += 15
        if subs_total >= 2:
            score += DET_REPEAT_BONUS_2
        if subs_total >= 4:
            score += DET_REPEAT_BONUS_4
        drivers.append("substance_escalation")

    neg_hits, neg_total = count_phrase_occurrences(norm_text, NEG_PHRASES)
    if len(neg_hits) >= 3:
        score += 10
        if neg_total >= 5:
            score += DET_REPEAT_BONUS_2
        if neg_total >= 8:
            score += DET_REPEAT_BONUS_4
        drivers.append("high_negative_density")

    prot_hits, prot_total = count_phrase_occurrences(norm_text, PROTECTIVE_PHRASES)
    if prot_hits:
        score -= 15
        protective.extend(prot_hits)

    score = max(0, min(100, score))
    return {
        "deterioration_score": int(score),
        "drivers": drivers,
        "protective_factors": protective[:10],
    }




def split_raw_sentences(text: str) -> List[str]:
    """
    Split the original transcript before normalization so punctuation still marks
    sentence boundaries. Returned sentences are normalized for phrase matching.
    """
    raw_sentences = re.split(r'[.!?\n]+', text)
    return [normalize(s) for s in raw_sentences if normalize(s)]

def window_phrase_hits(sentences: List[str], index: int, radius: int, phrases: List[str]) -> Tuple[List[str], str]:
    """
    Return phrase hits inside a sentence window around index.
    Generic terms like "maybe" only count when this same window also contains
    the patient's delusion-context language.
    """
    start = max(0, index - radius)
    end = min(len(sentences), index + radius + 1)
    window_text = " ".join(sentences[start:end])
    hits, _ = count_phrase_occurrences(window_text, phrases)
    return hits, window_text

def compute_delusion_recovery(text: str, profile: dict = DELUSION_RECOVERY_PROFILE) -> Dict[str, object]:
    """
    Detect recovery/protective markers only when they co-occur near this
    patient's delusion context. This prevents ordinary uncertainty language from
    being scored as positive outside the relevant theme.
    """
    sentences = split_raw_sentences(text)
    radius = int(profile.get("window_radius_sentences", 2))
    context_terms = profile.get("delusion_context_terms", [])
    marker_groups = profile.get("recovery_markers", {})

    protective_factors = []
    context_windows = []
    seen = set()

    for i, _sentence in enumerate(sentences):
        context_hits, window_text = window_phrase_hits(sentences, i, radius, context_terms)
        if not context_hits:
            continue

        matched_markers = {}
        for category, phrases in marker_groups.items():
            marker_hits, _ = count_phrase_occurrences(window_text, phrases)
            if marker_hits:
                matched_markers[category] = marker_hits[:10]

        if not matched_markers:
            continue

        context_windows.append({
            "sentence_index": i,
            "context_hits": context_hits[:10],
            "marker_categories": sorted(matched_markers.keys()),
        })

        for category, marker_hits in matched_markers.items():
            key = (category, tuple(marker_hits), tuple(context_hits))
            if key in seen:
                continue
            seen.add(key)
            protective_factors.append({
                "factor": category,
                "delusion_context_hits": context_hits[:10],
                "recovery_marker_hits": marker_hits[:10],
            })

    factor_names = sorted({p["factor"] for p in protective_factors})

    return {
        "profile": {
            "patient_id": profile.get("patient_id"),
            "delusion_theme_name": profile.get("delusion_theme_name"),
            "window_radius_sentences": radius,
        },
        "detected": bool(protective_factors),
        "protective_factor_names": factor_names,
        "protective_factors": protective_factors,
        "context_windows": context_windows[:20],
        "note": "Scores coping/recovery language only near patient-specific delusion context; does not judge whether the belief is true.",
    }

def compute_delusion_concern(text: str, profile: dict = DELUSION_RECOVERY_PROFILE) -> Dict[str, object]:
    """
    Detect possible buy-in, spiraling, distress, checking, or behavior change
    around the patient's delusion theme. This is a concern signal, not a truth
    judgment and not a diagnosis.
    """
    sentences = split_raw_sentences(text)
    radius = int(profile.get("window_radius_sentences", 2))
    context_terms = profile.get("delusion_context_terms", [])
    marker_groups = profile.get("concern_markers", {})

    concern_factors = []
    context_windows = []
    seen = set()
    total_marker_hits = 0

    for i, _sentence in enumerate(sentences):
        context_hits, window_text = window_phrase_hits(sentences, i, radius, context_terms)
        if not context_hits:
            continue

        matched_markers = {}
        for category, phrases in marker_groups.items():
            marker_hits, marker_total = count_phrase_occurrences(window_text, phrases)
            if marker_hits:
                matched_markers[category] = marker_hits[:10]
                total_marker_hits += marker_total

        if not matched_markers:
            continue

        context_windows.append({
            "sentence_index": i,
            "context_hits": context_hits[:10],
            "marker_categories": sorted(matched_markers.keys()),
        })

        for category, marker_hits in matched_markers.items():
            key = (category, tuple(marker_hits), tuple(context_hits))
            if key in seen:
                continue
            seen.add(key)
            concern_factors.append({
                "factor": category,
                "delusion_context_hits": context_hits[:10],
                "concern_marker_hits": marker_hits[:10],
            })

    factor_names = sorted({c["factor"] for c in concern_factors})
    score = 0
    if concern_factors:
        score += 25
    if len(factor_names) >= 2:
        score += 20
    if len(factor_names) >= 3:
        score += 15
    if total_marker_hits >= 3:
        score += 10
    score = max(0, min(100, score))

    if score >= 60:
        level = "high"
    elif score >= 35:
        level = "moderate"
    elif score > 0:
        level = "low"
    else:
        level = "none"

    return {
        "profile": {
            "patient_id": profile.get("patient_id"),
            "delusion_theme_name": profile.get("delusion_theme_name"),
            "window_radius_sentences": radius,
        },
        "detected": bool(concern_factors),
        "concern_score": int(score),
        "level": level,
        "concern_factor_names": factor_names,
        "concern_factors": concern_factors,
        "context_windows": context_windows[:20],
        "note": "Flags concern language only near patient-specific delusion context; does not judge whether the belief is true.",
    }

def apply_delusion_concern_to_deterioration(det: Dict[str, object], delusion_concern: Dict[str, object]) -> Dict[str, object]:
    """
    Keep deterioration separate, but add a small bounded contribution when the
    delusion-theme concern signal is present.
    """
    if not delusion_concern.get("detected"):
        return det

    level = str(delusion_concern.get("level", "none"))
    points = DELUSION_CONCERN_DETERIORATION_POINTS.get(level, 0)
    if points <= 0:
        return det

    updated = dict(det)
    updated["deterioration_score"] = int(max(0, min(100, int(det.get("deterioration_score", 0)) + points)))
    drivers = list(updated.get("drivers", []))
    if "delusion_theme_concern" not in drivers:
        drivers.append("delusion_theme_concern")
    updated["drivers"] = drivers
    updated["delusion_concern_points_added"] = points
    return updated


# -------------------------
# Safety detection + cooldowns
# -------------------------

@dataclass
class SafetyEvent:
    detected: bool
    hits: List[str]
    followup_needed: bool
    risk_level: str  # "none" | "unresolved" | "passive"
    action: str      # "none" | "prompt_followup"


def load_profile() -> dict:
    path = os.path.join(OUTPUT_DIR, "profiles", f"{PATIENT_ID}.json")
    if os.path.exists(path):
        try:
            return json.loads(read_text(path))
        except Exception:
            return {}
    return {}

def save_profile(profile: dict) -> None:
    path = os.path.join(OUTPUT_DIR, "profiles", f"{PATIENT_ID}.json")
    write_json(path, profile)

def within_cooldown(profile: dict, key: str, hours: int) -> bool:
    ts = profile.get("cooldowns", {}).get(key)
    if not ts:
        return False
    try:
        last = dt.datetime.fromisoformat(ts)
    except Exception:
        return False
    return (dt.datetime.now() - last) < dt.timedelta(hours=hours)

def set_cooldown(profile: dict, key: str) -> None:
    profile.setdefault("cooldowns", {})[key] = dt.datetime.now().isoformat(timespec="seconds")

def split_sentences(norm_text: str):
    """
    Split normalized text into sentences on . ! ? and newlines.
    Returns a list of sentence strings.
    """
    sentences = re.split(r'[.!?\n]+', norm_text)
    sentences = [s.strip() for s in sentences if s.strip()]
    return sentences

def detect_self_harm(norm_text: str, profile: dict) -> "SafetyEvent":
    sentences = split_sentences(norm_text)
 
    # Check figurative exclusions across whole transcript first
    active_exclusions = [e for e in FIGURATIVE_EXCLUSIONS if e in norm_text]
 
    hits = []
    risk_level = "none"
 
    for sentence in sentences:
        sentence_hits = []
 
        for term in SELF_HARM_TERMS:
            t_norm = normalize(term)
 
            # Word boundary match
            if not contains_phrase_boundary(sentence, t_norm):
                if HAVE_RAPIDFUZZ and fuzzy_hit(sentence, term, threshold=92):
                    pass
                else:
                    continue
 
            # Figurative exclusion check
            term_words = set(t_norm.split())
            cancelled = any(
                term_words & set(e.split()) for e in active_exclusions
            )
            if cancelled:
                continue
 
            sentence_hits.append(term)
 
        if not sentence_hits:
            continue
 
        # Check denial ONLY within this same sentence
        denial_in_sentence = any(
            normalize(d) in sentence for d in DENIAL_TERMS
        )
 
        if denial_in_sentence:
            # Denial cancels hits in this sentence â€” mark as passive
            # but don't add to hits list so it doesn't count toward threshold
            hits.append(("passive", sentence_hits))
        else:
            # No denial â€” these are unresolved hits
            hits.append(("unresolved", sentence_hits))
 
    if not hits:
        return SafetyEvent(False, [], False, "none", "none")
 
    # Flatten all hit terms for reporting
    all_hit_terms = []
    for risk, terms in hits:
        all_hit_terms.extend(terms)
    all_hit_terms = list(set(all_hit_terms))
 
    # Determine overall risk level:
    # If ANY sentence has unresolved hits -> unresolved
    # If ALL sentences with hits have denial -> passive
    unresolved_sentences = [h for h in hits if h[0] == "unresolved"]
    passive_sentences = [h for h in hits if h[0] == "passive"]
 
    if unresolved_sentences:
        overall_risk = "unresolved"
    elif passive_sentences:
        overall_risk = "passive"
    else:
        overall_risk = "none"
 
    # Only count toward threshold if unresolved
    unresolved_terms = []
    for _, terms in unresolved_sentences:
        unresolved_terms.extend(terms)
 
    # Explicit multiword phrases always count regardless
    explicit = []
    for t in SELF_HARM_TERMS:
        t_norm = normalize(t)
        if (" " in t_norm or "-" in t_norm):
            if contains_phrase_boundary(norm_text, t_norm):
                term_words = set(t_norm.split())
                cancelled = any(
                    term_words & set(e.split()) for e in active_exclusions
                )
                if not cancelled:
                    # Check if this explicit phrase has denial in its sentence
                    for sentence in sentences:
                        if contains_phrase_boundary(sentence, t_norm):
                            if not any(normalize(d) in sentence for d in DENIAL_TERMS):
                                explicit.append(t)
 
    explicit = list(set(explicit))
 
    detected = len(unresolved_terms) >= 2 or bool(explicit)
 
    if not detected and overall_risk == "passive":
        # Passive hits were found but all had denial â€” still flag softly
        detected = True
 
    if not detected:
        return SafetyEvent(False, [], False, "none", "none")
 
    # Cooldown check
    if within_cooldown(profile, "passive_si_followup", PASSIVE_SI_COOLDOWN_HOURS) and not explicit:
        return SafetyEvent(True, all_hit_terms, False, overall_risk, "none")
 
    set_cooldown(profile, "passive_si_followup")
    followup_needed = overall_risk == "unresolved" or bool(explicit)
 
    return SafetyEvent(
        detected=True,
        hits=all_hit_terms,
        followup_needed=followup_needed,
        risk_level=overall_risk,
        action="prompt_followup" if followup_needed else "none"
    )


# -------------------------
# Weekly PHQ-9 reconstruction (optional scaffold)
# -------------------------

PHQ9_ITEMS = [
    "anhedonia", "depressed_mood", "sleep", "fatigue", "appetite",
    "worthlessness", "concentration", "psychomotor", "self_harm_thoughts"
]

def tally_days_to_phq_score(days_present: int) -> int:
    if days_present <= 0:
        return 0
    if days_present <= 2:
        return 1
    if days_present <= 4:
        return 2
    return 3

def load_daily_checkins_for_week(week: str) -> List[dict]:
    if not os.path.isdir(DAILY_CHECKIN_DIR):
        return []
    out = []
    for fp in glob.glob(os.path.join(DAILY_CHECKIN_DIR, "*.json")):
        try:
            d = json.loads(read_text(fp))
            date_s = d.get("date_local")
            if not date_s:
                continue
            date_obj = dt.date.fromisoformat(date_s)
            if week_key(date_obj) == week:
                out.append(d)
        except Exception:
            continue
    return out

def compute_weekly_phq9(week: str) -> Optional[dict]:
    days = load_daily_checkins_for_week(week)
    if not days:
        return None

    counts = {k: 0 for k in PHQ9_ITEMS}
    for d in days:
        phq = d.get("phq9_daily_binary", {})
        for k in PHQ9_ITEMS:
            if int(phq.get(k, 0)) == 1:
                counts[k] += 1

    item_scores = {k: tally_days_to_phq_score(v) for k, v in counts.items()}
    total = sum(item_scores.values())

    return {
        "week": week,
        "days_counted": len(days),
        "days_present": counts,
        "phq9_item_scores": item_scores,
        "phq9_total": total,
    }


# -------------------------
# Decisions + labeling (v2)
# -------------------------

def label_day(valence: float, det_score: int) -> str:
    if det_score >= RECOMMEND_APPT_SCORE:
        return "Negative"
    if valence <= VAL_NEG:
        return "Negative"
    if valence >= VAL_POS and det_score < 40:
        return "Positive"
    return "Neutral"

def recommend_action(det_score: int, recent_scores: List[int], safety: SafetyEvent) -> Dict[str, object]:
    # Safety dominates (v1 only prompts followup)
    if safety.detected and safety.followup_needed:
        return {"level": "safety_followup_needed", "reason": "Self-harm language detected (v1 prompt only)"}

    sustained = False
    if len(recent_scores) >= SUSTAINED_DAYS:
        tail = recent_scores[-(SUSTAINED_DAYS - 1):] + [det_score]
        sustained = all(s >= SUSTAINED_SCORE for s in tail)

    if det_score >= RECOMMEND_APPT_SCORE:
        return {"level": "recommend_appointment_soon", "reason": f"deterioration_score >= {RECOMMEND_APPT_SCORE}"}
    if sustained:
        return {"level": "recommend_appointment_soon", "reason": f"sustained >= {SUSTAINED_SCORE} for {SUSTAINED_DAYS} days"}

    return {"level": "none", "reason": "No rule threshold met"}


# -------------------------
# Main scanning
# -------------------------

def find_transcripts(base_dir: str) -> List[str]:
    return sorted(glob.glob(os.path.join(base_dir, "**", "*.txt"), recursive=True))

def main():
    ensure_dir(OUTPUT_DIR)
    ensure_dir(os.path.join(OUTPUT_DIR, "daily"))
    ensure_dir(os.path.join(OUTPUT_DIR, "weekly"))
    ensure_dir(os.path.join(OUTPUT_DIR, "profiles"))

    profile = load_profile()
    profile.setdefault("cooldowns", {})

    if len(sys.argv) > 1:
        transcripts = [sys.argv[1]]
    else:
        transcripts = find_transcripts(BASE_DIR)

    if not transcripts:
        print("No transcripts found.")
        return

    # Sort by inferred date then mtime
    def sort_key(fp: str):
        d = parse_date_from_path(fp)
        if d:
            return (d.toordinal(), fp)
        return (int(os.path.getmtime(fp)), fp)

    transcripts.sort(key=sort_key)

    recent_by_date: Dict[str, int] = {}
    weekly_rollup: Dict[str, dict] = {}

    for txt_path in transcripts:
        text = read_text(txt_path)
        norm = normalize(text)

        d = parse_date_from_path(txt_path)
        if not d:
            d = dt.date.fromtimestamp(os.path.getmtime(txt_path))

        day_iso = d.isoformat()
        wk = week_key(d)

        q = transcript_quality(text)
        val = compute_valence(norm)
        det_base = compute_deterioration(norm)
        delusion_recovery = compute_delusion_recovery(text)
        delusion_concern = compute_delusion_concern(text)
        det = apply_delusion_concern_to_deterioration(det_base, delusion_concern)
        safety = detect_self_harm(norm, profile)

        recent_by_date[day_iso] = det["deterioration_score"]
        sorted_days = sorted(recent_by_date.keys())
        recent_scores = [recent_by_date[k] for k in sorted_days]

        action = recommend_action(int(det["deterioration_score"]), recent_scores, safety)
        day_label = label_day(float(val["valence"]), int(det["deterioration_score"]))

        out = {
            "patient_id": PATIENT_ID,
            "date_local": day_iso,
            "week": wk,
            "source_transcript_path": txt_path,
            "transcript_quality": q,
            "valence": {
                "score": val["valence"],
                "pos_count": val["pos_count"],
                "neg_count": val["neg_count"],
                "pos_hits": val["pos_hits"],
                "neg_hits": val["neg_hits"],
            },
            "deterioration": det,
            "delusion_recovery": delusion_recovery,
            "delusion_concern": delusion_concern,
            "day_label": day_label,
            "safety": asdict(safety) if safety.detected else None,
            "action": action,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        }

        base_name = os.path.splitext(os.path.basename(txt_path))[0]
        out_path = os.path.join(OUTPUT_DIR, "daily", f"{base_name}.json")
        write_json(out_path, out)

        wk_obj = weekly_rollup.setdefault(wk, {
            "week": wk,
            "patient_id": PATIENT_ID,
            "days": [],
            "counts": {"positive": 0, "neutral": 0, "negative": 0, "safety_days": 0},
            "avg_valence": [],
            "avg_deterioration": [],
        })

        wk_obj["days"].append({
            "date_local": day_iso,
            "day_label": day_label,
            "valence": val["valence"],
            "deterioration_score": det["deterioration_score"],
            "delusion_recovery_detected": delusion_recovery["detected"],
            "delusion_recovery_factors": delusion_recovery["protective_factor_names"],
            "delusion_concern_level": delusion_concern["level"],
            "delusion_concern_factors": delusion_concern["concern_factor_names"],
            "action_level": action["level"],
            "safety_detected": safety.detected,
        })

        wk_obj["counts"][day_label.lower()] += 1
        if safety.detected:
            wk_obj["counts"]["safety_days"] += 1

        wk_obj["avg_valence"].append(float(val["valence"]))
        wk_obj["avg_deterioration"].append(float(det["deterioration_score"]))

    # Write weekly summaries
    for wk, wk_obj in weekly_rollup.items():
        av = wk_obj["avg_valence"]
        ad = wk_obj["avg_deterioration"]
        wk_obj["avg_valence"] = round(sum(av) / len(av), 3) if av else None
        wk_obj["avg_deterioration_score"] = round(sum(ad) / len(ad), 2) if ad else None
        wk_obj.pop("avg_deterioration", None)

        wk_obj["weekly_phq9"] = compute_weekly_phq9(wk)

        out_path = os.path.join(OUTPUT_DIR, "weekly", f"{wk}.json")
        write_json(out_path, wk_obj)

    save_profile(profile)

    print(f"Done. Daily:  {os.path.join(OUTPUT_DIR, 'daily')}")
    print(f"Weekly: {os.path.join(OUTPUT_DIR, 'weekly')}")
    if not HAVE_RAPIDFUZZ:
        print("Tip: install rapidfuzz for fuzzy matching: python -m pip install rapidfuzz")

if __name__ == "__main__":
    main()

