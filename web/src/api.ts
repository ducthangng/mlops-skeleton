// Typed client for the gateway API (gateway/gateway/api.py).

export interface RunSettings {
  provider: string;
  model: string;
  system_prompt: string;
  temperature: number | null;
  top_p: number | null;
  max_tokens: number | null;
  level: string | null;
  show_thinking: boolean;
  routing: "auto" | "manual";
  use_cache: boolean;
}

export interface ProviderInfo {
  name: string;
  label: string;
  type: string;
  configured: boolean;
  api_key_env: string | null;
}

export interface ModelInfo {
  key: string;
  id: string;
  provider: string;
  label: string;
  description: string;
  levels: string[];
  default_level: string | null;
  sampling: boolean;
  max_output_tokens: number;
  default_max_tokens: number;
  context_window: number | null;
  available: boolean;
}

export interface Catalog {
  providers: ProviderInfo[];
  models: ModelInfo[];
  cache_enabled: boolean;
  router_enabled: boolean;
}

export interface MessageMeta {
  provider: string;
  model: string;
  served_model: string | null;
  routed_by: string;
  route_reason: string | null;
  cache_status: string;
  status: string;
  stop_reason: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  ttft_ms: number | null;
  latency_ms: number | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking: string | null;
  error: string | null;
  created_at: string;
  meta: MessageMeta | null;
}

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string;
  model: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  settings: Partial<RunSettings>;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
}

export interface RequestLogRow extends MessageMeta {
  id: string;
  conversation_id: string | null;
  requested_provider: string;
  requested_model: string;
  route_score: number | null;
  params: Record<string, unknown>;
  error: string | null;
  created_at: string;
}

export interface Stats {
  total_requests: number;
  window: number;
  errors: number;
  routed_by_router: number;
  cache_hit_rate: number | null;
  latency_ms_p50: number | null;
  latency_ms_p95: number | null;
  ttft_ms_p50: number | null;
  by_model: { model: string; requests: number; errors: number; output_tokens: number; latency_ms_p50: number | null }[];
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  catalog: () => fetch("/api/catalog").then(json<Catalog>),
  getDefaults: () => fetch("/api/settings").then(json<RunSettings>),
  saveDefaults: (s: RunSettings) => fetch("/api/settings", send("PUT", s)).then(json<RunSettings>),
  conversations: () => fetch("/api/conversations").then(json<ConversationSummary[]>),
  conversation: (id: string) => fetch(`/api/conversations/${id}`).then(json<Conversation>),
  rename: (id: string, title: string) =>
    fetch(`/api/conversations/${id}`, send("PATCH", { title })).then(json<ConversationSummary>),
  remove: (id: string) => fetch(`/api/conversations/${id}`, send("DELETE")).then(json<void>),
  requests: () => fetch("/api/requests?limit=500").then(json<RequestLogRow[]>),
  stats: () => fetch("/api/stats").then(json<Stats>),
};

// ---------- streaming chat (Server-Sent Events over a POST) ----------

export interface MetaEvent {
  request_id: string;
  conversation_id: string;
  provider: string;
  model: string;
  model_label: string;
  routed_by: string;
  route_reason: string | null;
  params: Record<string, unknown>;
}

export interface DoneEvent {
  message_id: string;
  status: string;
  cache_status: string;
  stop_reason: string | null;
  served_model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  ttft_ms: number | null;
  latency_ms: number | null;
}

export interface ChatHandlers {
  onMeta: (m: MetaEvent) => void;
  onText: (t: string) => void;
  onThinking: (t: string) => void;
  onDone: (d: DoneEvent) => void;
  onError: (message: string) => void;
}

export async function streamChat(
  body: { conversation_id: string | null; message: string; settings: RunSettings },
  h: ChatHandlers,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat", { ...send("POST", body), signal });
  if (!res.ok || !res.body) {
    await json(res); // throws with the server's detail
    throw new Error("No response body");
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === "meta") h.onMeta(payload);
      else if (event === "text") h.onText(payload.text);
      else if (event === "thinking") h.onThinking(payload.text);
      else if (event === "done") h.onDone(payload);
      else if (event === "error") h.onError(payload.message);
    }
  }
}
