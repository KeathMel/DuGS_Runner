"""
Semantic Router — match incoming text against a Semantic Table and output
the matching result. The if/else replacement.

Instead of a switch node with forty branches checking for keywords, you
write down every way you'd actually phrase a thing in a Semantic Table
(Data tab on the home screen), and this node picks the right one -- coping
with typos, word order, and extra filler words along the way.

Runs entirely locally. No API call, no model download, no network, no cost
per match -- so it's also just plain faster than asking an AI to classify.

OUTPUT MODES
============
  single  : one output port. Every item carries the match on it, so use an
            IF/Switch downstream to branch on {{ $json.result }}.
  branch  : one output port PER intent, in table order, plus a final
            "no match" port. The node does the branching itself, which is
            the point of the whole exercise.

CONFIDENCE
==========
Every match reports how sure it is, 0 to 1. Below the threshold nothing
matches and the item goes out unmatched (or down the last port in branch
mode) -- that's the hook for "hand this one to the AI instead", which is
the whole cost-saving idea: local match handles the easy 95%, the LLM only
ever sees what genuinely needs it.

SETTINGS
========
table       : which Semantic Table to match against
text        : the text to match; {{ }} allowed
threshold   : 0-1, how sure a match must be to count
mode        : single | branch
field       : what field name the result lands under
"""
import os
import sys

from node_base import Node

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage
import semantic_search


class SemanticRouterNode(Node):
    TYPE = "data.semantic"
    TITLE = "Semantic Router"
    CATEGORY = "data"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "table", "label": "Semantic Table", "type": "semantic",
         "default": "",
         "desc": "Which table of intents to match against."},
        {"key": "text", "label": "Text to match", "type": "text",
         "default": "{{ $json.body.message }}",
         "desc": "The incoming text. Expressions allowed.",
         "example": "{{ $json.body.message }}"},
        {"key": "threshold", "label": "Confidence threshold", "type": "number",
         "default": 0.45,
         "desc": "0 to 1. How sure a match must be to count. Raise it when a "
                 "wrong answer is expensive, lower it when missing a match is "
                 "worse than a fuzzy one. 0.45 is a sensible middle."},
        {"key": "mode", "label": "Output", "type": "select",
         "default": "single", "options": ["single", "branch"],
         "desc": "single = one port, branch on the result yourself. "
                 "branch = one port per intent plus a final 'no match' port."},
        {"key": "field", "label": "Output field", "type": "text",
         "default": "result",
         "desc": "The field name the matched result lands under."},
    ]

    def outputs_count(self):
        """In branch mode the port count depends on the chosen table -- one
        per intent, plus one for 'nothing matched'. The editor calls this to
        know how many output dots to draw."""
        if (self.params.get("mode") or "single") != "branch":
            return 1
        table_name = self.params.get("table")
        if not table_name:
            return 1
        try:
            table = storage.load_semantic_table(table_name)
            return len(table.get("intents") or []) + 1
        except Exception:
            return 1

    def run(self, items):
        table_name = self.p("table")
        if not table_name:
            return items or [{"json": {}}]

        try:
            table = storage.load_semantic_table(table_name)
        except Exception as e:
            return [{"json": {**(it.get("json", {})),
                             "error": f"could not load semantic table: {e}"}}
                   for it in (items or [{"json": {}}])]

        intents = table.get("intents") or []
        try:
            threshold = float(self.p("threshold", 0.45))
        except (TypeError, ValueError):
            threshold = 0.45
        mode = self.p("mode", "single")
        field = self.p("field", "result") or "result"

        if mode == "branch":
            # one bucket per intent, plus a final bucket for no-match
            ports = [[] for _ in range(len(intents) + 1)]
        out = []

        for it in (items or [{"json": {}}]):
            j = it.get("json", {})
            text = self.rexpr(self.p("text", ""), j)
            match = semantic_search.best_match(str(text or ""), table,
                                              threshold=threshold)

            new = dict(j)
            if match:
                new[field] = match["result"]
                new["confidence"] = match["confidence"]
                new["matched_variation"] = match["matched"]
                new["matched"] = True
            else:
                new[field] = None
                new["confidence"] = 0.0
                new["matched"] = False

            item = {"json": new}
            if mode == "branch":
                # unmatched items go down the LAST port -- that's the one you
                # wire to an AI node for the cases local matching can't handle
                port = match["intent_index"] if match else len(intents)
                ports[port].append(item)
            else:
                out.append(item)

        return ports if mode == "branch" else out
