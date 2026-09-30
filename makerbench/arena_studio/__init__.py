"""MakerBench Arena Studio (Issue #696).

A unified web cockpit for running, observing, voting, and analyzing the
Code-CAD A/B Arena (Epic #421 / #694).
"""

def create_studio_app(*args, **kwargs):
    """Import the optional HTTP stack when a Studio server is requested."""
    from .app import create_studio_app as factory

    return factory(*args, **kwargs)

__all__ = ["create_studio_app"]
