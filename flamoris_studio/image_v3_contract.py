"""Narrow presentation adapter for Generation's reviewed Image v3 profile.

Generation owns compilation, revocation and qualification. This module never
reads a graph, registers a workflow or fabricates a readiness receipt.
"""

import json
import math
import re

from .workflow_contract import MAX_SEED, normalize_descriptor

PROFILE = {"id": "image-generate-v1", "revision": 1}
DOMAIN = {"id": "image-v1-bounded-scalars", "revision": 1}
BOUNDS = {
    "width": ("integer", 64, 4096),
    "height": ("integer", 64, 4096),
    "seed": ("integer", 0, MAX_SEED),
    "steps": ("integer", 1, 150),
    "cfg": ("number", 0, 100),
    "denoise": ("number", 0, 1),
}


def normalize_v3_descriptor(raw):
    if not isinstance(raw, dict) or (
        raw.get("descriptor_revision") != 3
        or raw.get("capability_id") != "image.generate"
        or raw.get("profile") != PROFILE
        or type(raw["profile"].get("revision")) is not int
        or raw.get("execution_kind") not in {"provider", "composition"}
        or not isinstance(raw.get("id"), str)
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", raw["id"])
        or type(raw.get("version")) is not int
        or not 1 <= raw["version"] <= 100000
        or not isinstance(raw.get("digest"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", raw["digest"])
        or not isinstance(raw.get("name"), str)
        or not 1 <= len(raw["name"]) <= 120
    ):
        raise ValueError("unsupported_parameter")
    outputs = raw.get("outputs")
    if not isinstance(outputs, dict) or len(outputs) != 1:
        raise ValueError("unsupported_parameter")
    port, output = next(iter(outputs.items()))
    if not isinstance(port, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", port):
        raise ValueError("unsupported_parameter")
    if not isinstance(output, dict) or (
        any(
            type(output.get(k)) is not int
            for k in ("schema_revision", "min_count", "max_count")
        )
        or any(
            output.get(k) != v
            for k, v in {
                "type": "asset",
                "schema_id": "image-v1",
                "schema_revision": 1,
                "role": "primary_image",
                "media_kind": "image",
                "min_count": 1,
                "max_count": 1,
            }.items()
        )
        or not isinstance(output.get("mime_types"), list)
        or not 1 <= len(output["mime_types"]) <= 3
        or any(
            m not in {"image/png", "image/jpeg", "image/webp"}
            for m in output["mime_types"]
        )
        or len(set(output["mime_types"])) != len(output["mime_types"])
        or type(output.get("max_bytes")) is not int
        or not 0 < output["max_bytes"] <= 64 * 1024**2
    ):
        raise ValueError("unsupported_parameter")
    source = raw.get("inputs")
    if not isinstance(source, dict) or not 1 <= len(source) <= 12:
        raise ValueError("unsupported_parameter")
    parameters = {}
    for key, value in source.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key):
            raise ValueError("unsupported_parameter")
        if not isinstance(value, dict) or not isinstance(value.get("role"), str):
            raise TypeError("unsupported_parameter")
        role = value["role"]
        kind = value.get("type")
        low, high = value.get("minimum"), value.get("maximum")
        if role in BOUNDS:
            expected, minimum, maximum = BOUNDS[role]
            if (
                kind != expected
                or any(
                    type(v) not in (int, float) or not math.isfinite(v)
                    for v in (low, high)
                )
                or not minimum <= low <= high <= maximum
            ):
                raise ValueError("unsupported_parameter")
        elif (
            role not in {"checkpoint", "positive_prompt", "negative_prompt"}
            or kind != "string"
        ):
            raise ValueError("unsupported_parameter")
        if (
            value.get("schema_id") != "image-" + role.replace("_", "-")
            or type(value.get("schema_revision")) is not int
            or value["schema_revision"] != 1
            or type(value.get("min_count")) is not int
            or value["min_count"] not in (0, 1)
            or type(value.get("max_count")) is not int
            or value["max_count"] != 1
            or type(value.get("max_bytes")) is not int
            or not 0 < value["max_bytes"] <= 20000
        ):
            raise ValueError("unsupported_parameter")
        required = value["min_count"] == 1
        if (not required and value.get("default") is None) or (
            role in {"checkpoint", "positive_prompt"} and not required
        ):
            raise ValueError("unsupported_parameter")
        spec = {
            "type": kind,
            "role": role,
            "required": required,
            "max_bytes": value["max_bytes"],
        }
        if kind == "string":
            spec.update(max_length=value["max_bytes"])
            if role in {"checkpoint", "positive_prompt"}:
                spec["min_length"] = 1
        else:
            spec.update(minimum=low, maximum=high)
        if role in {"width", "height"}:
            spec["multiple_of"] = 8
        if role == "checkpoint":
            spec["model_kind"] = "checkpoint"
        if "default" in value:
            if required:
                raise ValueError("unsupported_parameter")
            spec["default"] = value["default"]
        parameters[key] = spec
    readiness = raw.get("readiness")
    if not isinstance(readiness, dict):
        raise TypeError("unsupported_parameter")
    qualified = raw.get("qualified_models")
    ready = readiness.get("state") == "ready" and raw.get("qualified_domain") == DOMAIN
    ready = ready and type(raw["qualified_domain"].get("revision")) is int
    ready = ready and isinstance(qualified, list) and len(qualified) == 1
    ready = (
        ready
        and isinstance(qualified[0], str)
        and qualified[0].startswith("checkpoint:")
    )
    ready = ready and 0 < len(qualified[0].removeprefix("checkpoint:")) <= 1024
    if ready:
        for spec in parameters.values():
            if spec["role"] == "checkpoint":
                spec["enum"] = [qualified[0].removeprefix("checkpoint:")]
    # Fixed dimensions are absent from the graph-free v3 catalog. The existing
    # Image editor supports editable width/height only; never guess a fixed size.
    converted = normalize_descriptor(
        {
            "id": "v3:" + raw["id"],
            "kind": "definition",
            "name": raw.get("name", raw["id"]),
            "metadata_schema_version": 2,
            "definition_version": raw["version"],
            "definition_digest": raw["digest"],
            "parameters": parameters,
            "image": {
                "profile": "image-v1",
                "mode": "txt2img",
                "dimensions": {"mode": "parameters"},
            },
            "readiness": {
                "state": "ready" if ready else "validated",
                "definition_version": raw["version"],
                "definition_digest": raw["digest"],
            },
        }
    )
    converted["kind"] = "v3"
    return converted


def normalize_v3_catalog(raw):
    if not isinstance(raw, dict) or raw.get("descriptor_revision") != 3:
        raise ValueError("unsupported_parameter")
    entries = raw.get("descriptors")
    if not isinstance(entries, list) or len(entries) > 128:
        raise ValueError("unsupported_parameter")
    result = []
    for item in entries:
        try:
            result.append(normalize_v3_descriptor(item))
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    if len({item["id"] for item in result}) != len(result):
        raise ValueError("unsupported_parameter")
    if (
        len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode())
        > 256 * 1024
    ):
        raise ValueError("unsupported_parameter")
    return result
