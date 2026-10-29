"""Graph-free projection of bounded Wan and silent MiniMax H3 video profiles."""

import re

from .workflow_contract import map_parameters, normalize_declared_parameters, random_seed


def budgets(values, profile="wan-video-v1"):
    if profile not in {"wan-video-v1", "minimax-h3-video-v1"}:
        raise ValueError("unsupported_video_contract")
    h3 = profile == "minimax-h3-video-v1"
    width, height, frames, fps = (values.get(key) for key in ("width", "height", "frames", "fps"))
    if (any(type(value) is not int for value in (width, height, frames, fps))
        or not 64 <= width <= 1024 or not 64 <= height <= 1024
        or width % (32 if h3 else 16) or height % (32 if h3 else 16) or width * height > 832 * 832
        or not 5 <= frames <= 161 or (frames % 17 != 5 if h3 else frames % 4 != 1)
        or (h3 and fps != 24)
        or not 1 <= fps <= 32 or frames / fps > 10.1):
        raise ValueError("unsupported_video_parameters")


def normalize_video_descriptor(raw):
    if type(raw) is not dict:
        raise ValueError("unsupported_video_contract")
    video, readiness = raw.get("video"), raw.get("readiness")
    if (raw.get("kind") != "registered-comfy"
        or type(raw.get("id")) is not str or not re.fullmatch(r"comfy-[a-f0-9]{64}", raw["id"])
        or type(raw.get("metadata_schema_version")) is not int or raw["metadata_schema_version"] != 4
        or type(raw.get("definition_version")) is not int or raw["definition_version"] != 1
        or type(raw.get("version")) is not int or raw["version"] != 1
        or raw.get("definition_digest") != raw["id"][6:]
        or raw.get("provider_id") != "comfyui" or raw.get("capability_id") != "video.generate"
        or type(video) is not dict or set(video) != {"profile", "mode", "conditioning_node"}
        or video.get("profile") not in {"wan-video-v1", "minimax-h3-video-v1"} or video.get("mode") not in {"i2v", "flf2v"}
        or type(video.get("conditioning_node")) is not str or not re.fullmatch(r"[0-9]{1,8}", video["conditioning_node"])
        or type(readiness) is not dict or readiness.get("state") != "validated"
        or readiness.get("basis") != "installed_object_info"
        or readiness.get("live_provider_verified") is not False):
        raise ValueError("unsupported_video_contract")
    specs, images = normalize_declared_parameters(raw.get("parameters"))
    expected = {"start_image"} | ({"end_image"} if video["mode"] == "flf2v" else set())
    if set(images) != expected:
        raise ValueError("unsupported_video_roles")
    defaults = {}
    h3 = video["profile"] == "minimax-h3-video-v1"
    for key, low, high in (("width", 64, 1024), ("height", 64, 1024), ("frames", 5, 161), ("fps", 24 if h3 else 1, 24 if h3 else 32)):
        spec = specs.get(key, {})
        if (spec.get("type") != "integer" or spec.get("role") != "parameter"
            or spec.get("minimum", low - 1) < low or spec.get("maximum", high + 1) > high
            or type(spec.get("default")) is not int):
            raise ValueError("unsupported_video_parameters")
        defaults[key] = spec["default"]
    budgets(defaults, video["profile"])
    for key in ("width", "height"):
        specs[key]["multiple_of"] = 32 if h3 else 16
    if "seed" in specs and (specs["seed"].get("type") != "integer" or specs["seed"].get("role") != "parameter"):
        raise ValueError("unsupported_video_parameters")
    return {"id": raw["id"], "kind": "registered-comfy", "name": str(raw.get("name", "Video"))[:120],
            "definitionVersion": 1, "definitionDigest": raw["id"][6:], "recipeSchema": 11,
            "video": {"profile": video["profile"], "mode": video["mode"]},
            "parameters": specs, "referenceRoles": sorted(images), "selectable": True, "reason": None}


def normalize_video_catalog(raw):
    if type(raw) is not dict or type(raw.get("descriptors")) is not list or len(raw["descriptors"]) > 128:
        raise ValueError("unsupported_video_contract")
    result = []
    for item in raw["descriptors"]:
        try:
            result.append(normalize_video_descriptor(item))
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    if len({item["id"] for item in result}) != len(result):
        raise ValueError("duplicate_video_definition")
    return result


def video_parameters(descriptor, original):
    values = {**original, "additionalParameters": dict(original.get("additionalParameters", {}))}
    if "seed" in descriptor["parameters"] and "seed" not in values["additionalParameters"]:
        values["additionalParameters"]["seed"] = random_seed(descriptor["parameters"]["seed"])
    result = map_parameters(descriptor, values)
    budgets(result, descriptor["video"]["profile"])
    return result
