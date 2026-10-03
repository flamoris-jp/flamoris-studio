"""Pinned native Music contracts; configuration does not attest a runtime run."""
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .gateway import GatewayError
from .speech_contract import exact

MUSIC_PROOF = {"profile": "yue2-single-track-v1",
    "source_revision": "decfe04c2ae2f8c73855832a56ddda0fce849407", "cot": "full", "tracks": 1,
    "output_format": "wav16", "sample_rate": 48000, "channels": 2}
TRANSCRIPTION_PROOF = {"profile": "sheetsage2-python-cpu-v1",
    "entrypoint_sha256": "20e6b23c910bfdecf012a8ee8d45efcb31cb6cb21351da5921d95fb877e70de6",
    "device": "cpu", "dtype": "fp32", "offline": True}
MUSIC_PARAMETERS = {
    "style": {"type": "string", "min_length": 1, "max_length": 1024, "max_bytes": 4096, "role": "style", "required": True},
    "lyrics": {"type": "string", "default": "", "max_length": 4096, "max_bytes": 16384, "role": "lyrics", "required": False},
    "seconds": {"type": "number", "default": 30, "minimum": 1, "maximum": 120, "role": "seconds", "required": False},
    "steps": {"type": "integer", "default": 32, "minimum": 1, "maximum": 64, "role": "steps", "required": False},
    **{name: {"type": "integer", "default": 0, "minimum": 0, "maximum": 2**53 - 1, "role": name, "required": False} for name in ("seed", "lm_seed")},
}
TRANSCRIPTION_PARAMETERS = {
    "audio": {"type": "managed_input", "media_types": ["audio/wav"], "role": "audio", "required": True},
    "max_seconds": {"type": "number", "default": 30.0, "minimum": 1, "maximum": 120, "role": "max_seconds", "required": False},
    "melody_only": {"type": "boolean", "default": False, "role": "melody_only", "required": False},
}


class MusicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    style: str = Field(min_length=1, max_length=1024)
    lyrics: str = Field(default="", max_length=4096)
    seconds: float = Field(default=30.0, ge=1, le=120, allow_inf_nan=False)
    steps: int = Field(default=32, ge=1, le=64)
    seed: int | None = Field(default=0, ge=0, le=2**53 - 1)
    lm_seed: int | None = Field(default=0, ge=0, le=2**53 - 1)

    @field_validator("style", "lyrics")
    @classmethod
    def text(cls, value, info):
        limit = 4096 if info.field_name == "style" else 16384
        if len(value.encode()) > limit or any(ord(c) < 32 and c not in "\n\t" for c in value) or (info.field_name == "style" and not value.strip()):
            raise ValueError("Unsupported Music text")
        return value


class TranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    referenceInputId: str = Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")
    max_seconds: float = Field(default=30.0, ge=1, le=120, allow_inf_nan=False)
    melody_only: bool = False


def contract(operation):
    generation = operation == "generate"
    return {"id": "music-generate" if generation else "music-transcribe", "kind": "native",
        "metadata_schema_version": 2, "schema_version": 5 if generation else 6, "version": 1,
        "provider_id": "yue2" if generation else "sheetsage2",
        "capability_id": "music.generate" if generation else "music.transcribe",
        "parameters": MUSIC_PARAMETERS if generation else TRANSCRIPTION_PARAMETERS,
        "input_roles": [] if generation else [{"parameter": "audio", "role": "audio", "media_kind": "audio"}],
        "output_roles": [{"port": "audio", "role": "audio", "media_kind": "audio"},
                         {"port": "score", "role": "score", "media_kind": "score"},
                         {"port": "metadata", "role": "metadata", "media_kind": "metadata"}] if generation else [
            {"port": "midi", "role": "midi", "media_kind": "midi"},
            {"port": "score", "role": "score", "media_kind": "score", "optional": True},
            {"port": "events", "role": "events", "media_kind": "metadata"},
            {"port": "summary", "role": "summary", "media_kind": "metadata"},
            {"port": "annotations", "role": "annotations", "media_kind": "metadata", "optional": True},
            {"port": "parts", "role": "parts", "media_kind": "midi", "collection": True, "optional": True}],
        "qualification": "configured-http-contract" if generation else "configured-local-resources",
        "music" if generation else "transcription": MUSIC_PROOF if generation else TRANSCRIPTION_PROOF,
        "readiness": {"status": "not-attested"}}


def qualified_music(catalog, capabilities, operation):
    if type(catalog) is not dict or type(capabilities) is not dict:
        return False
    entries, routes = catalog.get("descriptors"), capabilities.get("capabilities")
    if type(entries) is not list or len(entries) > 128 or type(routes) is not list or len(routes) > 128:
        return False
    expected = contract(operation)
    matching = [x for x in entries if type(x) is dict and x.get("id") == expected["id"]]
    selected = [x for x in routes if type(x) is dict and x.get("id") == expected["capability_id"]]
    if len(matching) != 1 or len(selected) != 1:
        return False
    raw, route = matching[0], selected[0]
    return all(exact(raw.get(key), value) for key, value in expected.items()) and route.get("available") is True and route.get("provider_id") == expected["provider_id"] and exact(route.get("workflow_templates"), [expected["id"]])


def checked_music_build(result, parameters, operation):
    expected = contract(operation)
    proof_key = "music" if operation == "generate" else "transcription"
    if (type(result) is not dict or not exact(result.get("schema_version"), expected["schema_version"]) or result.get("template") != expected["id"] or not exact(result.get("parameters"), parameters) or not exact(result.get(proof_key), expected[proof_key]) or "prompt" not in result or result["prompt"] is not None or type(result.get("workflow_id")) is not str or not re.fullmatch(r"[a-f0-9]{32}", result["workflow_id"])):
        raise GatewayError("validation")
    return result["workflow_id"]


def checked_music_outputs(outputs, operation, request=None):
    if operation == "music.generate":
        expected = {("audio", "audio", "audio", "audio/wav"), ("score", "score", "score", "text/vnd.abc"), ("metadata", "metadata", "metadata", "application/json")}
        if len(outputs) != 3 or {(x.role["port"], x.role["role"], x.media_kind, x.mime_type) for x in outputs if x.role and x.role["index"] == 0} != expected:
            raise GatewayError("validation")
    elif operation == "music.transcribe":
        allowed = {("midi", "midi", "midi", "audio/midi"), ("score", "score", "score", "text/vnd.abc"), ("events", "events", "metadata", "application/json"), ("summary", "summary", "metadata", "application/json"), ("annotations", "annotations", "metadata", "application/json"), ("parts", "parts", "midi", "audio/midi")}
        required = {("midi", "midi", "midi", "audio/midi"), ("events", "events", "metadata", "application/json"), ("summary", "summary", "metadata", "application/json")}
        if request and request.get("melody_only") is True:
            required.add(("score", "score", "score", "text/vnd.abc"))
        actual = {(x.role["port"], x.role["role"], x.media_kind, x.mime_type) for x in outputs if x.role and x.role["index"] == 0}
        if not outputs or not required <= actual or any(not x.role or (x.role["port"], x.role["role"], x.media_kind, x.mime_type) not in allowed or (x.role["index"] != 0 if x.role["role"] != "parts" else x.role["index"] > 3) for x in outputs):
            raise GatewayError("validation")
    else:
        raise GatewayError("validation")
