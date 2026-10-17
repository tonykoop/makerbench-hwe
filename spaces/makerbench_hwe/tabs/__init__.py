"""Tabs of the MakerBench HWE Space, one module per tab.

Each tab module exposes:

* ``TAB_ID``: a stable identifier (used as the Gradio tab id);
* ``TITLE``: the tab label shown to visitors;
* ``render()``: builds the tab's components inside the current ``gr.Tab``
  context. Gradio is imported inside ``render()`` so the module imports with
  the standard library alone.

Keeping each tab in its own module lets the stories that fill in a tab
(#1028 Grade a part, #1029 Explore designs) change only their own file.
"""
