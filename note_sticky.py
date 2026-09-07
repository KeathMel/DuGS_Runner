"""
Note — a sticky note you drop on the canvas to label and group things.

This is NOT a node in the flow. It has no ports, connects to nothing, and
never runs; the engine drops anything of this type before it works out the
graph. It exists as a node file only so it turns up in the palette like
everything else and can be dragged onto the canvas the same way.

Everything about it lives on the canvas: position, size, colour, and the
markdown body. It is saved inside the workflow's own nodes list, so a note
travels with the workflow through Save, Download and Deploy without needing
a second file or a separate format.

On the canvas:
  double-click   edit the text (markdown — #, **bold**, lists, links)
  drag           move it
  bottom-right   drag the corner to resize
  top-left dot   cycle its colour
  Delete         remove the selected note
"""
from node_base import Node


class StickyNoteNode(Node):
    TYPE = "note.sticky"
    TITLE = "Note"
    CATEGORY = "data"
    INPUTS = 0
    OUTPUTS = 0
    PARAMS = [
        {"key": "text", "label": "Text", "type": "multiline",
         "default": "## Note\n\nDouble-click on the canvas to edit.",
         "desc": "Markdown. Edited on the canvas rather than in here — this "
                 "field is what gets saved."},
    ]

    def run(self, items):
        # Never reached: engine.run_workflow filters this type out before any
        # node is instantiated. Kept honest in case that ever changes.
        return items or []
