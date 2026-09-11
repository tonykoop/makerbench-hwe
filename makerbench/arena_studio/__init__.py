"""MakerBench Arena Studio (Issue #696).

A unified web cockpit for running, observing, voting, and analyzing the
Code-CAD A/B Arena (Epic #421 / #694).
"""

from .app import create_studio_app

__all__ = ["create_studio_app"]
