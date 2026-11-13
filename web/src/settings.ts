import type { Catalog, ModelInfo, RunSettings } from "./api";

export function findModel(catalog: Catalog | null, s: Pick<RunSettings, "provider" | "model">): ModelInfo | undefined {
  return catalog?.models.find((m) => m.provider === s.provider && m.id === s.model);
}

/** Fit settings to a newly selected model (mirrors gateway/service.py fit_params). */
export function applyModel(s: RunSettings, m: ModelInfo): RunSettings {
  return {
    ...s,
    provider: m.provider,
    model: m.id,
    level: m.levels.length === 0 ? null : s.level && m.levels.includes(s.level) ? s.level : m.default_level,
    temperature: m.sampling ? s.temperature : null,
    top_p: m.sampling ? s.top_p : null,
    max_tokens: s.max_tokens ? Math.min(s.max_tokens, m.max_output_tokens) : null,
  };
}

/** Restore a conversation's saved settings on top of the defaults, if its model still exists. */
export function mergeSettings(defaults: RunSettings, saved: Partial<RunSettings>, catalog: Catalog | null): RunSettings {
  const merged = { ...defaults, ...saved } as RunSettings;
  const m = findModel(catalog, merged);
  return m && m.available ? applyModel(merged, m) : defaults;
}
