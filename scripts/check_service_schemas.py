"""Every field `services.yaml` advertises must be accepted by its schema.

A documented field missing from the schema is the worst of the three
ways a service field can be broken: Home Assistant renders it in the UI,
the user fills it in, and validation refuses the call BEFORE any of our
code runs -- with a message saying the field is invalid, on a field we
documented ourselves.

IT HAS HAPPENED THREE TIMES.

  - `two_pass` on `clean_room`: read by the handler, documented in
    services.yaml, absent from the schema. Recorded in services.py as a
    fixed bug, and nobody checked the neighbouring fields afterwards.
  - `clean_zone` was then missing THREE at once -- `cleaning_mode`,
    `smart_scrub`, `pad_wetness` -- all documented, all implemented,
    all refused. @theChef613 hit `cleaning_mode` and reported the
    detail that makes it obvious: the same field worked on `clean_room`.
  - and the two services had drifted into offering different options
    entirely, which was the same omission seen from further away.

READ AS A SYNTAX TREE, NOT AS TEXT. A first attempt matched the
registrations with regular expressions and reported fifteen findings, of
which most were its own blind spots -- a guard that cries wolf gets
ignored, which is worse than no guard. The AST gives the registration,
its schema, and the schema's keys exactly.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import yaml

COMPONENT = Path("custom_components/roomba_plus")
SERVICES_YAML = COMPONENT / "services.yaml"

#: EVERY MODULE THAT REGISTERS, not just the obvious one. The reset
#: services and the schedule services live elsewhere, and a check that
#: reads only `services.py` reports eleven of them as unregistered --
#: which is the check being blind, not the code being broken.
REGISTERING_MODULES = [
    COMPONENT / "services.py",
    COMPONENT / "prime_schedule_services.py",
    COMPONENT / "__init__.py",
]

#: Documented for the user but deliberately not in the schema, with the
#: reason. Empty is the goal; an entry here is a decision, not a bug.
ACCEPTED: dict[tuple[str, str], str] = {}


def _string_constants(paths: list[Path]) -> dict[str, str]:
    """{CONSTANT_NAME: "its string value"} across the given modules.

    Both the service names and the field names are written as constants
    (`SERVICE_CLEAN_ROOM`, `ATTR_CLEANING_MODE`), so nothing can be
    compared until they are resolved.
    """
    out: dict[str, str] = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            target = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
            elif isinstance(node, ast.AnnAssign):
                target = node.target
            if not isinstance(target, ast.Name) or node.value is None:
                continue
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                out[target.id] = node.value.value
    return out


def _named_schemas(tree: ast.Module) -> dict[str, ast.expr]:
    """{NAME: its value} for module-level schema variables.

    Three services share `_RESET_SCHEMA` and one uses
    `_CLEAN_SEQUENCE_SCHEMA`; without following those, they look like
    services whose schema accepts nothing at all.
    """
    # ANYWHERE, not just at module level. `_RESET_SCHEMA` is assigned
    # inside the registration function, so a module-level scan finds
    # nothing and reports the three services that share it as having an
    # unreadable schema.
    out: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and node.value is not None:
                out[target.id] = node.value
    return out


def _schema_field_names(
    node: ast.expr, constants: dict[str, str], named: dict[str, ast.expr]
) -> set[str] | None:
    """Field names a schema expression accepts, or None if unreadable.

    None matters: it means "this guard cannot tell", which must not be
    reported as "the schema rejects everything".
    """
    if isinstance(node, ast.Name):
        target = named.get(node.id)
        return (
            _schema_field_names(target, constants, named)
            if target is not None else None
        )

    if not isinstance(node, ast.Call):
        return None

    # vol.Schema({...}) / vol.All(vol.Schema({...}), ...) / cv.… wrappers
    for arg in node.args:
        if isinstance(arg, ast.Dict):
            return _dict_keys(arg, constants)
        nested = _schema_field_names(arg, constants, named)
        if nested is not None:
            return nested
    return None


def _dict_keys(node: ast.Dict, constants: dict[str, str]) -> set[str]:
    """The field names of a schema dict, resolving marker calls."""
    out: set[str] = set()
    for key in node.keys:
        if key is None:
            continue
        # vol.Optional("x") / vol.Required(ATTR_X)
        if isinstance(key, ast.Call) and key.args:
            key = key.args[0]
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            out.add(key.value)
        elif isinstance(key, ast.Name):
            resolved = constants.get(key.id)
            if resolved is not None:
                out.add(resolved)
    return out


def _registrations(
    tree: ast.Module, constants: dict[str, str], named: dict[str, ast.expr]
) -> dict[str, set[str] | None]:
    """{service name: accepted field names, or None when unreadable}."""
    out: dict[str, set[str] | None] = {}
    # REGISTERED IN A LOOP OVER TUPLES. The schedule services are
    # written as `for name, handler, schema in ((SERVICE_X, h, S), ...)`,
    # so the register call names a loop variable and nothing about the
    # Call node says which service it is. Reading the tuples gives the
    # pairing directly.
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.iter, ast.Tuple):
            continue
        if not (
            isinstance(node.target, ast.Tuple)
            and len(node.target.elts) >= 2
        ):
            continue
        for entry in node.iter.elts:
            if not isinstance(entry, ast.Tuple) or len(entry.elts) < 2:
                continue
            first, last = entry.elts[0], entry.elts[-1]
            name = (
                first.value if isinstance(first, ast.Constant)
                else constants.get(getattr(first, "id", ""))
            )
            if isinstance(name, str):
                out[name] = _schema_field_names(last, constants, named)

    for node in ast.walk(tree):
        # `hass.services.async_register(...)`, or since 4.2.11 the guarded
        # wrapper `register_guarded(hass, ...)` from service_guard.py, which
        # every Roomba+ service now goes through (quality scale:
        # action-setup). Missing the second form made every service look
        # unregistered here.
        is_register = isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "async_register")
            or (isinstance(node.func, ast.Name) and node.func.id == "register_guarded")
        )
        if not is_register:
            continue

        # (hass, DOMAIN, service, handler, ...) or (DOMAIN, service, …)
        service: str | None = None
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                candidate: str | None = arg.value
            elif isinstance(arg, ast.Name):
                candidate = constants.get(arg.id)
            else:
                continue
            if candidate and candidate != "roomba_plus":
                service = candidate
                break
        if service is None:
            continue

        schema = next(
            (kw.value for kw in node.keywords if kw.arg == "schema"), None
        )
        out[service] = (
            _schema_field_names(schema, constants, named)
            if schema is not None else None
        )
    return out


def main() -> int:
    constants = _string_constants(
        [COMPONENT / "const.py", *REGISTERING_MODULES]
    )
    accepted: dict[str, set[str] | None] = {}
    for module in REGISTERING_MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        found = _registrations(tree, constants, _named_schemas(tree))
        # A service registered in two places keeps the readable schema
        # rather than whichever module was parsed last.
        for service, fields in found.items():
            if accepted.get(service) is None:
                accepted[service] = fields
    documented: dict[str, Any] = (
        yaml.safe_load(SERVICES_YAML.read_text(encoding="utf-8")) or {}
    )

    findings: list[str] = []
    unreadable: list[str] = []
    checked = 0

    for service, spec in documented.items():
        fields = list((spec or {}).get("fields") or {})
        if service not in accepted:
            unreadable.append(f"  {service}: no registration found")
            continue
        schema_fields = accepted[service]
        if schema_fields is None:
            unreadable.append(f"  {service}: schema could not be read")
            continue
        for field in fields:
            checked += 1
            if field in schema_fields or (service, field) in ACCEPTED:
                continue
            findings.append(
                f"  {service}.{field} is offered by the UI and refused "
                f"by the schema"
            )

    if unreadable:
        print(f"\n{len(unreadable)} service(s) this check could not read:\n")
        print("\n".join(unreadable))
        print(
            "\nThat is a gap in this script, not necessarily in the code -- "
            "but an unread schema is an unchecked one.\n"
        )

    if findings:
        print(f"\n{len(findings)} field(s) documented but not accepted:\n")
        print("\n".join(findings))
        print(
            "\nHome Assistant validates before any handler runs, so the user "
            "sees\n'not a valid option' on a field we documented ourselves. "
            "Add it to\nthe schema, or remove it from services.yaml.\n"
        )

    if findings or unreadable:
        return 1

    print(
        f"OK: {checked} documented field(s) across {len(documented)} "
        f"service(s), all accepted by their schemas."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
