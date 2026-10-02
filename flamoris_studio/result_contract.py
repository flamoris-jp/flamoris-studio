"""Safe additive catalog metadata; output roles come from the owning adapter."""

import re
from dataclasses import dataclass

from .gateway import GatewayError
from .media import filename

KINDS = {
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "image/vnd.adobe.photoshop": "image",
    "audio/wav": "audio",
    "audio/mpeg": "audio",
    "video/mp4": "video",
    "audio/midi": "midi",
    "application/json": "metadata",
}
NATIVE_IMAGES = {"image/png", "image/jpeg", "image/webp"}


def preview_kind(kind, mime):
    if kind == "image" and mime in NATIVE_IMAGES:
        return "image"
    if kind == "audio" and mime in {"audio/wav", "audio/mpeg"}:
        return "audio"
    if kind == "video" and mime == "video/mp4":
        return "video"
    return "file"


def output_role(value):
    if value is None:
        return None
    if (
        type(value) is not dict
        or set(value) != {"port", "role", "index"}
        or any(
            type(value.get(name)) is not str
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value[name])
            for name in ("port", "role")
        )
        or type(value.get("index")) is not int
        or not 0 <= value["index"] < 128
    ):
        raise GatewayError("validation")
    return dict(value)


@dataclass(frozen=True)
class CatalogOutput:
    upstream_id: str
    display_name: str
    media_kind: str
    mime_type: str
    size_bytes: int | None
    role: dict | None


def normalize_outputs(listing):
    if type(listing) is not list or len(listing) > 64:
        raise GatewayError("validation")
    result, ids, roles, port_roles, role_ports = [], set(), set(), {}, {}
    for item in listing:
        if type(item) is not dict:
            raise GatewayError("validation")
        upstream, mime = item.get("asset_id"), item.get("mime_type")
        if (
            type(upstream) is not str
            or not 0 < len(upstream) <= 256
            or upstream in ids
            or type(mime) is not str
            or len(mime) > 64
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9.!#$&^_+-]*/[a-z0-9][a-z0-9.!#$&^_+-]*", mime
            )
        ):
            raise GatewayError("validation")
        ids.add(upstream)
        supplied = {name for name in ("port", "role", "role_index") if name in item}
        role = None
        if supplied:
            if len(supplied) != 3:
                raise GatewayError("validation")
            role = output_role(
                {
                    "port": item["port"],
                    "role": item["role"],
                    "index": item["role_index"],
                }
            )
            identity = (role["role"], role["index"])
            if (
                identity in roles
                or port_roles.get(role["port"], role["role"]) != role["role"]
                or role_ports.get(role["role"], role["port"]) != role["port"]
            ):
                raise GatewayError("validation")
            roles.add(identity)
            port_roles[role["port"]], role_ports[role["role"]] = (
                role["role"],
                role["port"],
            )
        kind = KINDS.get(mime)
        # Unsupported/mismatched types remain metadata + attachment download;
        # they cannot obtain a native renderer by naming a filename/media kind.
        kind = (
            kind if kind is not None and item.get("media_kind") == kind else "unknown"
        )
        size = item.get("size_bytes")
        size = size if type(size) is int and 0 <= size <= 2**63 - 1 else None
        result.append(
            CatalogOutput(
                upstream,
                filename(
                    item.get("filename") if type(item.get("filename")) is str else None
                ),
                kind,
                mime,
                size,
                role,
            )
        )
    return result
