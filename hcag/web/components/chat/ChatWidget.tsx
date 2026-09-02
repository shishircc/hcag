"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Composer from "./Composer";
import DevBar from "./DevBar";
import Launcher from "./Launcher";
import Message from "./Message";
import PanelHeader from "./PanelHeader";
import Thinking from "./Thinking";
import VoiceOverlay from "./VoiceOverlay";
import type { ChatMessage, Device, Feedback, View } from "./types";
import {
  isApiEnabled,
  scriptedReply,
  sendChat,
  streamChat,
  StreamUnsupported,
  shortPacketName,
  type TurnEvent,
} from "@/lib/chat-client";
import { useLiveKitVoice } from "@/lib/voice-client";

function newId() {
  return Math.random().toString(36).slice(2, 10);
}

function panelStyle(view: View, device: Device): React.CSSProperties {
  const mobile = device === "mobile";
  const focus = view === "focus";
  const base: React.CSSProperties = {
    position: "fixed",
    zIndex: 50,
    background: "#fff",
    border: "1px solid var(--line-2)",
    borderRadius: 14,
    boxShadow: "0 24px 60px rgba(16,20,24,.24)",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  };
  if (mobile) {
    return {
      ...base,
      top: 56,
      height: "min(844px, calc(100vh - 72px))",
      width: 412,
      left: "50%",
      transform: "translateX(-50%)",
      borderRadius: 0,
      border: 0,
    };
  }
  if (focus) {
    return {
      ...base,
      left: "50%",
      transform: "translateX(-50%)",
      bottom: 16,
      top: 16,
      width: "calc(100vw - 96px)",
      maxWidth: 1320,
    };
  }
  return {
    ...base,
    right: 24,
    bottom: 24,
    width: 400,
    height: "min(620px, calc(100vh - 48px))",
  };
}

