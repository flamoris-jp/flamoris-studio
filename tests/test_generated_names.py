import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from flamoris_studio.app import create_app
from flamoris_studio.db import Asset
from test_studio import clients, image_request, register


def test_duplicate_provider_names_have_stable_distinct_catalog_and_download_names(clients):
    a, b, gateway, factory = clients
    csrf = register(a, "names@example.test")
    register(b, "other-names@example.test")

    async def outputs(job):
        return [{"asset_id": f"private-{index}", "filename": "000.png",
                 "mime_type": "image/png", "media_kind": "image",
                 "size_bytes": len(gateway.image)} for index in range(2)]

    gateway.assets = outputs
    made = a.post("/api/generation/image/jobs", json=image_request(),
                  headers={"X-CSRF-TOKEN": csrf}).json()
    path = f"/api/executions/{made['id']}/result"
    assets = a.get(path).json()["assets"]
    names = [item["displayName"] for item in assets]
    assert len(set(names)) == 2
    assert all(re.fullmatch(r"image_\d{8}_\d{6}_0[12]_[a-f0-9]{12}\.png", name) for name in names)
    assert a.get(path).json()["assets"] == assets
    for item in assets:
        assert a.get(item["downloadUrl"]).headers["Content-Disposition"] == (
            f'attachment; filename="{item["displayName"]}"')
        assert b.get(item["downloadUrl"]).status_code == 404
    with factory() as db:
        assert {item.original_filename for item in db.scalars(select(Asset))} == {"000.png"}
    with TestClient(create_app(factory, gateway, a.app.state.thumbnails)) as restarted:
        restarted.cookies.update(a.cookies)
        assert [item["displayName"] for item in restarted.get(path).json()["assets"]] == names


def test_existing_catalog_names_are_preserved(clients):
    a, _, _, factory = clients
    csrf = register(a, "old-names@example.test")
    made = a.post("/api/generation/image/jobs", json=image_request(),
                  headers={"X-CSRF-TOKEN": csrf}).json()
    path = f"/api/executions/{made['id']}/result"
    a.get(path)
    with factory() as db:
        asset = db.scalar(select(Asset))
        asset.display_name = "legacy-name.png"
        db.commit()
    assert a.get(path).json()["assets"][0]["displayName"] == "legacy-name.png"
