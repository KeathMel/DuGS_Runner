"""
Keyword Match — true/false on whether certain words show up in some text.

A lighter cousin of the Semantic Router: no table to build, no scoring, no
confidence number. You list the words you care about right here in the node,
and it answers yes or no. Word order never matters.

HOW THE LIST WORKS
==================
One entry per line. Every word ON a line has to be present, in any order:

    weather today        <- needs both "weather" AND "today"
    rain                 <- needs just "rain"
    forecast please      <- needs both

  "what's today's weather"  -> matches line 1  -> TRUE
  "is there rain"           -> matches line 2  -> TRUE
  "weather"                 -> nothing complete -> FALSE

With "Match" set to "any line" (the default), one full line is enough. Set
it to "all lines" and every line has to be satisfied.

WHOLE WORDS
===========
On by default, so "cat" does not fire on "category" and "rain" does not fire
on "train". Turn it off to match anywhere inside words too.

OUTPUTS
=======
  port 0 : true   -- the keywords were found
  port 1 : false  -- they were not

Each item also gets `keywords_matched` (true/false) and `matched_line` (the
line that fired, or null), so a later node can see why it went the way it did.

SETTINGS
========
field          : the text to check; {{ }} allowed
keywords       : one entry per line, words on a line must all appear
match          : any line | all lines
whole_words    : only match complete words
case_sensitive : treat upper and lower case as different
on_missing     : false | true | error -- when the field isn't on the item
"""
import re

from node_base import Node

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


class KeywordMatchNode(Node):
    TYPE = "logic.keywords"
    TITLE = "Keyword Match"
    CATEGORY = "logic"
    INPUTS = 1
    OUTPUTS = 2
    PARAMS = [
        {"key": "field", "label": "Text to check", "type": "text",
         "default": "{{ $json.body.message }}",
         "desc": "The text to look through. Expressions allowed.",
         "example": "{{ $json.body.message }}"},
        {"key": "keywords", "label": "Keywords (one entry per line)",
         "type": "multiline", "default": "",
         "desc": "One entry per line. Every word on a line must appear, in "
                 "any order. e.g. 'weather today' needs both words.",
         "example": "weather today\nrain\nforecast please"},
        {"key": "match", "label": "Match", "type": "select",
         "default": "any line", "options": ["any line", "all lines"],
         "desc": "any line = one complete line is enough. "
                 "all lines = every line has to be satisfied."},
        {"key": "whole_words", "label": "Whole words only", "type": "bool",
         "default": True,
         "desc": "On: 'rain' won't fire on 'train'. Off: matches anywhere, "
                 "even inside other words."},
        {"key": "case_sensitive", "label": "Case sensitive", "type": "bool",
         "default": False,
         "desc": "Off (default): 'Weather' and 'weather' are the same."},
        {"key": "on_missing", "label": "If the field is missing",
         "type": "select", "default": "false",
         "options": ["false", "true", "error"],
         "desc": "What to do when the text isn't on the item at all. "
                 "false = false branch (default). true = true branch. "
                 "error = stop and report it."},
    ]

    # ---- helpers -----------------------------------------------------------
    def _lines(self, case_sensitive):
        """The keyword list, parsed once per run into [[word, word], ...].
        Blank lines are ignored so stray empty lines in the box can't
        accidentally become an always-true entry."""
        raw = str(self.params.get("keywords") or "")
        lines = []
        for ln in raw.splitlines():
            words = ln.split()
            if not words:
                continue
            if not case_sensitive:
                words = [w.lower() for w in words]
            lines.append(words)
        return lines

    def _line_hits(self, words, text, text_words, whole_words):
        """Are all the words on one line present in the text?"""
        if whole_words:
            return all(w in text_words for w in words)
        return all(w in text for w in words)

    # ---- run ---------------------------------------------------------------
    def run(self, items):
        case_sensitive = bool(self.params.get("case_sensitive", False))
        whole_words = bool(self.params.get("whole_words", True))
        need_all = (self.params.get("match") or "any line") == "all lines"
        on_missing = str(self.params.get("on_missing", "false")).lower()
        field_expr = self.params.get("field", "")
        lines = self._lines(case_sensitive)

        true_items, false_items = [], []

        for item in (items or [{"json": {}}]):
            j = item.get("json", {})

            # same missing-field handling as the IF node: an expression that
            # resolves to nothing, or a plain field name not on the item
            if "{{" in str(field_expr):
                text = self.rexpr(field_expr, j)
                missing = text is None
            else:
                name = str(field_expr).strip()
                text = j.get(name)
                missing = name not in j

            if missing:
                if on_missing == "error":
                    shown = str(field_expr).strip() or "(blank)"
                    raise ValueError(
                        f"Keyword Match: the field {shown} is missing on this "
                        f"item, and this node is set to error when that happens.")
                passed, hit = (on_missing == "true"), None
            else:
                # a non-string value (a number, a dict) still gets checked as
                # text rather than crashing the node
                text = str(text)
                if not case_sensitive:
                    text = text.lower()
                text_words = set(_WORD_RE.findall(text))

                if not lines:
                    # no keywords written at all -- nothing can match, so
                    # everything is false rather than trivially true
                    passed, hit = False, None
                else:
                    results = [(ln, self._line_hits(ln, text, text_words, whole_words))
                               for ln in lines]
                    if need_all:
                        passed = all(ok for _ln, ok in results)
                        hit = " / ".join(" ".join(ln) for ln, _ in results) if passed else None
                    else:
                        winner = next((ln for ln, ok in results if ok), None)
                        passed = winner is not None
                        hit = " ".join(winner) if winner else None

            new = dict(j)
            new["keywords_matched"] = passed
            new["matched_line"] = hit
            (true_items if passed else false_items).append({"json": new})

        return [true_items, false_items]
