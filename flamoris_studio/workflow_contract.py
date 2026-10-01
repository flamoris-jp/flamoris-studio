"""Graph-free Image descriptors, role mapping and exact integer seed domains."""

import math
import secrets

MAX_SEED = 2**53 - 1
ROLES = {
    "checkpoint": "checkpoint",
    "positive_prompt": "positivePrompt",
    "negative_prompt": "negativePrompt",
    "width": "width",
    "height": "height",
    "steps": "steps",
    "cfg": "cfg",
    "seed": "seed",
    "sampler": "sampler",
    "scheduler": "scheduler",
    "denoise": "denoise",
    "loras": "loras",
    "initial_image": "referenceInputId",
}
SPEC_FIELDS = {
    "type",
    "role",
    "required",
    "default",
    "minimum",
    "maximum",
    "enum",
    "multiple_of",
    "min_length",
    "max_length",
    "model_kind",
    "pattern",
    "media_types",
    "max_items",
    "min_items",
    "items",
}


def validate_value(spec, value):
    kind = spec["type"]
    if kind == "integer":
        if type(value) is not int or abs(value) > MAX_SEED:
            raise ValueError("unsupported_parameter")
    elif kind == "number":
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("unsupported_parameter")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError("unsupported_parameter")
    elif kind == "string":
        if not isinstance(value, str) or not spec.get("min_length", 0) <= len(
            value
        ) <= spec.get("max_length", 20000):
            raise ValueError("unsupported_parameter")
    elif kind == "ordered_loras":
        if not isinstance(value, list) or not spec.get("min_items", 0) <= len(
            value
        ) <= spec.get("max_items", 16):
            raise ValueError("unsupported_parameter")
        for item in value:
            if not isinstance(item, dict) or set(item) != {
                "name",
                "strength_model",
                "strength_clip",
            }:
                raise ValueError("unsupported_parameter")
            if (
                not isinstance(item["name"], str)
                or not item["name"]
                or len(item["name"]) > 1024
            ):
                raise ValueError("unsupported_parameter")
            for key in ("strength_model", "strength_clip"):
                if (
                    type(item[key]) not in (int, float)
                    or not math.isfinite(item[key])
                    or not -20 <= item[key] <= 20
                ):
                    raise ValueError("unsupported_parameter")
    elif kind != "managed_input":
        raise ValueError("unsupported_parameter")
    if kind in {"integer", "number"}:
        if value < spec.get("minimum", -math.inf) or value > spec.get(
            "maximum", math.inf
        ):
            raise ValueError("unsupported_parameter")
    divisor = spec.get("multiple_of")
    if divisor is not None:
        if (
            kind != "integer"
            or type(divisor) is not int
            or divisor <= 0
            or value % divisor
        ):
            raise ValueError("unsupported_parameter")
    if "enum" in spec and not any(
        type(value) is type(x) and value == x for x in spec["enum"]
    ):
        raise ValueError("unsupported_parameter")
    return value


