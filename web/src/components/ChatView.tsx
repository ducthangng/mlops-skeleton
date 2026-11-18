import Avatar from "@cloudscape-design/chat-components/avatar";
import ChatBubble from "@cloudscape-design/chat-components/chat-bubble";
import SupportPromptGroup from "@cloudscape-design/chat-components/support-prompt-group";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import ButtonDropdown from "@cloudscape-design/components/button-dropdown";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Header from "@cloudscape-design/components/header";
import LiveRegion from "@cloudscape-design/components/live-region";
import PromptInput from "@cloudscape-design/components/prompt-input";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { colorBackgroundLayoutMain } from "@cloudscape-design/design-tokens";
import { useEffect, useRef, useState } from "react";

import { api, type Catalog, type ChatMessage, type MessageMeta, type RunSettings, type Step, streamChat } from "../api";
import { findModel } from "../settings";
import Markdown from "./Markdown";
import { ConfirmDelete, RenameModal } from "./Modals";
import { type PipelineView, templateSteps } from "./PipelinePanel";

interface UiMessage extends ChatMessage {
  streaming?: boolean;
  steps?: Step[]; // live steps while streaming; saved ones are in meta.steps
}

const stepsOf = (m: UiMessage) => m.steps ?? m.meta?.steps ?? null;

interface Props {
  catalog: Catalog | null;
  settings: RunSettings | null;
  conversationId: string | null;
  onOpenConversation: (id: string | null) => void;
  onConversationsChanged: () => void;
  onSettingsRestored: (saved: Partial<RunSettings>) => void;
  onError: (message: string) => void;
  onPipeline: (view: PipelineView) => void;
}

const SUGGESTIONS = [
  { id: "code", text: "Why does my Go program deadlock when two goroutines share a mutex?" },
  { id: "math", text: "Prove that the square root of 2 is irrational." },
  { id: "chat", text: "Suggest a weekend hobby that gets me off screens." },
];

