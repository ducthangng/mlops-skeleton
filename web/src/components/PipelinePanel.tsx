import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Steps, { type StepsProps } from "@cloudscape-design/components/steps";

import type { Step } from "../api";

export interface PipelineView {
  steps: Step[];
  mode: "live" | "latest" | "selected" | "idle";
}

// Shown before the first message, so the user can see what will happen.
export function templateSteps(routerName: string): Step[] {
  return [
    ["cache", "Check cache"],
    ["router", `Ask ${routerName}`],
    ["model", "Model"],
    ["level", "Thinking level"],
    ["max_tokens", "Max output tokens"],
    ["sampling", "Temperature · Top P"],
    ["generate", "Generate answer"],
    ["save", "Save"],
  ].map(([id, label]) => ({ id, label, status: "pending" as const }));
}

const SUBTITLE: Record<PipelineView["mode"], string> = {
  live: "Running now",
  latest: "Last answer in this chat",
  selected: "Selected answer",
  idle: "Each step of your next request",
};

function sourceBadge(source: string | undefined, routerName: string) {
  if (!source) return null;
  if (source === "router") return <Badge color="blue">by {routerName}</Badge>;
  if (source === "cache") return <Badge color="green">from cache</Badge>;
  if (source === "you") return <Badge color="grey">by you</Badge>;
  return <Badge color="grey">{source}</Badge>;
}

const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`);

export default function PipelinePanel({ view, routerName }: { view: PipelineView; routerName: string }) {
  const steps: StepsProps.Step[] = view.steps.map((s) => ({
    status: s.status,
    statusIconAriaLabel: s.status,
    header: (
      <span className="step-header">
        <span className="step-header__label">{s.label}</span>
        {s.value && <span className="step-header__value">{s.value}</span>}
      </span>
    ),
    details:
      s.detail || s.source || s.ms != null ? (
        <div className="step-details">
          {sourceBadge(s.source, routerName)}
          {s.detail && <span>{s.detail}</span>}
          {s.ms != null && <span className="step-details__ms">{fmtMs(s.ms)}</span>}
        </div>
      ) : undefined,
  }));

  return (
    <div className="pipeline">
      <Box variant="small" color="text-body-secondary" padding={{ bottom: "s" }}>
        {SUBTITLE[view.mode]}
      </Box>
      <Steps
        steps={steps}
        ariaLabel="Request pipeline"
        // Custom render keeps the status icon in its own column beside a multi-part header.
        renderStep={(step) => ({ header: step.header, details: step.details })}
      />
    </div>
  );
}
