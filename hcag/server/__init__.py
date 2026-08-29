"""FastAPI backend for the hcag web app.

Exposes two endpoints consumed by the Next.js frontend at ``hcag/web``:

- ``POST /chat``           — runs an ``AgentRuntime`` turn against a session.
- ``POST /livekit/token``  — mints a LiveKit access token so the browser can
                             join the same room the ``hcag-voice serve`` worker
                             is publishing on.

Install with ``pip install -e ".[web,voice]"`` and run with ``hcag-server``.
"""
