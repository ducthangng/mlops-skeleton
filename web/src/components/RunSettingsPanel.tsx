import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import Select, { type SelectProps } from "@cloudscape-design/components/select";
import Slider from "@cloudscape-design/components/slider";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Textarea from "@cloudscape-design/components/textarea";
import Toggle from "@cloudscape-design/components/toggle";

import type { Catalog, RunSettings } from "../api";
import PipelinePanel, { type PipelineView } from "./PipelinePanel";
import { applyModel, findModel } from "../settings";

interface Props {
  catalog: Catalog | null;
  value: RunSettings | null;
  onChange: (s: RunSettings) => void;
  onSaveDefault: () => void;
  onResetDefault: () => void;
  saving: boolean;
  pipeline: PipelineView;
}

const fmtTokens = (n: number) => (n >= 1_000_000 ? `${n / 1_000_000}M` : n >= 1000 ? `${Math.round(n / 1000)}K` : `${n}`);

export default function RunSettingsPanel(props: Props) {
  const { catalog, value, onChange, onSaveDefault, onResetDefault, saving, pipeline } = props;
  if (!catalog || !value) {
    return (
      <Box padding="l">
        <StatusIndicator type="loading">Loading models</StatusIndicator>
      </Box>
    );
  }
  const s = value;
  const model = findModel(catalog, s);
  const set = (patch: Partial<RunSettings>) => onChange({ ...s, ...patch });
  const jj = catalog.router_name;
  const auto = s.routing === "auto"; // the router manages model, level, max tokens and sampling

  const options: SelectProps.OptionGroup[] = catalog.providers
    .map((p) => ({
      label: p.label,
      options: catalog.models
        .filter((m) => m.provider === p.name)
        .map((m) => ({
          value: m.key,
          label: m.label,
          description: m.available ? m.description || m.id : `Set ${p.api_key_env} to enable`,
          tags: [m.id, ...(m.context_window ? [`${fmtTokens(m.context_window)} ctx`] : [])],
          disabled: !m.available,
        })),
    }))
    .filter((g) => g.options.length > 0);
  const selected = options.flatMap((g) => g.options).find((o) => o.value === model?.key) ?? null;

  return (
    <div className="run-settings">
      <Box padding={{ horizontal: "l", top: "m", bottom: "l" }}>
        <SpaceBetween size="l">
          <Header variant="h2" description={auto ? `Live view of what ${jj} and the gateway do.` : "Applied to the next message you send."}>
            {auto ? "Request pipeline" : "Run settings"}
          </Header>

          <FormField
            label="Routing"
            description={`${jj} picks the model, thinking level, max output tokens and sampling for each prompt, while Manual you define yourself.`}
          >
            <SegmentedControl
              selectedId={s.routing}
              onChange={({ detail }) => set({ routing: detail.selectedId as RunSettings["routing"] })}
              options={[
                { id: "auto", text: `Auto by ${jj}`, iconName: "gen-ai" },
                { id: "manual", text: "Manual" },
              ]}
            />
          </FormField>

          {auto ? (
            <PipelinePanel view={pipeline} routerName={jj} />
          ) : (
            <>

            <FormField label="Model" description={`Also the fallback when Auto is on and ${jj} can't decide.`}>
              <Select
                selectedOption={selected}
                options={options}
                filteringType="auto"
                  onChange={({ detail }) => {
                  const m = catalog.models.find((x) => x.key === detail.selectedOption.value);
                  if (m) onChange(applyModel(s, m));
                }}
                triggerVariant="option"
              />
            </FormField>

            {model && model.levels.length > 0 && (
              <FormField label="Thinking level" description="How much the model reasons before answering.">
                <SegmentedControl
                  selectedId={s.level ?? model.default_level}
                  onChange={({ detail }) => set({ level: detail.selectedId })}
                  options={model.levels.map((l) => ({ id: l, text: l }))}
                />
              </FormField>
            )}

            {model && model.levels.length > 0 && (
              <Toggle checked={s.show_thinking} onChange={({ detail }) => set({ show_thinking: detail.checked })}>
                Show thoughts
              </Toggle>
            )}

            <FormField
              label="Max output tokens"
              constraintText={model ? `Up to ${model.max_output_tokens.toLocaleString()}. Empty uses ${model.default_max_tokens.toLocaleString()}.` : undefined}
            >
              <Input
                  type="number"
                inputMode="numeric"
                value={s.max_tokens?.toString() ?? ""}
                placeholder={model?.default_max_tokens.toString()}
                onChange={({ detail }) => {
                  const n = parseInt(detail.value, 10);
                  set({ max_tokens: Number.isFinite(n) && n > 0 ? Math.min(n, model?.max_output_tokens ?? n) : null });
                }}
              />
            </FormField>

            <FormField label="Cache" description={catalog.cache_enabled ? "Answer repeated requests from the KV cache." : "No cache plugin is configured."}>
              <Toggle
                checked={s.use_cache && catalog.cache_enabled}
                disabled={!catalog.cache_enabled}
                onChange={({ detail }) => set({ use_cache: detail.checked })}
              >
                Use cache
              </Toggle>
            </FormField>

            <FormField label="System instructions" stretch>
              <Textarea
                value={s.system_prompt}
                rows={4}
                placeholder="Optional tone and style instructions for the model"
                onChange={({ detail }) => set({ system_prompt: detail.value })}
              />
            </FormField>

            <ExpandableSection headerText="Advanced sampling" variant="footer" defaultExpanded={false}>
              {model && !model.sampling ? (
                <Box color="text-status-inactive" fontSize="body-s">
                  {model.label} doesn't accept temperature or top P.
                </Box>
              ) : (
                <SpaceBetween size="m">
                  <SamplingField
                          label="Temperature"
                    value={s.temperature}
                    min={0}
                    max={2}
                    step={0.05}
                    onChange={(v) => set({ temperature: v })}
                  />
                  <SamplingField label="Top P" value={s.top_p} min={0} max={1} step={0.01} onChange={(v) => set({ top_p: v })} />
                </SpaceBetween>
              )}
            </ExpandableSection>

            <ColumnLayout columns={2}>
              <Button onClick={onSaveDefault} loading={saving} fullWidth>
                Save as default
              </Button>
              <Button variant="normal" onClick={onResetDefault} fullWidth>
                Reset
              </Button>
            </ColumnLayout>
            </>
          )}
        </SpaceBetween>
      </Box>
    </div>
  );
}

function SamplingField(props: {
  label: string;
  value: number | null;
  min: number;
  max: number;
  step: number;
  disabled?: boolean;
  onChange: (v: number | null) => void;
}) {
  const usingDefault = props.value === null;
  const shown = props.value ?? 1;
  return (
    <FormField
      label={props.label}
      secondaryControl={
        <Checkbox checked={usingDefault} disabled={props.disabled} onChange={({ detail }) => props.onChange(detail.checked ? null : shown)}>
          Model default
        </Checkbox>
      }
    >
      <div className="slider-row">
        <div className="slider-row__slider">
          <Slider
            value={shown}
            min={props.min}
            max={props.max}
            step={props.step}
            disabled={usingDefault || props.disabled}
            ariaLabel={props.label}
            onChange={({ detail }) => props.onChange(detail.value)}
          />
        </div>
        <Box variant="code" color={usingDefault ? "text-status-inactive" : undefined}>
          {usingDefault ? "auto" : shown.toFixed(2)}
        </Box>
      </div>
    </FormField>
  );
}
