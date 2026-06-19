"""Example instrument specs for the arena prototype.

In production these come from instrument-maker registry.yaml.
These inline examples mirror the registry schema closely enough that
swapping in the real registry is a one-line change.
"""

from arena.specs.example_instruments import EXAMPLE_SPECS, get_spec

__all__ = ["EXAMPLE_SPECS", "get_spec"]
