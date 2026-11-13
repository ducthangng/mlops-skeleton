import AppLayout from "@cloudscape-design/components/app-layout";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Flashbar, { type FlashbarProps } from "@cloudscape-design/components/flashbar";
import SideNavigation, { type SideNavigationProps } from "@cloudscape-design/components/side-navigation";
import SpaceBetween from "@cloudscape-design/components/space-between";
import TopNavigation from "@cloudscape-design/components/top-navigation";
import { useCallback, useEffect, useState } from "react";

import { api, type Catalog, type ConversationSummary, type RunSettings } from "./api";
import ChatView from "./components/ChatView";
import RequestLog from "./components/RequestLog";
import RunSettingsPanel from "./components/RunSettingsPanel";
import { mergeSettings } from "./settings";

// Hash routes: #/            new chat
//              #/chat/<id>   existing chat
//              #/requests    request log
type Route = { page: "chat"; id: string | null } | { page: "requests" };

function parseHash(): Route {
  const h = window.location.hash.replace(/^#/, "");
  if (h.startsWith("/requests")) return { page: "requests" };
  const m = h.match(/^\/chat\/([0-9a-f-]{36})/);
  return { page: "chat", id: m ? m[1] : null };
}

function useRoute(): [Route, (hash: string) => void] {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [route, (hash) => (window.location.hash = hash)];
}

export default function App() {
  const [route, go] = useRoute();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [defaults, setDefaults] = useState<RunSettings | null>(null);
  const [settings, setSettings] = useState<RunSettings | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  // Side panels start open on desktop, closed on phones (they overlay the chat there).
  const [navOpen, setNavOpen] = useState(() => window.innerWidth >= 1000);
  const [toolsOpen, setToolsOpen] = useState(() => window.innerWidth >= 1200);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState<FlashbarProps.MessageDefinition[]>([]);

  const notify = useCallback((type: FlashbarProps.Type, content: string) => {
    const id = `${Date.now()}`;
    const dismiss = () => setFlash((f) => f.filter((x) => x.id !== id));
    setFlash((f) => [...f.slice(-2), { id, type, content, dismissible: true, onDismiss: dismiss }]);
    if (type === "success") setTimeout(dismiss, 3000);
  }, []);

  const refreshConversations = useCallback(() => {
    api.conversations().then(setConversations).catch(() => undefined);
  }, []);

  useEffect(() => {
    Promise.all([api.catalog(), api.getDefaults()])
      .then(([c, d]) => {
        setCatalog(c);
        setDefaults(d);
        setSettings((s) => s ?? d);
      })
      .catch((e) => notify("error", `Can't reach the gateway API: ${e.message}`));
    refreshConversations();
  }, [notify, refreshConversations]);

  const chatId = route.page === "chat" ? route.id : null;

  // A new chat starts from the saved defaults.
  useEffect(() => {
    if (route.page === "chat" && route.id === null && defaults) setSettings(defaults);
  }, [route, defaults]);

  const navItems: SideNavigationProps.Item[] = [
    { type: "link", text: "Playground", href: "#/" },
    { type: "link", text: "Request log", href: "#/requests" },
    { type: "divider" },
    {
      type: "section-group",
      title: "Recent chats",
      items: conversations.length
        ? conversations.map((c) => ({ type: "link" as const, text: c.title, href: `#/chat/${c.id}`, info: c.model ? <Box variant="small" color="text-status-inactive">{c.model}</Box> : undefined }))
        : [{ type: "link" as const, text: "No chats yet", href: "#/" }],
    },
  ];
  const activeHref = route.page === "requests" ? "#/requests" : chatId ? `#/chat/${chatId}` : "#/";

  return (
    <>
      <div id="top-nav">
        <TopNavigation
          identity={{ href: "#/", title: "AI Gateway", logo: { src: LOGO, alt: "" } }}
          utilities={[
            {
              type: "button",
              text: catalog ? `Router: ${catalog.router_enabled ? "on" : "off"}` : "Router",
              iconName: catalog?.router_enabled ? "status-positive" : "status-stopped",
              title: "Set GATEWAY_ROUTER_CLASS to plug in a router",
            },
            {
              type: "button",
              text: catalog ? `Cache: ${catalog.cache_enabled ? "on" : "off"}` : "Cache",
              iconName: catalog?.cache_enabled ? "status-positive" : "status-stopped",
              title: "Set GATEWAY_CACHE_CLASS to plug in a cache",
            },
            { type: "button", text: "API docs", href: "/docs", external: true, externalIconAriaLabel: "(opens in a new tab)" },
          ]}
        />
      </div>
      <AppLayout
        headerSelector="#top-nav"
        navigationOpen={navOpen}
        onNavigationChange={({ detail }) => setNavOpen(detail.open)}
        navigationWidth={280}
        navigation={
          <div className="nav">
            <Box padding={{ horizontal: "l", top: "l" }}>
              <Button iconName="add-plus" fullWidth onClick={() => go("#/")}>
                New chat
              </Button>
            </Box>
            <SideNavigation
              activeHref={activeHref}
              items={navItems}
              onFollow={(e) => {
                e.preventDefault();
                go(e.detail.href);
              }}
            />
          </div>
        }
        toolsHide={route.page !== "chat"}
        toolsOpen={toolsOpen}
        onToolsChange={({ detail }) => setToolsOpen(detail.open)}
        toolsWidth={360}
        ariaLabels={{ tools: "Run settings", toolsToggle: "Open run settings", toolsClose: "Close run settings", navigation: "Chats" }}
        tools={
          <RunSettingsPanel
            catalog={catalog}
            value={settings}
            onChange={setSettings}
            saving={saving}
            onResetDefault={() => defaults && setSettings(defaults)}
            onSaveDefault={async () => {
              if (!settings) return;
              setSaving(true);
              try {
                setDefaults(await api.saveDefaults(settings));
                notify("success", "Saved as your default run settings.");
              } catch (e) {
                notify("error", (e as Error).message);
              } finally {
                setSaving(false);
              }
            }}
          />
        }
        notifications={flash.length ? <Flashbar items={flash} stackItems /> : undefined}
        disableContentPaddings={route.page === "chat"}
        content={
          route.page === "requests" ? (
            <RequestLog onOpenConversation={(id) => go(`#/chat/${id}`)} />
          ) : (
            <SpaceBetween size="m">
              <ChatView
                catalog={catalog}
                settings={settings}
                conversationId={chatId}
                onOpenConversation={(id) => go(id ? `#/chat/${id}` : "#/")}
                onConversationsChanged={refreshConversations}
                onSettingsRestored={(saved) => defaults && setSettings(mergeSettings(defaults, saved, catalog))}
                onError={(m) => notify("error", m)}
              />
            </SpaceBetween>
          )
        }
      />
    </>
  );
}

// Small inline logo: a routing fork in AWS orange.
const LOGO =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#ff9900"/><path d="M7 16h7l5-7h6M14 16l5 7h6" stroke="#0f141a" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/><circle cx="7" cy="16" r="2.4" fill="#0f141a"/></svg>`,
  );
