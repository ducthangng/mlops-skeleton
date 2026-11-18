"""Step tracker for one chat request. The UI renders these steps as a vertical stepper.

Each step has a stable id, and its status uses the UI's status-indicator names:
pending | in-progress | success | warning | error | stopped (skipped).
"""

from typing import Any

STEP_ORDER = [
    ("cache", "Check cache"),
    ("router", "Ask {router}"),
    ("model", "Model"),
    ("level", "Thinking level"),
    ("max_tokens", "Max output tokens"),
    ("sampling", "Temperature · Top P"),
    ("generate", "Generate answer"),
    ("save", "Save"),
]


class Pipeline:
    def __init__(self, router_name: str):
        self._steps: dict[str, dict[str, Any]] = {
            sid: {"id": sid, "label": label.format(router=router_name), "status": "pending"}
            for sid, label in STEP_ORDER
        }

    def update(self, sid: str, **fields: Any) -> dict:
        """Merge fields into a step and return the SSE event for it."""
        step = self._steps[sid]
        step.update({k: v for k, v in fields.items() if v is not None})
        return {"event": "step", "data": dict(step)}

    def snapshot(self) -> list[dict]:
        return [dict(s) for s in self._steps.values()]
