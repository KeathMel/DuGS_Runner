"""
semantic_search.py — the matching engine behind Semantic Tables.

WHY THERE IS NO EMBEDDING MODEL HERE
====================================
Every other part of DuGS runs on the Python standard library alone. That is
the whole reason the runner works on a phone under Termux, on a Pi, in a
tiny Alpine container -- nothing to pip install, nothing to download, no
model weights. Pulling in sentence-transformers (~90MB of PyTorch plus a
model) to match some short strings would break that outright.

So this scores text the old-fashioned way, and it is honest about the
trade: it will not understand that "it's pouring" means rain unless you
write that variation down. What it IS good at is exactly the job described
-- you give it a long list of the ways you actually say a thing, and it
reliably picks the right one despite typos, word order, extra filler words,
and partial phrasing.

Three signals get blended, because each covers the others' blind spots:

  token overlap   -- "what is the temperature" vs "temperature what" -> high.
                     Word order does not matter. This carries most of the work.
  character 3-grams -- survives typos and word endings: "temprature" still
                     scores well against "temperature", which plain word
                     matching would miss completely.
  sequence ratio  -- difflib's whole-string similarity, as a tiebreaker for
                     short inputs where the other two are coarse.

If you later want true embeddings, `score()` is the single function to
swap; nothing else in the file cares how the number is produced.
"""
import re
import math
from difflib import SequenceMatcher

# Words so common they say nothing about intent. Dropping them stops
# "what is the weather" and "what is the time" from looking similar just
# because they share three filler words.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "can", "could", "do", "does",
    "for", "from", "get", "give", "how", "i", "if", "in", "is", "it", "its",
    "just", "me", "my", "of", "on", "or", "please", "pls", "so", "that",
    "the", "then", "there", "this", "to", "up", "want", "was", "what",
    "whats", "when", "where", "which", "who", "will", "with", "would",
    "you", "your", "u", "ur",
}

_WORD_RE = re.compile(r"[a-z0-9']+")


def normalise(text):
    """Lowercase, strip punctuation, collapse whitespace. Everything else
    in this module works on the output of this, so a table written with
    capitals and question marks still matches typed-in lowercase text."""
    return " ".join(_WORD_RE.findall(str(text or "").lower()))


def _tokens(text, drop_stopwords=True):
    words = _WORD_RE.findall(str(text or "").lower())
    if drop_stopwords:
        meaningful = [w for w in words if w not in _STOPWORDS]
        # if the phrase was ENTIRELY stopwords ("what is it"), keep them --
        # better to match on filler than to have nothing at all to compare
        if meaningful:
            return meaningful
    return words


def _char_ngrams(text, n=3):
    t = normalise(text).replace(" ", "")
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _fuzzy_token_overlap(q_tok, v_tok):
    """Token overlap that tolerates typos.

    Plain set intersection treats "temprature" and "temperature" as two
    completely different words, so one slipped letter wipes out the entire
    word-level signal and the match collapses. Here each query token gets
    credited with its best near-match among the variation's tokens, so a
    typo costs a little confidence instead of all of it.
    """
    if not q_tok or not v_tok:
        return 0.0
    total = 0.0
    for q in q_tok:
        best = 0.0
        for v in v_tok:
            if q == v:
                best = 1.0
                break
            # only bother with words of similar length -- "in" vs
            # "instrument" should not count as a partial hit
            if abs(len(q) - len(v)) <= 3:
                r = SequenceMatcher(None, q, v).ratio()
                if r > best:
                    best = r
        # below this a pair is just two different words, not a typo
        total += best if best >= 0.75 else 0.0
    # normalise by the larger side so extra unmatched words still dilute
    return total / max(len(q_tok), len(v_tok))


def score(query, variation):
    """How well one phrase matches one variation, 0.0 to 1.0.

    This is the single swap point if you ever move to real embeddings.
    """
    q_norm, v_norm = normalise(query), normalise(variation)
    if not q_norm or not v_norm:
        return 0.0
    if q_norm == v_norm:
        return 1.0

    q_tok, v_tok = set(_tokens(query)), set(_tokens(variation))
    token_sim = _jaccard(q_tok, v_tok)
    # typo-tolerant overlap rescues the cases exact matching drops entirely
    token_sim = max(token_sim, _fuzzy_token_overlap(q_tok, v_tok))

    # a short variation fully contained in a longer query is a strong signal:
    # "set a timer for ten minutes" should match the variation "set a timer"
    if v_tok and v_tok.issubset(q_tok):
        token_sim = max(token_sim, 0.9)
    elif q_tok and q_tok.issubset(v_tok):
        token_sim = max(token_sim, 0.85)

    char_sim = _jaccard(_char_ngrams(query), _char_ngrams(variation))
    seq_sim = SequenceMatcher(None, q_norm, v_norm).ratio()

    # token overlap carries the most weight because it is the most
    # meaning-bearing; the other two mainly rescue typos and short inputs
    return (token_sim * 0.55) + (char_sim * 0.30) + (seq_sim * 0.15)


def search(query, table, threshold=0.0, top_k=1):
    """Match `query` against a semantic table.

    table is the dict shape storage.load_semantic_table returns:
        {"name": ..., "intents": [
            {"result": "weather_check", "variations": ["weather today", ...]},
            ...
        ]}

    Returns a list of match dicts, best first:
        [{"result": ..., "confidence": 0.93, "matched": "the variation that won",
          "intent_index": 2}, ...]

    An intent scores as its single best-matching variation -- NOT the
    average. Averaging would punish you for writing lots of variations,
    which is the exact opposite of what this thing is for.
    """
    results = []
    for idx, intent in enumerate(table.get("intents") or []):
        best_score, best_variation = 0.0, None
        for variation in (intent.get("variations") or []):
            s = score(query, variation)
            if s > best_score:
                best_score, best_variation = s, variation
        if best_variation is None:
            continue
        if best_score >= threshold:
            results.append({
                "result": intent.get("result"),
                "confidence": round(best_score, 4),
                "matched": best_variation,
                "intent_index": idx,
            })
    results.sort(key=lambda r: r["confidence"], reverse=True)
    return results[:max(1, int(top_k))]


def best_match(query, table, threshold=0.45):
    """The single best match, or None if nothing clears the threshold.

    0.45 is deliberately middling: high enough that unrelated text does not
    match, low enough that a typo or an extra filler word still lands. Tune
    it per node -- raise it when a wrong answer is expensive, lower it when
    missing a match is worse than a fuzzy one.
    """
    hits = search(query, table, threshold=threshold, top_k=1)
    return hits[0] if hits else None
