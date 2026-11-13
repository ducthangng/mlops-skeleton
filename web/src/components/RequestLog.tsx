import { useCollection } from "@cloudscape-design/collection-hooks";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Link from "@cloudscape-design/components/link";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator, { type StatusIndicatorProps } from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import { useCallback, useEffect, useState } from "react";

import { api, type RequestLogRow, type Stats } from "../api";

const STATUS: Record<string, StatusIndicatorProps.Type> = {
  ok: "success",
  error: "error",
  refused: "warning",
  cancelled: "stopped",
};

const ms = (v: number | null) => (v == null ? "-" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);

export default function RequestLog({ onOpenConversation }: { onOpenConversation: (id: string) => void }) {
  const [rows, setRows] = useState<RequestLogRow[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([api.requests(), api.stats()])
      .then(([r, s]) => {
        setRows(r);
        setStats(s);
        setError(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, [load]);

  const { items, collectionProps, filterProps, paginationProps, filteredItemsCount } = useCollection(rows, {
    filtering: {
      fields: ["model", "requested_model", "provider", "status", "cache_status", "routed_by", "route_reason"],
      empty: <Box textAlign="center" color="inherit">No requests yet. Send a chat message.</Box>,
      noMatch: <Box textAlign="center" color="inherit">No requests match the filter.</Box>,
    },
    pagination: { pageSize: 25 },
    sorting: { defaultState: { sortingColumn: { sortingField: "created_at" }, isDescending: true } },
  });

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Every call through the gateway, with its routing decision, cache result and latency."
          actions={<Button iconName="refresh" onClick={load} loading={loading} ariaLabel="Refresh" />}
        >
          Request log
        </Header>
      }
    >
      <SpaceBetween size="l">
        {error && <StatusIndicator type="error">{error}</StatusIndicator>}
        <Container header={<Header variant="h2" description={stats ? `Last ${stats.window} requests` : undefined}>Overview</Header>}>
          <KeyValuePairs
            columns={4}
            items={[
              { label: "Total requests", value: stats?.total_requests.toLocaleString() ?? "-" },
              {
                label: "Cache hit rate",
                value: stats?.cache_hit_rate == null ? "-" : `${(stats.cache_hit_rate * 100).toFixed(1)}%`,
              },
              { label: "Latency p50 / p95", value: stats ? `${ms(stats.latency_ms_p50)} / ${ms(stats.latency_ms_p95)}` : "-" },
              { label: "Time to first token p50", value: stats ? ms(stats.ttft_ms_p50) : "-" },
              { label: "Errors", value: stats?.errors.toLocaleString() ?? "-" },
              { label: "Routed by router", value: stats?.routed_by_router.toLocaleString() ?? "-" },
              {
                label: "Busiest model",
                value: stats?.by_model[0] ? `${stats.by_model[0].model} (${stats.by_model[0].requests})` : "-",
              },
            ]}
          />
        </Container>

        <Table
          {...collectionProps}
          items={items}
          loading={loading && rows.length === 0}
          loadingText="Loading requests"
          variant="container"
          stickyHeader
          wrapLines={false}
          header={<Header counter={`(${filteredItemsCount ?? rows.length})`}>Requests</Header>}
          filter={<TextFilter {...filterProps} filteringPlaceholder="Filter by model, status, cache, route" />}
          pagination={<Pagination {...paginationProps} />}
          columnDefinitions={[
            {
              id: "created_at",
              header: "Time",
              sortingField: "created_at",
              cell: (r) => new Date(r.created_at).toLocaleString(),
            },
            {
              id: "model",
              header: "Model",
              sortingField: "model",
              cell: (r) => (
                <span title={r.served_model ?? undefined}>
                  {r.provider}/{r.model}
                </span>
              ),
            },
            {
              id: "routed_by",
              header: "Routing",
              sortingField: "routed_by",
              cell: (r) =>
                r.routed_by === "router" ? (
                  <span title={r.route_reason ?? undefined}>
                    <Badge color="blue">router</Badge> from {r.requested_model}
                  </span>
                ) : (
                  <span title={r.route_reason ?? undefined}>user</span>
                ),
            },
            {
              id: "cache",
              header: "Cache",
              sortingField: "cache_status",
              cell: (r) => (r.cache_status === "hit" ? <Badge color="green">hit</Badge> : r.cache_status),
            },
            {
              id: "status",
              header: "Status",
              sortingField: "status",
              cell: (r) => (
                <span title={r.error ?? undefined}>
                  <StatusIndicator type={STATUS[r.status] ?? "info"}>{r.status}</StatusIndicator>
                </span>
              ),
            },
            { id: "ttft", header: "TTFT", sortingField: "ttft_ms", cell: (r) => ms(r.ttft_ms) },
            { id: "latency", header: "Latency", sortingField: "latency_ms", cell: (r) => ms(r.latency_ms) },
            {
              id: "tokens",
              header: "Tokens in / out",
              cell: (r) => `${r.input_tokens ?? "-"} / ${r.output_tokens ?? "-"}`,
            },
            {
              id: "chat",
              header: "Chat",
              cell: (r) =>
                r.conversation_id ? (
                  <Link
                    href={`#/chat/${r.conversation_id}`}
                    onFollow={(e) => {
                      e.preventDefault();
                      onOpenConversation(r.conversation_id!);
                    }}
                  >
                    Open
                  </Link>
                ) : (
                  <Box color="text-status-inactive">deleted</Box>
                ),
            },
          ]}
        />
      </SpaceBetween>
    </ContentLayout>
  );
}
