from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple


REFERENCE_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


class BindingError(ValueError):
    pass


def lookup_reference(reference: str, values: Dict[str, Any]) -> Any:
    parts = [part for part in str(reference or "").split(".") if part]
    if not parts or parts[0] not in {"input", "run", "nodes"}:
        raise BindingError(f"Invalid reference root: {reference}")
    current: Any = values
    traversed: List[str] = []
    for part in parts:
        traversed.append(part)
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
            continue
        raise BindingError(f"Missing reference: {'.'.join(traversed)}")
    return current


def resolve_bindings(value: Any, values: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return lookup_reference(str(value["$ref"]), values)
        return {key: resolve_bindings(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_bindings(item, values) for item in value]
    if not isinstance(value, str) or "{{" not in value:
        return value
    matches = list(REFERENCE_RE.finditer(value))
    if len(matches) == 1 and matches[0].span() == (0, len(value)):
        return lookup_reference(matches[0].group(1), values)
    output = value
    for match in reversed(matches):
        resolved = lookup_reference(match.group(1), values)
        replacement = "" if resolved is None else str(resolved)
        output = output[:match.start()] + replacement + output[match.end():]
    if "{{" in output or "}}" in output:
        raise BindingError("Malformed template expression")
    return output


def iter_references(value: Any, path: str = "") -> Iterable[Tuple[str, str]]:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            yield path or "$", str(value["$ref"])
            return
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            yield from iter_references(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_references(item, f"{path}[{index}]")
    elif isinstance(value, str):
        for match in REFERENCE_RE.finditer(value):
            yield path or "$", match.group(1)