export default function ChatWidget({
  botName = "Work pass assistant",
  initialView = "min",
  showNudgeInitial = true,
}: {
  botName?: string;
  initialView?: View;
  showNudgeInitial?: boolean;
}) {
  const [view, setView] = useState<View>(initialView);
  const [device, setDevice] = useState<Device>("desktop");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // What the agent is doing right now, from tool.* events (§2.14.1).
  const [activity, setActivity] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [thinking, setThinking] = useState(false);
  const [voice, setVoice] = useState(false);
  const [draft, setDraft] = useState("");
  const [badge, setBadge] = useState(false);
  const [nudge, setNudge] = useState(showNudgeInitial);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [voiceStateLocal, setVoiceStateLocal] = useState("Listening");
  const [captionLocal, setCaptionLocal] = useState("Listening…");
  const scroller = useRef<HTMLDivElement | null>(null);
  const sessionId = useMemo(() => newId(), []);
  const apiEnabled = useMemo(() => isApiEnabled(), []);

  const mobile = device === "mobile";
  const focus = view === "focus";
  const isEmpty = messages.length === 0 && !thinking;
  const showVoiceInvite = focus && !mobile && isEmpty && !voice;
  const dimmed = focus && !mobile;

  const scrollDown = useCallback(() => {
    // Defer so the DOM has laid out the new message before we jump.
    const id = window.setTimeout(() => {
      const el = scroller.current;
      if (el) el.scrollTop = el.scrollHeight;
    }, 40);
    return () => window.clearTimeout(id);
  }, []);

  useEffect(() => {
    scrollDown();
  }, [messages, thinking, scrollDown]);

  const voiceHook = useLiveKitVoice({
    active: voice && apiEnabled,
    sessionId,
    onCaption: (text) => setCaptionLocal(text),
  });

  const voiceState = apiEnabled
    ? voiceHook.status === "error"
      ? "Voice unavailable"
      : voiceHook.status.charAt(0).toUpperCase() + voiceHook.status.slice(1)
    : voiceStateLocal;
  const caption = apiEnabled ? voiceHook.caption : captionLocal;

  const push = useCallback((m: ChatMessage) => {
    setMessages((prev) => prev.concat([m]));
  }, []);

  const advance = useCallback(
    async (userText: string) => {
      if (userText) push({ id: newId(), role: "user", text: userText });
      const currentStep = step;
      setThinking(true);
      setDraft("");
      setNudge(false);

      const finalize = (reply: {
        text?: string;
        card?: ChatMessage["card"];
        sources?: ChatMessage["sources"];
        hasFeedback?: boolean;
      }) => {
        setThinking(false);
        setStep(currentStep + 1);
        push({ id: newId(), role: "bot", ...reply });
        if (voice) {
          setVoiceStateLocal("Speaking");
          setCaptionLocal(reply.text ?? "");
          window.setTimeout(() => {
            setVoiceStateLocal("Listening");
            setCaptionLocal("Listening…");
          }, 2400);
        }
      };

      if (apiEnabled) {
        // Stream by default (§10.4). An HCAG turn loads a packet before its
        // first token, so synchronously that whole interval is a blank panel;
        // streaming turns it into visible — and, via tool events, *named* —
        // progress.
        const botId = newId();
        let started = false;
        let accumulated = "";

        const onEvent = (e: TurnEvent) => {
          if (e.kind === "tool.start") {
            // The one place HCAG's structure is visible to an end user:
            // "Consulting <packet>…" instead of a spinner.
            const what = (e.requested ?? []).map(shortPacketName).join(", ");
            setActivity(what ? `Consulting ${what}…` : "Looking things up…");
            return;
          }
          if (e.kind === "tool.end") {
            setActivity(null);
            return;
          }
          if (e.kind !== "assistant.delta" || !e.text) return;
          accumulated += e.text;
          if (!started) {
            started = true;
            setThinking(false);
            push({ id: botId, role: "bot", text: accumulated });
            return;
          }
          // Replace the bubble's text rather than appending a new one: the
          // Markdown renderer re-parses the accumulated document each time
          // (§10.3.3), because a fragment ending mid-table is not a document.
          setMessages((prev) =>
            prev.map((m) => (m.id === botId ? { ...m, text: accumulated } : m)),
          );
        };

        try {
          const answer = await streamChat(sessionId, messages, userText, onEvent);
          setActivity(null);
          if (started) {
            setMessages((prev) =>
              prev.map((m) => (m.id === botId ? { ...m, text: answer } : m)),
            );
            setThinking(false);
            setStep(currentStep + 1);
            if (voice) {
              setVoiceStateLocal("Speaking");
              setCaptionLocal(answer);
              window.setTimeout(() => {
                setVoiceStateLocal("Listening");
                setCaptionLocal("Listening…");
              }, 2400);
            }
          } else {
            finalize({ text: answer });
          }
        } catch (e) {
          setActivity(null);
          if (e instanceof StreamUnsupported) {
            // A backend without a tool loop (§9.5) answers 501; fall back
            // rather than showing the user a transport detail.
            try {
              finalize(await sendChat(sessionId, messages, userText));
            } catch (e2) {
              finalize({
                text: `I couldn't reach the assistant service. ${(e2 as Error).message}`,
              });
            }
          } else if (started) {
            // A stream that ended without assistant.final is a FAILED turn,
            // not a short answer (§2.14.3) — say so rather than leaving a
            // truncated reply looking complete.
            setThinking(false);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === botId
                  ? { ...m, text: `${accumulated}\n\n_(interrupted — ${(e as Error).message})_` }
                  : m,
              ),
            );
          } else {
            finalize({
              text: `I couldn't reach the assistant service. ${(e as Error).message}`,
            });
          }
        }
      } else {
        // Scripted flow — matches the design prototype's four turns.
        window.setTimeout(() => finalize(scriptedReply(currentStep)), 1100);
      }
    },
    [apiEnabled, messages, push, sessionId, step, voice],
  );

  const escalate = useCallback(() => {
    push({ id: newId(), role: "user", text: "Talk to an officer" });
    setThinking(true);
    window.setTimeout(() => {
      setThinking(false);
      push({ id: newId(), role: "bot", isEscalate: true });
    }, 900);
  }, [push]);

  const enterVoice = useCallback(() => {
    setView((v) => (v === "min" ? "docked" : v));
    setVoice(true);
    setBadge(false);
    setNudge(false);
    setVoiceStateLocal("Listening");
    setCaptionLocal("Listening…");
    if (!apiEnabled) {
      // Mock: fake a heard utterance and advance the scripted flow.
      window.setTimeout(() => {
        const said = step === 0 ? "Which work pass am I eligible for?" : "Go on.";
        setVoiceStateLocal("Heard you");
        setCaptionLocal("“" + said + "”");
        window.setTimeout(() => void advance(said), 700);
      }, 1800);
    }
  }, [advance, apiEnabled, step]);

  const reset = useCallback(() => {
    setView("min");
    setMessages([]);
    setStep(0);
    setThinking(false);
    setVoice(false);
    setDraft("");
    setBadge(false);
    setNudge(true);
    setFeedback(null);
  }, []);

  const send = useCallback(() => {
    if (draft.trim()) void advance(draft.trim());
  }, [advance, draft]);

  const statusLine = voice
    ? "Voice mode"
    : apiEnabled
      ? "Live · answers grounded in the knowledge base"
      : "Answers with links to source pages";

  return (
    <>
      <DevBar
        device={device}
        onDesktop={() => setDevice("desktop")}
        onMobile={() => setDevice("mobile")}
        onReset={reset}
      />

      {dimmed ? (
        <div
          onClick={() => setView("docked")}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(16,20,24,.45)",
            zIndex: 40,
          }}
        />
      ) : null}

      {view === "min" ? (
        <Launcher
          mobile={mobile}
          showNudge={nudge && !mobile && showNudgeInitial}
          hasBadge={badge}
          draft={draft}
          onDraft={setDraft}
          onSubmit={(text) => {
            setView("docked");
            setBadge(false);
            void advance(text);
          }}
          onOpenDocked={() => {
            setView("docked");
            setBadge(false);
            setNudge(false);
          }}
          onEnterVoice={enterVoice}
          onHideNudge={() => setNudge(false)}
          onStartTriage={() => {
            setView(mobile ? "docked" : "focus");
            setBadge(false);
            setNudge(false);
          }}
        />
      ) : (
        <div role="dialog" aria-label={botName} style={panelStyle(view, device)}>
          <PanelHeader
            botName={botName}
            statusLine={statusLine}
            focus={focus}
            onEnterVoice={enterVoice}
            onToggleFocus={() => setView(focus ? "docked" : "focus")}
            onMinimize={() => {
              setView("min");
              setVoice(false);
            }}
            onDismiss={() => {
              setView("min");
              setVoice(false);
              setBadge(true);
            }}
          />

          <div ref={scroller} style={{ flex: 1, overflowY: "auto", background: "#fff" }}>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 18,
                padding: focus && !mobile ? "28px 32px" : "18px 16px",
                maxWidth: focus && !mobile ? 720 : "none",
                margin: "0 auto",
                width: "100%",
              }}
            >
              {isEmpty ? (
                <div
                  style={{
                    maxWidth: "92%",
                    background: "var(--bg-2)",
                    border: "1px solid var(--bg-msg-border)",
                    padding: "12px 14px",
                    borderRadius: "14px 14px 14px 4px",
                    lineHeight: 1.55,
                    color: "var(--ink)",
                  }}
                >
                  Hello, you are through to work pass support. How can I help you today?
                </div>
              ) : null}

              {showVoiceInvite ? (
                <button
                  type="button"
                  onClick={enterVoice}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 14,
                    textAlign: "left",
                    background: "var(--primary-tint)",
                    border: "1px solid var(--primary-border-soft)",
                    borderRadius: 14,
                    padding: "16px 18px",
                    cursor: "pointer",
                    width: "100%",
                  }}
                >
                  <span
                    style={{
                      width: 46,
                      height: 46,
                      borderRadius: 999,
                      background: "var(--primary)",
                      color: "#fff",
                      display: "grid",
                      placeItems: "center",
                      fontSize: 20,
                      flex: "none",
                    }}
                  >
                    ◍
                  </span>
                  <span style={{ lineHeight: 1.35 }}>
                    <span
                      style={{
                        display: "block",
                        fontWeight: 700,
                        fontSize: 16,
                        color: "var(--ink)",
                      }}
                    >
                      Rather speak than type?
                    </span>
                    <span style={{ display: "block", fontSize: 14, color: "var(--muted)" }}>
                      Start a voice conversation. You can switch back to typing at any time.
                    </span>
                  </span>
                </button>
              ) : null}

              {messages.map((m) => (
                <Message
                  key={m.id}
                  msg={m}
                  feedback={feedback}
                  onThumbUp={() => setFeedback("up")}
                  onThumbDown={() => setFeedback("down")}
                  onEscalate={escalate}
                />
              ))}

              {/* tool.* events name the packet being loaded, so the wait is
                  specific rather than a spinner (§10.4). */}
              {thinking || activity ? <Thinking label={activity ?? undefined} /> : null}
            </div>
          </div>

          <Composer
            draft={draft}
            onDraft={setDraft}
            onSend={send}
            onEnterVoice={enterVoice}
            focus={focus}
            mobile={mobile}
            onDownload={() => setFeedback("downloaded")}
          />

          {voice ? (
            <VoiceOverlay
              voiceState={voiceState}
              caption={caption}
              onExit={() => setVoice(false)}
              onEnd={() => {
                setVoice(false);
                setView("min");
                setBadge(true);
              }}
            />
          ) : null}
        </div>
      )}
    </>
  );
}
