"""Parity test: kentro.types and kentro_server.api.types must stay structurally identical.

Per the handoff (§1.2), the SDK and server keep manually duplicated Pydantic v2 type
definitions. This test catches accidental drift on every CI run. Intentional divergence
requires updating both this test (to allow the specific difference) and the
`.claude/skills/sync-types/` skill in the same change.
"""

import inspect
from enum import Enum, EnumMeta
from typing import cast

from kentro import types as sdk_types
from kentro_server.api import types as server_types
from pydantic import BaseModel

# Symbols that are type aliases (Annotated unions), not classes — Python won't introspect
# them via `vars()` so we list them explicitly and check they exist on both sides.
TYPE_ALIASES = {"ResolverSpec", "Rule"}


def _public_symbols(mod: object) -> dict[str, object]:
    return {n: getattr(mod, n) for n in getattr(mod, "__all__", [])}


def _model_classes(syms: dict[str, object]) -> dict[str, type[BaseModel]]:
    return {n: v for n, v in syms.items() if inspect.isclass(v) and issubclass(v, BaseModel)}


def _enum_classes(syms: dict[str, object]) -> dict[str, type[Enum]]:
    """Returns enum *classes* (typed as `type[Enum]`, not `EnumMeta`) so iteration
    over `__members__` yields properly-typed `Enum` instances rather than `object`.
    Runtime check is `isinstance(v, EnumMeta)` (which matches enum *classes*); the
    explicit `cast` tells the type checker the same thing."""
    out: dict[str, type[Enum]] = {}
    for n, v in syms.items():
        if isinstance(v, EnumMeta):
            out[n] = cast(type[Enum], v)
    return out


def test_public_symbols_match() -> None:
    sdk = set(_public_symbols(sdk_types))
    srv = set(_public_symbols(server_types))
    if sdk != srv:
        only_sdk = sdk - srv
        only_srv = srv - sdk
        raise AssertionError(
            f"Public-symbol drift.\n  Only in SDK: {sorted(only_sdk)}\n"
            f"  Only in server: {sorted(only_srv)}"
        )


def test_type_aliases_exist_on_both_sides() -> None:
    for name in TYPE_ALIASES:
        if not hasattr(sdk_types, name):
            raise AssertionError(f"{name} missing from kentro.types")
        if not hasattr(server_types, name):
            raise AssertionError(f"{name} missing from kentro_server.api.types")


def test_enum_members_match() -> None:
    sdk_enums = _enum_classes(_public_symbols(sdk_types))
    srv_enums = _enum_classes(_public_symbols(server_types))
    for name, sdk_enum in sdk_enums.items():
        srv_enum = srv_enums.get(name)
        if srv_enum is None:
            raise AssertionError(f"Enum {name} only on SDK side")
        sdk_members = {n: m.value for n, m in sdk_enum.__members__.items()}
        srv_members = {n: m.value for n, m in srv_enum.__members__.items()}
        if sdk_members != srv_members:
            raise AssertionError(
                f"Enum {name} drift.\n  SDK: {sdk_members}\n  Server: {srv_members}"
            )


_MODULE_PREFIXES = ("kentro.types.", "kentro_server.api.types.")


def _normalize_annotation(text: str) -> str:
    """Strip the SDK and server module prefixes so identical types compare equal."""
    out = text
    for prefix in _MODULE_PREFIXES:
        out = out.replace(prefix, "")
    return out


def _field_signature(model: type[BaseModel]) -> dict[str, tuple[str, object]]:
    """Capture each field as (annotation_repr, default_value) for cross-model comparison."""
    out: dict[str, tuple[str, object]] = {}
    for name, info in model.model_fields.items():
        ann = info.annotation
        ann_repr = _normalize_annotation(repr(ann)) if ann is not None else "None"
        default = info.default
        out[name] = (ann_repr, default)
    return out


def test_model_field_shapes_match() -> None:
    sdk_models = _model_classes(_public_symbols(sdk_types))
    srv_models = _model_classes(_public_symbols(server_types))
    drift: list[str] = []
    for name, sdk_model in sdk_models.items():
        srv_model = srv_models.get(name)
        if srv_model is None:
            drift.append(f"  Model {name} missing from server")
            continue
        sdk_sig = _field_signature(sdk_model)
        srv_sig = _field_signature(srv_model)
        if sdk_sig != srv_sig:
            drift.append(f"  Model {name} drift:\n    SDK:    {sdk_sig}\n    Server: {srv_sig}")
    if drift:
        raise AssertionError("Model field drift detected:\n" + "\n".join(drift))
