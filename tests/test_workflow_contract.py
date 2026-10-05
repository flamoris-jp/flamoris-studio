import pytest

from flamoris_studio.workflow_contract import map_parameters, normalize_catalog, random_seed, seed_domain, validate_value


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
    return {"id": "text-to-image", "kind": "builtin", "metadata_schema_version": 2,
            "readiness": {"state": "ready", "basis": "builtin_compatibility"},
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
        item["definition_digest"] = "sha256:" + "b" * 64
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
    {"type": "integer", "minimum": True},
    {"type": "integer", "maximum": float("inf")},
])
def test_empty_invalid_seed_domains_fail_closed(spec):
    with pytest.raises(ValueError):
        seed_domain(spec)


@pytest.mark.parametrize("mode", ["txt2img", "img2img"])
def test_retired_reference_descriptors_cannot_masquerade_as_builtins(mode):
    raw = descriptor(mode)
    raw["parameters"]["source"] = {"type": "managed_input", "role": "initial_image"}
    assert normalize_catalog({"descriptors": [raw]}) == []


def test_fixed_dimensions_and_reference_semantics_are_not_retained_templates():
    raw = descriptor()
    raw["image"]["dimensions"] = {"mode": "fixed", "width": 512, "height": 512}
    assert not normalize_catalog({"descriptors": [raw]})[0]["selectable"]
    raw = descriptor()
    raw["image"]["reference_semantics"] = "initial_image"
    assert not normalize_catalog({"descriptors": [raw]})[0]["selectable"]


def test_reference_and_unsupported_loras_are_rejected_by_retained_mapping():
    item = normalize_catalog({"descriptors": [descriptor()]})[0]
    values = {"checkpoint": "model", "positivePrompt": "prompt", "width": 512, "height": 512}
    for extra in ({"referenceInputId": "owned-id"}, {"loras": [{"name": "lora"}]}):
        with pytest.raises(ValueError):
            map_parameters(item, {**values, **extra})
    with pytest.raises(ValueError):
        validate_value({"type": "managed_input"}, "owned-id")


def test_selected_request_rejects_a_reference_before_upstream_dispatch():
    from flamoris_studio.app import WorkflowImageRequest
    from test_studio import image_request
    with pytest.raises(ValueError):
        WorkflowImageRequest(**image_request(), workflowId="text-to-image", workflowKind="builtin",
                             referenceInputId="00000000-0000-0000-0000-000000000001")


@pytest.mark.parametrize("field,value", [("image", []), ("readiness", None), ("parameters", [])])
def test_malformed_catalog_entries_do_not_break_discovery(field, value):
    raw = descriptor()
    raw[field] = value
    assert normalize_catalog({"descriptors": [raw]}) == []


def test_wrong_role_type_and_noncanonical_identity_never_selectable():
    raw = descriptor()
    raw["parameters"]["w"]["type"] = "string"
    raw["parameters"]["w"].pop("multiple_of")
    assert not normalize_catalog({"descriptors": [raw]})[0]["selectable"]
    raw = descriptor()
    raw["definition_digest"] = raw["readiness"]["definition_digest"] = "unknown"
    assert not normalize_catalog({"descriptors": [raw]})[0]["selectable"]


def test_selected_request_scalar_types_are_exact():
    from flamoris_studio.app import WorkflowImageRequest
    from test_studio import image_request
    raw = {**image_request(), "workflowId": "text-to-image", "workflowKind": "builtin"}
    for key in ("width", "height", "steps", "cfg", "seed", "denoise"):
        with pytest.raises(ValueError):
            WorkflowImageRequest(**{**raw, key: True})