def seed_domain(spec):
    if spec.get("type") != "integer":
        raise ValueError("unsupported_parameter")
    divisor = spec.get("multiple_of", 1)
    if type(divisor) is not int or divisor <= 0:
        raise ValueError("unsupported_parameter")
    lower = max(0, math.ceil(spec.get("minimum", 0)))
    upper = min(MAX_SEED, math.floor(spec.get("maximum", MAX_SEED)))
    if "enum" in spec:
        values = [
            x
            for x in spec["enum"]
            if type(x) is int and lower <= x <= upper and x % divisor == 0
        ]
        if not values:
            raise ValueError("unsupported_parameter")
        return values
    first = ((lower + divisor - 1) // divisor) * divisor
    count = (upper - first) // divisor + 1
    if count < 1:
        raise ValueError("unsupported_parameter")
    return first, divisor, count


def random_seed(spec):
    domain = seed_domain(spec)
    if isinstance(domain, list):
        return domain[secrets.randbelow(len(domain))]
    first, step, count = domain
    return first + step * secrets.randbelow(count)


def normalize_descriptor(raw):
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("id"), str)
        or len(raw["id"]) > 128
    ):
        raise ValueError("unsupported_parameter")
    kind = raw.get("kind")
    image = raw.get("image", {})
    readiness = raw.get("readiness", {})
    supported = (
        raw.get("metadata_schema_version") == 2 and image.get("profile") == "image-v1"
    )
    parameters = {}
    roles = set()
    source = raw.get("parameters", {})
    if not isinstance(source, dict) or len(source) > 64:
        raise ValueError("unsupported_parameter")
    for key, value in source.items():
        if not isinstance(key, str) or len(key) > 128 or not isinstance(value, dict):
            raise ValueError("unsupported_parameter")
        spec = {k: v for k, v in value.items() if k in SPEC_FIELDS}
        role = spec.get("role")
        if role is not None:
            if role not in ROLES or role in roles:
                supported = False
            roles.add(role)
        if spec.get("type") == "managed_input" and role != "initial_image":
            supported = False
        if role == "seed":
            seed_domain(spec)
        if "multiple_of" in spec and (
            spec.get("type") != "integer"
            or type(spec["multiple_of"]) is not int
            or spec["multiple_of"] <= 0
        ):
            raise ValueError("unsupported_parameter")
        parameters[key] = spec
    if supported:
        required = {"checkpoint", "positive_prompt"}
        if image.get("dimensions", {}).get("mode") == "parameters":
            required |= {"width", "height"}
        if image.get("mode") == "img2img":
            required |= {"initial_image", "denoise"}
            supported = (
                image.get("reference_semantics") == "initial_image"
                and image.get("resize_policy") == "center-crop-resize"
            )
        supported = supported and required <= roles
    ready = readiness.get("state") == "ready"
    if kind == "definition":
        ready = (
            ready
            and type(raw.get("definition_version")) is int
            and isinstance(raw.get("definition_digest"), str)
        )
        ready = (
            ready
            and readiness.get("definition_version") == raw.get("definition_version")
            and readiness.get("definition_digest") == raw.get("definition_digest")
        )
    elif kind == "builtin":
        ready = ready and readiness.get("basis") == "builtin_compatibility"
    else:
        ready = False
    return {
        "id": raw["id"],
        "kind": kind,
        "name": str(raw.get("name", raw["id"]))[:120],
        "definitionVersion": raw.get("definition_version"),
        "definitionDigest": raw.get("definition_digest"),
        "selectable": bool(supported and ready),
        "reason": None if supported and ready else "workflow_unavailable",
        "image": {
            k: image[k]
            for k in (
                "profile",
                "mode",
                "dimensions",
                "reference_semantics",
                "resize_policy",
            )
            if k in image
        },
        "parameters": parameters,
    }


def normalize_catalog(raw):
    entries = raw.get("descriptors", [])
    if not isinstance(entries, list) or len(entries) > 128:
        raise ValueError("unsupported_parameter")
    result = []
    for item in entries:
        try:
            result.append(normalize_descriptor(item))
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    if len({item["id"] for item in result}) != len(result):
        raise ValueError("unsupported_parameter")
    return result


def map_parameters(descriptor, request, upstream_input=None):
    params = {}
    extras = request.get("additionalParameters", {})
    if not isinstance(extras, dict) or len(extras) > 64:
        raise ValueError("unsupported_parameter")
    allowed_extras = {
        key
        for key, spec in descriptor["parameters"].items()
        if not spec.get("role") and spec.get("type") != "managed_input"
    }
    if set(extras) - allowed_extras:
        raise ValueError("unsupported_parameter")
    if request.get("loras") and not any(
        s.get("role") == "loras" and s.get("max_items", 0) > 0
        for s in descriptor["parameters"].values()
    ):
        raise ValueError("unsupported_loras")
    for key, spec in descriptor["parameters"].items():
        role = spec.get("role")
        field = ROLES.get(role)
        if role == "initial_image":
            if not upstream_input:
                raise ValueError("input_unavailable")
            value = upstream_input
        elif role == "seed" and request.get(field) is None:
            value = random_seed(spec)
        elif role == "loras":
            value = [
                {
                    "name": v["name"],
                    "strength_model": v.get("strengthModel", 1),
                    "strength_clip": v.get("strengthClip", 1),
                }
                for v in request.get("loras", [])
            ]
        elif field and field in request:
            value = request[field]
        elif key in extras:
            value = extras[key]
        elif "default" in spec:
            value = spec["default"]
        elif spec.get("required", True):
            raise ValueError("unsupported_parameter")
        else:
            continue
        params[key] = validate_value(spec, value)
    return params
