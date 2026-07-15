from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from typing import Any, Callable


@dataclass
class ToolResult:
    ok: bool
    output: str
    data: Any = None


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    func: Callable[..., ToolResult]
    needs_confirmation: bool = False
    capabilities: list[str] = field(default_factory=list)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self, names: list[str] | tuple[str, ...] | None = None) -> str:
        lines = []
        selected = self.names() if names is None else [name for name in names if name in self._tools]
        for name in selected:
            t = self._tools[name]
            capabilities = f"; capabilities={t.capabilities}" if t.capabilities else ""
            lines.append(f"- {name}: {t.description}; schema={t.schema}{capabilities}")
        return "\n".join(lines)

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self.get(name)
        return tool.func(**args)

    def openai_tools(self, names: list[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        selected = self.names() if names is None else [name for name in names if name in self._tools]
        definitions: list[dict[str, Any]] = []
        for name in selected:
            tool = self._tools[name]
            signature = inspect.signature(tool.func)
            properties = {key: _json_schema_for(value) for key, value in tool.schema.items()}
            required = [
                key
                for key in properties
                if key in signature.parameters and signature.parameters[key].default is inspect.Parameter.empty
            ]
            parameters: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
            if required:
                parameters["required"] = required
            definitions.append(
                {
                    "type": "function",
                    "function": {"name": name, "description": tool.description, "parameters": parameters},
                }
            )
        return definitions


def _json_schema_for(value: Any) -> dict[str, Any]:
    text = str(value).strip().lower()
    if text.startswith("list[") or text in {"list", "array"}:
        inner = text[5:-1] if text.startswith("list[") else "str"
        return {"type": "array", "items": _json_schema_for(inner)}
    if text in {"dict", "object", "dict[str,any]", "dict[str, any]"}:
        return {"type": "object", "additionalProperties": True}
    mapping = {"str": "string", "string": "string", "int": "integer", "integer": "integer", "float": "number", "number": "number", "bool": "boolean", "boolean": "boolean"}
    return {"type": mapping.get(text, "string")}