@pytest.mark.parametrize("controls", [{}, {"steps": 11, "cfg": 4.5}, {"sampler": "euler", "scheduler": "normal", "denoise": 0.5}])
def test_selected_request_accepts_omitted_controls_and_maps_only_descriptor_roles(controls):
    from flamoris_studio.app import WorkflowImageRequest

    item = normalize_catalog({"descriptors": [descriptor()]})[0]
    item["parameters"]["count"] = {"type": "integer", "role": "steps", "required": False, "default": 12}
    raw = {"workflowId": item["id"], "workflowKind": "builtin", "positivePrompt": "x", "checkpoint": "model",
           "width": 512, "height": 512, **controls}
    values = WorkflowImageRequest(**raw).model_dump(mode="json", exclude_none=True)
    params = map_parameters(item, values)
    assert params["count"] == controls.get("steps", 12)
    assert set(params) == {"model_key", "prompt_key", "w", "h", "random", "count"}


def test_omitted_required_descriptor_control_is_rejected():
    from flamoris_studio.app import WorkflowImageRequest

    item = normalize_catalog({"descriptors": [descriptor()]})[0]
    item["parameters"]["count"] = {"type": "integer", "role": "steps", "required": True}
    raw = {"workflowId": item["id"], "workflowKind": "builtin", "positivePrompt": "x", "checkpoint": "model", "width": 512, "height": 512}
    values = WorkflowImageRequest(**raw).model_dump(mode="json", exclude_none=True)
    with pytest.raises(ValueError):
        map_parameters(item, values)


@pytest.mark.parametrize("role,value", [("cfg", 4), ("denoise", 1)])
@pytest.mark.parametrize("floating_enum", [False, True])
def test_number_enum_roles_survive_request_normalization(role, value, floating_enum):
    from flamoris_studio.app import WorkflowImageRequest

    raw = descriptor()
    raw["parameters"]["control"] = {"type": "number", "role": role, "enum": [float(value) if floating_enum else value]}
    item = normalize_catalog({"descriptors": [raw]})[0]
    request = WorkflowImageRequest(workflowId=item["id"], workflowKind="builtin", positivePrompt="x", checkpoint="model",
                                   width=512, height=512, **{role: value})
    params = map_parameters(item, request.model_dump(mode="json", exclude_none=True))
    assert params["control"] == value


@pytest.mark.parametrize("value", [4, 4.0])
@pytest.mark.parametrize("candidate", [4, 4.0])
def test_number_enums_share_generation_json_semantics(candidate, value):
    assert validate_value({"type": "number", "enum": [candidate]}, value) == 4


@pytest.mark.parametrize("kind,value", [("number", True), ("number", "4"), ("number", 5), ("integer", 4.0), ("integer", True)])
def test_numeric_enum_type_and_domain_rejections(kind, value):
    with pytest.raises(ValueError):
        validate_value({"type": kind, "enum": [4]}, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow_id", [None, True, 7, "", "../private", "a" * 33, "g" * 32, {"id": "a" * 32}])
async def test_selected_build_rejects_missing_or_invalid_workflow_id(monkeypatch, workflow_id):
    from flamoris_studio.gateway import GenerationGateway, GatewayError

    item = normalize_catalog({"descriptors": [descriptor()]})[0]
    gateway = GenerationGateway()

    async def result(name, args):
        return {**args, **({"workflow_id": workflow_id} if workflow_id is not None else {})}

    monkeypatch.setattr(gateway, "_json", result)
    with pytest.raises(GatewayError) as error:
        await gateway.build_selected(item, {"model_key": "model"})
    assert error.value.code == "validation"


@pytest.mark.asyncio
async def test_selected_build_preserves_builtin_contract_and_valid_id(monkeypatch):
    from flamoris_studio.gateway import GenerationGateway

    item = normalize_catalog({"descriptors": [descriptor()]})[0]
    gateway = GenerationGateway()

    async def result(name, args):
        assert name == "workflows.build"
        assert set(args) == {"template", "parameters"}
        return {**args, "workflow_id": "a" * 32, "schema_version": 1}

    monkeypatch.setattr(gateway, "_json", result)
    assert await gateway.build_selected(item, {"model_key": "model"}) == "a" * 32
