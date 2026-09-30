"""Hugging Face entrypoint: only the read-only Studio app is constructed."""
from makerbench.arena_studio.demo import create_demo_app

app = create_demo_app(allowed_hosts=("*",))
