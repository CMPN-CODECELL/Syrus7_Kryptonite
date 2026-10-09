"""Dump JSON Schemas for every pydantic model in backend/quantfolio/contracts.py.

Used by scripts/gen-contract-types.mjs so the frontend's TypeScript types are
derived from the backend contract instead of being maintained by hand.
Output (stdout): {"output": {Name: schema}, "input": {Name: schema}, "defs": {...}}.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from pydantic import BaseModel  # noqa: E402
from pydantic.json_schema import models_json_schema  # noqa: E402

from quantfolio import contracts  # noqa: E402

models = [
    obj
    for name, obj in vars(contracts).items()
    if inspect.isclass(obj)
    and issubclass(obj, BaseModel)
    and obj.__module__ == contracts.__name__
    and not name.startswith("_")
]
literals = {
    name: list(obj.__args__)
    for name, obj in vars(contracts).items()
    if getattr(obj, "__origin__", None) is not None and str(getattr(obj, "__origin__", "")) == "typing.Literal"
}

out = {}
for mode in ("serialization", "validation"):
    _, schema = models_json_schema([(m, mode) for m in models], ref_template="#/$defs/{model}")
    out[mode] = schema.get("$defs", {})
out["literals"] = literals
out["constants"] = {"SCHEMA_VERSION": contracts.SCHEMA_VERSION, "DISCLAIMER": contracts.DISCLAIMER}
json.dump(out, sys.stdout, indent=1, sort_keys=True, default=str)
