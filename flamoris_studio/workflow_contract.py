"""Graph-free Image descriptors, role mapping and exact integer seed domains."""

import math
import json
import re
import secrets

MAX_SEED = 2**53 - 1
REFERENCE_FIELDS = {"initial_image": "referenceInputId", "character_image": "characterInputId",
                    "style_image": "styleInputId", "control_image": "controlInputId"}
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
    **REFERENCE_FIELDS,
}
SPEC_FIELDS = {
    "max_bytes",
    "label",
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
        if "pattern" in spec and re.fullmatch(spec["pattern"], value) is None:
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
    else:
        raise ValueError("unsupported_parameter")
    if "max_bytes" in spec and len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                              separators=(",", ":")).encode()) > spec["max_bytes"]:
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
        (type(x) in (int, float) if kind == "number" else type(value) is type(x))
        and value == x for x in spec["enum"]
    ):
        raise ValueError("unsupported_parameter")
    return value


def seed_domain(spec):
    if spec.get("type") != "integer":
        raise ValueError("unsupported_parameter")
    divisor = spec.get("multiple_of", 1)
    if type(divisor) is not int or divisor <= 0:
        raise ValueError("unsupported_parameter")
    limits = [spec.get("minimum", 0), spec.get("maximum", MAX_SEED)]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in limits):
        raise ValueError("unsupported_parameter")
    lower = max(0, math.ceil(limits[0]))
    upper = min(MAX_SEED, math.floor(limits[1]))
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
    registered = kind == "registered-comfy" and re.fullmatch(r"comfy-[a-f0-9]{64}", raw["id"]) is not None
    if not registered and (kind != "builtin" or raw["id"] not in {"text-to-image", "text-to-image-lora"}):
        raise ValueError("retired_recipe")
    if isinstance(raw.get("image"), dict) and raw["image"].get("profile") == "declared-comfy-v1":
        return normalize_declared_descriptor(raw)
    image = raw.get("image", {})
    readiness = raw.get("readiness", {})
    if not isinstance(image, dict) or not isinstance(readiness, dict):
        raise ValueError("unsupported_parameter")
    dimensions = image.get("dimensions", {})
    if not isinstance(dimensions, dict):
        raise ValueError("unsupported_parameter")
    supported = (
        raw.get("metadata_schema_version") == 2 and image.get("profile") == "image-v1"
        and image.get("mode") == "txt2img"
        and dimensions.get("mode") == "parameters"
        and "reference_semantics" not in image and "resize_policy" not in image
    )
    reference_roles = image.get("reference_roles", [])
    if registered:
        supported = (raw.get("metadata_schema_version") == 2
                     and image.get("profile") in {"checkpoint-comfy-v1", "checkpoint-reference-v1"}
                     and image.get("mode") in {"txt2img", "img2img"}
                     and dimensions.get("mode") == "parameters"
                     and type(raw.get("definition_version")) is int and raw.get("definition_version") == 1
                     and raw.get("definition_digest") == raw["id"][6:]
                     and readiness.get("state") == "validated"
                     and readiness.get("basis") == "static_checkpoint_profile"
                     and readiness.get("live_provider_verified") is False)
        if image.get("profile") == "checkpoint-comfy-v1":
            reference_roles = ["initial_image"] if image.get("reference_semantics") == "init_image" else []
        if (not isinstance(reference_roles, list) or len(reference_roles) > 4
            or any(type(v) is not str or v not in REFERENCE_FIELDS for v in reference_roles)
            or len(set(reference_roles)) != len(reference_roles)):
            raise ValueError("unsupported_parameter")
    if registered and ((image.get("mode") == "img2img") != ("initial_image" in reference_roles)):
        supported = False
    parameters = {}
    roles = set()
    source = raw.get("parameters", {})
    if not isinstance(source, dict) or len(source) > 64:
        raise ValueError("unsupported_parameter")
    for key, value in source.items():
        if not isinstance(key, str) or len(key) > 128 or not isinstance(value, dict):
            raise ValueError("unsupported_parameter")
        spec = {k: v for k, v in value.items() if k in SPEC_FIELDS}
        if registered and spec.get("role") == "managed_input" and key == "reference_image" and "initial_image" in reference_roles:
            spec["role"] = "initial_image"
        if registered and spec.get("role") in REFERENCE_FIELDS:
            # The browser receives only Studio UUID handles, never Controller input IDs.
            if spec.get("type") != "string" or value.get("pattern") != "^[a-f0-9]{32}$" or spec.get("required") is not True:
                raise ValueError("unsupported_parameter")
            spec["pattern"] = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
        # All public metadata is bounded scalar data; never forward graph bindings
        # or arbitrary nested objects from upstream metadata.
        spec.pop("items", None)
        if spec.get("type") not in {"string", "integer", "number", "boolean", "ordered_loras"}:
            raise ValueError("unsupported_parameter")
        for field in ("minimum", "maximum"):
            if field in spec and (type(spec[field]) not in (int, float) or not math.isfinite(spec[field])):
                raise ValueError("unsupported_parameter")
        for field in ("min_length", "max_length", "min_items", "max_items", "max_bytes"):
            if field in spec and (type(spec[field]) is not int or not 0 <= spec[field] <= 20000):
                raise ValueError("unsupported_parameter")
        for field in ("required",):
            if field in spec and type(spec[field]) is not bool:
                raise ValueError("unsupported_parameter")
        for field in ("model_kind", "pattern"):
            if field in spec and (not isinstance(spec[field], str) or len(spec[field]) > 1024):
                raise ValueError("unsupported_parameter")
        if "pattern" in spec:
            try:
                re.compile(spec["pattern"])
            except re.error as exc:
                raise ValueError("unsupported_parameter") from exc
        if "enum" in spec and (
            not isinstance(spec["enum"], list) or not 1 <= len(spec["enum"]) <= 64
            or any(type(v) not in (str, int, float, bool) or (isinstance(v, str) and len(v) > 20000)
                   or (type(v) in (int, float) and not math.isfinite(v)) for v in spec["enum"])
        ):
            raise ValueError("unsupported_parameter")
        if "default" in spec:
            validate_value(spec, spec["default"])
        role = spec.get("role")
        if role is not None and not isinstance(role, str):
            raise ValueError("unsupported_parameter")
        if role is not None:
            if role not in ROLES or role in roles:
                supported = False
            roles.add(role)
            expected_type = {
                "checkpoint": "string", "positive_prompt": "string", "negative_prompt": "string",
                "width": "integer", "height": "integer", "steps": "integer", "seed": "integer",
                "cfg": "number", "denoise": "number", "sampler": "string", "scheduler": "string",
                "loras": "ordered_loras",
                **{role: "string" for role in REFERENCE_FIELDS},
            }.get(role)
            if spec.get("type") != expected_type:
                supported = False
        elif spec["type"] == "ordered_loras":
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
    supported = supported and {"checkpoint", "positive_prompt", "width", "height"} <= roles
    if not registered and roles & set(REFERENCE_FIELDS):
        supported = False
    if registered and (roles & set(REFERENCE_FIELDS)) != set(reference_roles):
        supported = False
    ready = readiness.get("state") == "ready"
    ready = (ready and readiness.get("basis") == "builtin_compatibility"
             and raw.get("definition_version") is None and raw.get("definition_digest") is None)
    if registered:
        ready = supported  # Static compatibility is explicit, never a fabricated live attestation.
    return {
        "id": raw["id"],
        "kind": kind,
        "name": str(raw.get("name", raw["id"]))[:120],
        "definitionVersion": raw.get("definition_version"),
        "definitionDigest": raw.get("definition_digest"),
        "selectable": bool(supported and ready),
        "reason": None if supported and ready else "workflow_unavailable",
        "image": {
            k: image[k] for k in ("profile", "mode") if k in image
        } | {"dimensions": {"mode": dimensions.get("mode")}},
        "parameters": parameters,
        "referenceRoles": reference_roles if registered else [],
        "liveProviderVerified": False if registered else None,
        "recipeSchema": 8 if registered and image.get("profile") == "checkpoint-reference-v1" else 7 if registered else 1,
    }


