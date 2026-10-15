"""Reviewed native no-reference Speech contract; independent from Image readiness."""

import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .gateway import GatewayError

TEMPLATE = "speech-no-reference"
PROOF = {"profile": "irodori-no-reference-v1",
         "source_revision": "89f9d8fbd4d51ea019867ee1197725ede1df13c5",
         "reference_audio": False, "candidates": 1}
HTTP_PROOF = {
    "profile": "irodori-server-no-reference-v1",
    "server_contract_revision": "61012c760f22f7b4a6c21c5c5f8f9e148120b6f9",
    "reference_audio": False, "candidates": 1,
}
REFERENCE_TEMPLATE = "speech-reference"
REFERENCE_PROOF = {**HTTP_PROOF, "profile": "irodori-server-reference-v1", "reference_audio": True}
REFERENCE_PARAMETER = {
    "type": "managed_input", "role": "reference_audio", "required": True,
    "media_types": ["audio/wav"], "max_bytes": 8 * 1024**2, "max_seconds": 30,
}
REFERENCE_ROLES = [
    {"parameter": "reference_audio", "role": "reference_audio", "media_kind": "audio"}
]
PARAMETERS = {
    "text": {"type": "string", "role": "text", "required": True,
             "min_length": 1, "max_length": 512, "max_bytes": 2048},
    "caption": {"type": "string", "role": "caption", "required": False,
                "default": "", "max_length": 512, "max_bytes": 2048},
    "seconds": {"type": "number", "role": "seconds", "required": False,
                "default": 10, "minimum": 0.5, "maximum": 30},
    "steps": {"type": "integer", "role": "steps", "required": False,
              "default": 40, "minimum": 1, "maximum": 80},
    "seed": {"type": "integer", "role": "seed", "required": False,
             "default": 0, "minimum": 0, "maximum": 2**53 - 1},
}


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=512)
    caption: str = Field(default="", max_length=512)
    seconds: float = Field(default=10, ge=0.5, le=30, allow_inf_nan=False)
    steps: int = Field(default=40, ge=1, le=80)
    seed: int | None = Field(default=0, ge=0, le=2**53 - 1)

    @field_validator("text", "caption")
    @classmethod
    def bounded_text(cls, value, info):
        if len(value.encode()) > 2048 or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("Unsupported speech text")
        if info.field_name == "text" and not value.strip():
            raise ValueError("Speech text is required")
        return value


def exact(value, expected):
    # Canonical JSON distinguishes booleans from integers and extra schema keys.
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True)
    except (ValueError, TypeError):
        return False


def qualified_speech(catalog, capabilities):
    if type(catalog) is not dict or type(capabilities) is not dict:
        return False
    entries, routes = catalog.get("descriptors"), capabilities.get("capabilities")
    if type(entries) is not list or len(entries) > 128 or type(routes) is not list or len(routes) > 128:
        return False
    matching = [item for item in entries if type(item) is dict and item.get("id") == TEMPLATE]
    selected = [item for item in routes if type(item) is dict and item.get("id") == "speech.generate"]
    if len(matching) != 1 or len(selected) != 1:
        return False
    raw, route = matching[0], selected[0]
    http = exact(raw.get("speech"), HTTP_PROOF)
    fields = {"id": TEMPLATE, "kind": "native", "metadata_schema_version": 2,
              "schema_version": 4, "version": 1, "provider_id": "irodori",
              "capability_id": "speech.generate", "parameters": PARAMETERS,
              "input_roles": [], "output_roles": [{"port": "audio", "role": "audio", "media_kind": "audio"}],
              "speech": HTTP_PROOF if http else PROOF,
              "qualification": "configured-http-contract" if http else "configured-local-resources",
              "readiness": {"status": "not-attested"}}
    return (all(exact(raw.get(key), value) for key, value in fields.items()) and
            route.get("available") is True and route.get("provider_id") == "irodori" and
            any(exact(route.get("workflow_templates"), templates) for templates in (
                [TEMPLATE], [REFERENCE_TEMPLATE, TEMPLATE])))


def checked_build(result, parameters, proof=PROOF):
    if not any(exact(proof, allowed) for allowed in (PROOF, HTTP_PROOF)):
        raise GatewayError("validation")
    if (type(result) is not dict or type(result.get("schema_version")) is not int or
            result["schema_version"] != 4 or result.get("template") != TEMPLATE or
            result.get("parameters") != parameters or not exact(result.get("speech"), proof) or
            "prompt" not in result or result["prompt"] is not None or
            type(result.get("workflow_id")) is not str or not re.fullmatch(r"[a-f0-9]{32}", result["workflow_id"])):
        raise GatewayError("validation")
    # Re-validate normalized parameters so a bool/int equality cannot satisfy
    # the echoed build contract. No graph, managed input or alternate profile.
    try:
        parsed = SpeechRequest.model_validate(result["parameters"])
        if parsed.seed is None or parsed.model_dump() != parameters:
            raise ValueError()
    except (ValueError, TypeError):
        raise GatewayError("validation") from None
    return result["workflow_id"]



def qualified_reference(catalog, capabilities):
    if not qualified_speech(catalog, capabilities):
        return False
    entries = [
        item for item in catalog["descriptors"]
        if type(item) is dict and item.get("id") == REFERENCE_TEMPLATE
    ]
    if len(entries) != 1:
        return False
    fields = {
        "id": REFERENCE_TEMPLATE, "kind": "native", "metadata_schema_version": 2,
        "schema_version": 9, "version": 1, "provider_id": "irodori",
        "capability_id": "speech.generate",
        "parameters": {**PARAMETERS, "reference_audio": REFERENCE_PARAMETER},
        "input_roles": REFERENCE_ROLES,
        "output_roles": [{"port": "audio", "role": "audio", "media_kind": "audio"}],
        "speech": REFERENCE_PROOF, "qualification": "configured-http-contract",
        "readiness": {"status": "not-attested"},
    }
    return all(exact(entries[0].get(key), value) for key, value in fields.items()) and any(
        type(route) is dict and exact(route.get("workflow_templates"), [REFERENCE_TEMPLATE, TEMPLATE])
        for route in capabilities["capabilities"]
        if type(route) is dict and route.get("id") == "speech.generate"
    )


def checked_reference_build(result, parameters):
    reference = parameters.get("reference_audio")
    if type(reference) is not str or re.fullmatch(r"[a-f0-9]{32}", reference) is None:
        raise GatewayError("validation")
    if (
        type(result) is not dict or not exact(result.get("schema_version"), 9)
        or result.get("template") != REFERENCE_TEMPLATE
        or not exact(result.get("speech"), REFERENCE_PROOF)
        or result.get("parameters") != parameters
    ):
        raise GatewayError("validation")
    # Reuse strict scalar, identity and no-graph validation after removing only
    # the separately checked managed input binding.
    scalars = {key: value for key, value in parameters.items() if key != "reference_audio"}
    normalized = {
        **result, "template": TEMPLATE, "schema_version": 4,
        "speech": HTTP_PROOF, "parameters": scalars,
    }
    return checked_build(normalized, scalars, HTTP_PROOF)
