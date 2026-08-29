"use client";

import { useEffect, useRef, useState } from "react";
import {
  Room,
  RoomEvent,
  Track,
  createLocalAudioTrack,
  type RemoteAudioTrack,
  type RemoteTrackPublication,
  type RemoteParticipant,
  type TranscriptionSegment,
} from "livekit-client";

export type VoiceStatus = "idle" | "connecting" | "listening" | "speaking" | "error";

type Options = {
  active: boolean;
  sessionId: string;
  onCaption?: (text: string) => void;
};

type Result = {
  status: VoiceStatus;
  caption: string;
  error?: string;
};

async function fetchToken(sessionId: string): Promise<{ url: string; token: string }> {
  const res = await fetch("/api/livekit/token", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ identity: sessionId }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`livekit token failed: ${res.status} ${body}`);
  }
  return (await res.json()) as { url: string; token: string };
}

// Manages a LiveKit Room lifecycle in response to `active`. When active flips on,
// it fetches a token, connects, publishes the mic and attaches remote audio to a
// hidden <audio> element. Flip it off and it tears everything down.
export function useLiveKitVoice({ active, sessionId, onCaption }: Options): Result {
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [caption, setCaption] = useState<string>("");
  const [error, setError] = useState<string | undefined>(undefined);
  const roomRef = useRef<Room | null>(null);
  const audioElRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    if (!active) {
      setStatus("idle");
      setCaption("");
      setError(undefined);
      return;
    }
    let cancelled = false;

    async function connect() {
      try {
        setStatus("connecting");
        setCaption("Connecting…");
        setError(undefined);
        const { url, token } = await fetchToken(sessionId);
        if (cancelled) return;

        const room = new Room({ adaptiveStream: true, dynacast: true });
        roomRef.current = room;

        room.on(RoomEvent.TrackSubscribed, (track, _pub, _participant) => {
          if (track.kind === Track.Kind.Audio) {
            const el = document.createElement("audio");
            el.autoplay = true;
            (track as RemoteAudioTrack).attach(el);
            document.body.appendChild(el);
            audioElRef.current = el;
          }
        });

        room.on(
          RoomEvent.TrackUnsubscribed,
          (_track, _pub: RemoteTrackPublication, _p: RemoteParticipant) => {
            if (audioElRef.current) {
              audioElRef.current.remove();
              audioElRef.current = null;
            }
          },
        );

        // Speaking state — when a remote participant is active, the agent is talking.
        room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
          const remoteActive = speakers.some((s) => s.identity !== room.localParticipant.identity);
          setStatus(remoteActive ? "speaking" : "listening");
          if (remoteActive) setCaption("Speaking");
          else setCaption("Listening…");
        });

        // Transcriptions arrive as text tracks / data messages depending on the
        // livekit-agents version. Support both if present.
        room.on(
          RoomEvent.TranscriptionReceived,
          (segments: TranscriptionSegment[], _p, _pub) => {
            const finals = segments.filter((s) => s.final);
            const last = (finals[finals.length - 1] ?? segments[segments.length - 1])?.text;
            if (last) {
              setCaption(last);
              onCaption?.(last);
            }
          },
        );

        await room.connect(url, token);
        if (cancelled) {
          await room.disconnect();
          return;
        }

        const mic = await createLocalAudioTrack();
        await room.localParticipant.publishTrack(mic);
        setStatus("listening");
        setCaption("Listening…");
      } catch (e) {
        if (cancelled) return;
        setStatus("error");
        setError(String((e as Error).message ?? e));
        setCaption("Voice unavailable");
      }
    }

    void connect();

    return () => {
      cancelled = true;
      const room = roomRef.current;
      roomRef.current = null;
      if (room) {
        void room.disconnect();
      }
      if (audioElRef.current) {
        audioElRef.current.remove();
        audioElRef.current = null;
      }
    };
  }, [active, sessionId, onCaption]);

  return { status, caption, error };
}
