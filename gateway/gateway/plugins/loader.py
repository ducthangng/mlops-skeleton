import importlib
import inspect
from typing import Any


def load_plugin(path: str) -> Any:
    """Import "package.module:Name" and return an instance.

    Name may be a class or a zero-arg factory function.
    """
    module_name, _, attr = path.partition(":")
    if not attr:
        raise ValueError(f"plugin path must look like 'package.module:Name', got {path!r}")
    obj = getattr(importlib.import_module(module_name), attr)
    return obj() if callable(obj) else obj


async def call_hook(plugin: Any, name: str) -> None:
    hook = getattr(plugin, name, None)
    if hook is None:
        return
    result = hook()
    if inspect.isawaitable(result):
        await result