export default function ChatView(props: Props) {
  const { catalog, settings, conversationId } = props;
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [title, setTitle] = useState<string>("New chat");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const ownedId = useRef<string | null>(null); // conversation created by our own stream: don't reload it
  const bottomRef = useRef<HTMLDivElement>(null);

  // Load the conversation when the URL changes.
  useEffect(() => {
    setSelectedId(null);
    if (conversationId === null) {
      ownedId.current = null;
      setMessages([]);
      setTitle("New chat");
      return;
    }
    if (conversationId === ownedId.current) return;
    abortRef.current?.abort();
    let cancelled = false;
    setLoading(true);
    api
      .conversation(conversationId)
      .then((c) => {
        if (cancelled) return;
        setMessages(c.messages);
        setTitle(c.title);
        props.onSettingsRestored(c.settings);
      })
      .catch((e) => !cancelled && props.onError(`Could not open chat: ${e.message}`))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  // Tell the left-hand stepper which request to show.
  const routerName = catalog?.router_name ?? "JJ";
  useEffect(() => {
    const last = [...messages].reverse().find((m) => m.role === "assistant" && stepsOf(m));
    const selected = selectedId ? messages.find((m) => m.id === selectedId && stepsOf(m)) : undefined;
    if (streaming && last) props.onPipeline({ steps: stepsOf(last)!, mode: "live" });
    else if (selected) props.onPipeline({ steps: stepsOf(selected)!, mode: "selected" });
    else if (last) props.onPipeline({ steps: stepsOf(last)!, mode: "latest" });
    else props.onPipeline({ steps: templateSteps(routerName), mode: "idle" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, selectedId, streaming, routerName]);

  const patchLast = (fn: (m: UiMessage) => UiMessage) =>
    setMessages((ms) => (ms.length ? [...ms.slice(0, -1), fn(ms[ms.length - 1])] : ms));

  async function send(text: string) {
    const prompt = text.trim();
    if (!prompt || !settings || streaming) return;
    setInput("");
    const now = new Date().toISOString();
    setMessages((ms) => [
      ...ms,
      { id: `u-${now}`, role: "user", content: prompt, thinking: null, error: null, created_at: now, meta: null },
      {
        id: `a-${now}`, role: "assistant", content: "", thinking: null, error: null, created_at: now, meta: null,
        streaming: true, steps: templateSteps(routerName),
      },
    ]);
    setStreaming(true);
    setSelectedId(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let meta: MessageMeta | null = null;
    try {
      await streamChat(
        { conversation_id: conversationId, message: prompt, settings },
        {
          onMeta: (m) => {
            patchLast((x) => ({ ...x, steps: m.steps }));
            if (!conversationId) {
              ownedId.current = m.conversation_id;
              setTitle(prompt.split(/\s+/).join(" ").slice(0, 60));
              props.onOpenConversation(m.conversation_id);
            }
          },
          onStep: (step) =>
            patchLast((x) => ({ ...x, steps: (x.steps ?? []).map((s) => (s.id === step.id ? step : s)) })),
          onModel: (m) => {
            meta = {
              provider: m.provider, model: m.model, served_model: null, routed_by: m.routed_by,
              route_reason: m.route_reason, cache_status: "…", status: "streaming", stop_reason: null,
              input_tokens: null, output_tokens: null, ttft_ms: null, latency_ms: null,
            };
            patchLast((x) => ({ ...x, meta }));
          },
          onThinking: (t) => patchLast((x) => ({ ...x, thinking: (x.thinking ?? "") + t })),
          onText: (t) => patchLast((x) => ({ ...x, content: x.content + t })),
          onDone: (d) =>
            patchLast((x) => ({
              ...x,
              id: d.message_id,
              streaming: false,
              meta: x.meta && {
                ...x.meta, status: d.status, cache_status: d.cache_status, stop_reason: d.stop_reason,
                served_model: d.served_model, input_tokens: d.input_tokens, output_tokens: d.output_tokens,
                ttft_ms: d.ttft_ms, latency_ms: d.latency_ms,
              },
            })),
          onError: (message) => patchLast((x) => ({ ...x, streaming: false, error: message, meta: x.meta && { ...x.meta, status: "error" } })),
        },
        ctrl.signal,
      );
    } catch (e) {
      const aborted = (e as Error).name === "AbortError";
      patchLast((x) => ({
        ...x,
        streaming: false,
        error: aborted ? null : (e as Error).message,
        meta: x.meta && { ...x.meta, status: aborted ? "cancelled" : "error" },
      }));
    } finally {
      setStreaming(false);
      abortRef.current = null;
      props.onConversationsChanged();
    }
  }

  const model = findModel(catalog, settings ?? { provider: "", model: "" });
  const empty = !loading && messages.length === 0;

  return (
    <div className="chat">
      <div className="chat__header">
        <Header
          variant="h1"
          description={
            settings?.routing === "auto"
              ? `Auto · ${routerName} chooses the model and settings`
              : model
                ? `Manual · ${model.label}`
                : undefined
          }
          actions={
            conversationId && (
              <ButtonDropdown
                variant="icon"
                ariaLabel="Chat actions"
                items={[
                  { id: "rename", text: "Rename" },
                  { id: "delete", text: "Delete" },
                ]}
                onItemClick={({ detail }) => (detail.id === "rename" ? setRenameOpen(true) : setDeleteOpen(true))}
              />
            )
          }
        >
          {title}
        </Header>
      </div>

      <div className="chat__messages" role="log" aria-label="Conversation">
        {loading && <StatusIndicator type="loading">Loading chat</StatusIndicator>}
        {empty && (
          <div className="chat__empty">
            <SpaceBetween size="l" alignItems="center">
              <Avatar color="gen-ai" iconName="gen-ai" ariaLabel="AI Gateway" width={48} />
              <Box variant="h2" textAlign="center">
                What do you want to ask?
              </Box>
              <Box color="text-body-secondary" textAlign="center">
                The gateway sends your prompt to the model in Run settings, or to the one the router picks when routing is Auto.
              </Box>
              <SupportPromptGroup
                ariaLabel="Suggested prompts"
                alignment="vertical"
                items={SUGGESTIONS}
                onItemClick={({ detail }) => send(SUGGESTIONS.find((s) => s.id === detail.id)?.text ?? "")}
              />
            </SpaceBetween>
          </div>
        )}
        <SpaceBetween size="m">
          {messages.map((m) =>
            m.role === "user" ? (
              <ChatBubble
                key={m.id}
                type="outgoing"
                ariaLabel="You"
                avatar={<Avatar ariaLabel="You" iconName="user-profile" tooltipText="You" />}
              >
                <div className="bubble-text">{m.content}</div>
              </ChatBubble>
            ) : (
              <AssistantBubble
                key={m.id}
                m={m}
                catalog={catalog}
                selected={selectedId === m.id}
                onSelect={settings?.routing === "auto" && stepsOf(m) && !m.streaming ? () => setSelectedId(selectedId === m.id ? null : m.id) : undefined}
              />
            ),
          )}
        </SpaceBetween>
        <div ref={bottomRef} />
      </div>

      <div className="chat__input" style={{ background: `linear-gradient(to bottom, transparent 0, ${colorBackgroundLayoutMain} 14px)` }}>
        <PromptInput
          value={input}
          onChange={({ detail }) => setInput(detail.value)}
          onAction={() => (streaming ? abortRef.current?.abort() : send(input))}
          actionButtonIconName={streaming ? "stop-circle" : "send"}
          actionButtonAriaLabel={streaming ? "Stop generating" : "Send message"}
          disableActionButton={!streaming && (!input.trim() || !settings)}
          placeholder="Ask anything. Enter to send, Shift+Enter for a new line."
          minRows={2}
          maxRows={12}
          ariaLabel="Prompt"
        />
        <Box variant="small" color="text-status-inactive" padding={{ top: "xxs" }}>
          Responses are saved to Postgres and listed in the request log.
        </Box>
      </div>

      {conversationId && (
        <>
          <RenameModal
            visible={renameOpen}
            initial={title}
            onDismiss={() => setRenameOpen(false)}
            onSave={async (t) => {
              try {
                await api.rename(conversationId, t);
                setTitle(t);
                props.onConversationsChanged();
              } catch (e) {
                props.onError((e as Error).message);
              }
              setRenameOpen(false);
            }}
          />
          <ConfirmDelete
            visible={deleteOpen}
            title={title}
            onDismiss={() => setDeleteOpen(false)}
            onConfirm={async () => {
              try {
                await api.remove(conversationId);
                props.onConversationsChanged();
                props.onOpenConversation(null);
              } catch (e) {
                props.onError((e as Error).message);
              }
              setDeleteOpen(false);
            }}
          />
        </>
      )}
    </div>
  );
}

function AssistantBubble(props: { m: UiMessage; catalog: Catalog | null; selected: boolean; onSelect?: () => void }) {
  const { m, catalog } = props;
  const label = m.meta ? findModel(catalog, m.meta)?.label ?? m.meta.model : "Assistant";
  const waiting = m.streaming && !m.content;
  return (
    <ChatBubble
      type="incoming"
      ariaLabel={label}
      showLoadingBar={m.streaming}
      avatar={<Avatar color="gen-ai" iconName="gen-ai" ariaLabel={label} tooltipText={label} loading={waiting} />}
      actions={
        !m.streaming && m.content ? (
          <CopyToClipboard
            variant="icon"
            textToCopy={m.content}
            copyButtonAriaLabel="Copy response"
            copySuccessText="Copied"
            copyErrorText="Couldn't copy"
          />
        ) : undefined
      }
    >
      <SpaceBetween size="s">
        {m.meta && <MetaLine meta={m.meta} label={label} routerName={catalog?.router_name ?? "JJ"} selected={props.selected} onSelect={props.onSelect} />}
        {m.thinking && (
          <ExpandableSection headerText={m.streaming && !m.content ? "Thinking…" : "Thoughts"} variant="footer">
            <div className="thinking">
              <Markdown text={m.thinking} />
            </div>
          </ExpandableSection>
        )}
        {waiting && !m.thinking && <StatusIndicator type="loading">Waiting for the model</StatusIndicator>}
        {m.content && <Markdown text={m.content} />}
        {m.meta?.status === "refused" && (
          <Alert type="warning">The model declined to answer this request.</Alert>
        )}
        {m.meta?.status === "cancelled" && (
          <Box color="text-status-inactive" fontSize="body-s">
            Stopped.
          </Box>
        )}
        {m.error && (
          <Alert type="error" header="Request failed">
            {m.error}
          </Alert>
        )}
      </SpaceBetween>
      <LiveRegion hidden>{m.streaming ? "" : m.content}</LiveRegion>
    </ChatBubble>
  );
}

function MetaLine(props: { meta: MessageMeta; label: string; routerName: string; selected: boolean; onSelect?: () => void }) {
  const { meta, label } = props;
  const parts: string[] = [];
  if (meta.served_model && meta.served_model !== meta.model) parts.push(`served by ${meta.served_model}`);
  if (meta.cache_status === "hit") parts.push("cache hit");
  if (meta.latency_ms != null) parts.push(`${(meta.latency_ms / 1000).toFixed(2)} s`);
  if (meta.ttft_ms != null && meta.cache_status !== "hit") parts.push(`TTFT ${Math.round(meta.ttft_ms)} ms`);
  if (meta.output_tokens) parts.push(`${meta.output_tokens.toLocaleString()} tokens out`);
  return (
    <div className="meta-line">
      <span className={meta.routed_by === "router" ? "chip chip--router" : "chip"} title={meta.route_reason ?? undefined}>
        {meta.routed_by === "router" ? `${props.routerName}'s pick` : "Your pick"} · {label}
      </span>
      {meta.routed_by === "router" && meta.route_reason && <span className="meta-line__reason">{meta.route_reason}</span>}
      {parts.length > 0 && <span className="meta-line__stats">{parts.join(" · ")}</span>}
      {props.onSelect && (
        <button type="button" className="meta-line__steps" aria-pressed={props.selected} onClick={props.onSelect}>
          {props.selected ? "Showing steps" : "Show steps"}
        </button>
      )}
    </div>
  );
}