def normalize_catalog(raw):
    if not isinstance(raw, dict):
        raise ValueError("unsupported_parameter")
    entries = raw.get("descriptors", [])
    if not isinstance(entries, list) or len(entries) > 128:
        raise ValueError("unsupported_parameter")
    result = []
    for item in entries:
        if isinstance(item, dict) and item.get("capability_id") == "video.generate":
            continue
        try:
            result.append(normalize_descriptor(item))
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    if len({item["id"] for item in result}) != len(result):
        raise ValueError("unsupported_parameter")
    return result


def map_parameters(descriptor, request):
    if descriptor.get("recipeSchema") in {10, 11}:
        if not descriptor.get("selectable"):
            raise ValueError("unsupported_parameter")
        extras, images = request.get("additionalParameters", {}), request.get("imageInputs", {})
        specs = descriptor["parameters"]
        image_keys = {k for k, v in specs.items() if v.get("role") == "managed_input"}
        if (not isinstance(extras, dict) or not isinstance(images, dict)
            or set(extras) - (set(specs) - image_keys) or set(images) != image_keys):
            raise ValueError("unsupported_parameter")
        result = {}
        for key, spec in specs.items():
            value = images.get(key) if key in image_keys else extras.get(key, spec.get("default"))
            if value is None and not spec.get("required"):
                continue
            result[key] = validate_value(spec, value)
        return result
    fields = {ROLES[spec["role"]] for spec in descriptor["parameters"].values() if spec.get("role") in REFERENCE_FIELDS}
    if (not descriptor.get("selectable") or any(request.get(field) is not None and field not in fields
                                               for field in REFERENCE_FIELDS.values())):
        raise ValueError("unsupported_parameter")
    params = {}
    extras = request.get("additionalParameters", {})
    if not isinstance(extras, dict) or len(extras) > 64:
        raise ValueError("unsupported_parameter")
    allowed_extras = {
        key
        for key, spec in descriptor["parameters"].items()
        if not spec.get("role")
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
        if role == "seed" and request.get(field) is None:
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



def normalize_declared_descriptor(raw):
    # A separate versioned image contract, using the same scalar validator.
    if (not isinstance(raw.get("readiness"), dict) or raw.get("kind") != "registered-comfy" or re.fullmatch(r"comfy-[a-f0-9]{64}", raw.get("id", "")) is None
        or raw.get("metadata_schema_version") != 3 or raw.get("definition_version") != 1
        or raw.get("definition_digest") != raw["id"][6:]
        or raw.get("image") != {"profile": "declared-comfy-v1", "mode": "declared", "dimensions": {"mode": "graph"}}
        or raw.get("readiness", {}).get("state") != "validated"
        or raw.get("readiness", {}).get("live_provider_verified") is not False
        or raw.get("readiness", {}).get("basis") != "installed_object_info"):
        raise ValueError("unsupported_parameter")
    specs, images = normalize_declared_parameters(raw.get("parameters"))
    return {"id": raw["id"], "kind": "registered-comfy", "name": str(raw.get("name", raw["id"]))[:120],
            "definitionVersion": 1, "definitionDigest": raw["id"][6:], "selectable": True,
            "reason": None, "image": raw["image"], "parameters": specs,
            "referenceRoles": images, "liveProviderVerified": False, "recipeSchema": 10}


def normalize_declared_parameters(source):
    if not isinstance(source, dict) or not 1 <= len(source) <= 64:
        raise ValueError("unsupported_parameter")
    specs, images = {}, []
    for key, value in source.items():
        if type(key) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) or not isinstance(value, dict):
            raise ValueError("unsupported_parameter")
        spec = {k: v for k, v in value.items() if k in SPEC_FIELDS}
        if spec.get("type") not in {"string", "integer", "number", "boolean"} or spec.get("role") not in {"prompt", "parameter", "model", "managed_input"}:
            raise ValueError("unsupported_parameter")
        if spec.get("role") != "parameter" and spec.get("type") != "string":
            raise ValueError("unsupported_parameter")
        model_kind = spec.get("model_kind")
        if (spec["role"] == "model" and (type(model_kind) is not str or not 1 <= len(model_kind) <= 64)) or (spec["role"] != "model" and model_kind is not None):
            raise ValueError("unsupported_parameter")
        if type(spec.get("required")) is not bool or type(spec.get("label")) is not str or not 1 <= len(spec["label"]) <= 120:
            raise ValueError("unsupported_parameter")
        for field in ("minimum", "maximum"):
            if field in spec and (type(spec[field]) not in (int, float) or not math.isfinite(spec[field])):
                raise ValueError("unsupported_parameter")
        for field in ("min_length", "max_length"):
            if field in spec and (type(spec[field]) is not int or not 0 <= spec[field] <= 20000):
                raise ValueError("unsupported_parameter")
        if "enum" in spec and (type(spec["enum"]) is not list or not 1 <= len(spec["enum"]) <= 512 or any(type(v) not in (str, int, float, bool) for v in spec["enum"])):
            raise ValueError("unsupported_parameter")
        spec.pop("pattern", None)
        if spec["role"] == "managed_input":
            if len(key) > 32:
                raise ValueError("unsupported_parameter")
            if spec["required"] is not True or "default" in spec:
                raise ValueError("unsupported_parameter")
            spec["pattern"] = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
            images.append(key)
        if "default" in spec:
            validate_value(spec, spec["default"])
        specs[key] = spec
    return specs, images
