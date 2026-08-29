# hcag-web — self-service support chatbot + voicebot

Next.js frontend that renders a mock "Work Pass Authority" host page with the
HCAG chat and voice assistant embedded. Ported from a Claude Design handoff.

The UI works standalone against a scripted mock flow. Flip one env var and it
talks to a real `AgentRuntime` (chat) and a LiveKit voice worker (voice).

## Layout

- `app/`            — Next.js App Router pages + API route proxies.
- `components/`     — Host page + chat widget (launcher, panel, voice overlay).
- `lib/`            — `chat-client`, `voice-client` (LiveKit hook).
- The Python backend lives at `hcag/server/` (a proper submodule of the `hcag`
  package). See "Backend" below.

## Frontend: run

```bash
cd hcag/web
npm install
npm run dev          # http://localhost:3000
```

Out of the box the chat runs the prototype's scripted flow — no backend needed.

## Turn on the real backend

The widget switches to the FastAPI server when `NEXT_PUBLIC_USE_API=1` is set.
Copy `.env.example` to `.env.local` and edit:

```bash
NEXT_PUBLIC_USE_API=1
HCAG_API_URL=http://localhost:8000
NEXT_PUBLIC_LIVEKIT_URL=wss://your-livekit.livekit.cloud
```

`HCAG_API_URL` is server-side only (used by the Next.js API route proxies).
`NEXT_PUBLIC_LIVEKIT_URL` is exposed to the browser and only informational —
the actual `wss://` URL the client connects to is returned by
`POST /livekit/token`.

## Backend

The Python service is `hcag.server` (installed as `hcag-server`).

```bash
# From the repo root
pip install -e ".[web,voice,dev]"

# Chat backend — talks to the HCAG AgentRuntime
ANTHROPIC_API_KEY=... hcag-server serve \
    --agent-config ./examples/agent.toml \
    --port 8000
```

Endpoints:

- `POST /chat` — `{ session_id, message, history[] }` → `{ text, session_id }`.
  Reuses an `AgentRuntime` per `session_id` so the KB catalog + active packet
  set stay warm across turns.
- `POST /livekit/token` — `{ identity, room? }` → `{ url, token, room }`. Mints
  a LiveKit access token so the browser can join the same room the
  `hcag-voice` worker is publishing on.
- `GET /health` — sanity probe.

Session state lives in-process — this is a single-node dev server. For prod,
front it with a session store.

## Voice

Voice mode joins a LiveKit room served by `hcag-voice`. Three processes need
to be running:

1. **LiveKit** — either self-hosted or LiveKit Cloud.
2. **Voice worker** — subscribes to rooms and drives STT → HCAG → TTS.
   ```bash
   hcag-voice serve --config ./voice.toml
   ```
3. **`hcag-server`** — mints the browser's LiveKit token.

Required env for the backend (or set in `voice.toml`):

```
LIVEKIT_URL=wss://your-livekit.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_ROOM_PREFIX=hcag-        # optional
HCAG_VOICE_CONFIG=./voice.toml   # optional, lets the server read url + prefix
```

When the user opens voice mode, the browser:

1. Calls `/api/livekit/token` (proxied to `POST /livekit/token`).
2. Connects to the returned LiveKit URL + room.
3. Publishes the mic and subscribes to the agent's audio.

The `hcag-voice` worker joins the same room (by convention `hcag-<identity>`)
and drives the conversation. The overlay reflects speaking/listening based on
`ActiveSpeakersChanged` events and shows transcriptions if the worker
publishes them.

## Wheel packaging

`hcag/web/node_modules`, `hcag/web/.next`, and other build artifacts are
excluded from the Python wheel in `pyproject.toml`. The frontend source stays
under `hcag/web/` so the repo layout keeps everything web-related in one place;
the Python backend is a proper submodule at `hcag/server/`.
