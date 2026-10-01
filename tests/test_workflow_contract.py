import pytest

from flamoris_studio.workflow_contract import map_parameters, normalize_catalog, random_seed, seed_domain


def descriptor(mode="txt2img"):
    roles = {"model_key": {"type": "string", "role": "checkpoint", "required": True},
             "prompt_key": {"type": "string", "role": "positive_prompt", "required": True},
             "w": {"type": "integer", "role": "width", "minimum": 64, "maximum": 4096, "multiple_of": 8},
             "h": {"type": "integer", "role": "height", "minimum": 64, "maximum": 4096, "multiple_of": 8},
             "random": {"type": "integer", "role": "seed", "minimum": 4, "maximum": 32, "multiple_of": 4}}
    image = {"profile": "image-v1", "mode": mode, "dimensions": {"mode": "parameters"}}
    if mode == "img2img":
        image.update(reference_semantics="initial_image", resize_policy="center-crop-resize")
        roles.update(source={"type": "managed_input", "role": "initial_image"},
                     strength={"type": "number", "role": "denoise", "minimum": 0, "maximum": 1})
    return {"id": "arbitrary-image", "kind": "definition", "metadata_schema_version": 2,
            "definition_version": 7, "definition_digest": "sha256:" + "a" * 64,
            "readiness": {"state": "ready", "definition_version": 7, "definition_digest": "sha256:" + "a" * 64},
            "image": image, "parameters": roles, "graph": {"private": "never exposed"}}


def test_arbitrary_public_keys_effective_dimensions_seed_and_no_graph():
    item = normalize_catalog({"descriptors": [descriptor()]})[0]
    assert "graph" not in item and item["selectable"]
    values = map_parameters(item, {"checkpoint": "model", "positivePrompt": "long prompt",
                                  "width": 768, "height": 1152, "seed": 8})
    assert values == {"model_key": "model", "prompt_key": "long prompt", "w": 768, "h": 1152, "random": 8}


@pytest.mark.parametrize("change", ["digest", "validated", "legacy"])
def test_missing_or_mismatched_readiness_disabled(change):
    item = descriptor()
    if change == "digest":
        item["readiness"]["definition_digest"] = "sha256:" + "b" * 64
    elif change == "legacy":
        item["metadata_schema_version"] = 1
    else:
        item["readiness"]["state"] = "validated"
    assert not normalize_catalog({"descriptors": [item]})[0]["selectable"]


@pytest.mark.parametrize("spec,expected", [
    ({"type": "integer", "minimum": 2, "maximum": 2}, 2),
    ({"type": "integer", "enum": [True, "4", 8], "multiple_of": 4}, 8),
    ({"type": "integer", "minimum": 4.2, "maximum": 8, "multiple_of": 4}, 8),
])
def test_singleton_typed_seed_domains(spec, expected):
    assert random_seed(spec) == expected


@pytest.mark.parametrize("spec", [
    {"type": "integer", "minimum": 1, "maximum": 2, "multiple_of": 4},
    {"type": "integer", "minimum": 2**53},
    {"type": "integer", "multiple_of": True},
    {"type": "integer", "multiple_of": 0.5},
    {"type": "number"},
])
def test_empty_invalid_seed_domains_fail_closed(spec):
    with pytest.raises(ValueError):
        seed_domain(spec)


def test_managed_injection_and_unsupported_loras_rejected():
    item = normalize_catalog({"descriptors": [descriptor("img2img")]})[0]
    values = {"checkpoint": "model", "positivePrompt": "x", "width": 512, "height": 512, "seed": 8, "denoise": 0.5}
    with pytest.raises(ValueError):
        map_parameters(item, {**values, "additionalParameters": {"source": "raw-id"}}, "owned-id")
    with pytest.raises(ValueError):
        map_parameters(item, {**values, "loras": [{"name": "lora"}]}, "owned-id")
    with pytest.raises(ValueError):
        map_parameters(item, values)
    assert map_parameters(item, values, "owned-id")["source"] == "owned-id"
