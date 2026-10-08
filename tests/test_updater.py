from contextlib import nullcontext

import pytest
from flamoris_update_core.errors import UpdateError
from flamoris_update_core.postgres import PostgresBinding, PostgresResource
from flamoris_update_core.resources import TreeBinding, TreeResource

from flamoris_studio import updater


def tree(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return TreeResource(
        TreeBinding(id=name, path=str(path), max_files=20, max_bytes=4096)
    )


class ReadOnlyDatabase(PostgresResource):
    def __init__(self, history, uncertain):
        super().__init__(
            PostgresBinding(
                id="database",
                dsn_file="/unused",
                schemas=["public"],
                writer_roles=["writer"],
                pg_bin="/unused",
                max_bytes=4096,
            )
        )
        self.history, self.uncertain, self.statements = history, uncertain, []

    def connect(self):
        return nullcontext(self)

    def transaction(self):
        return nullcontext(self)

    def execute(self, sql):
        self.statements.append(sql)
        return self

    def fetchall(self):
        return [(item,) for item in self.history]

    def fetchone(self):
        return (1,) if self.uncertain else None


@pytest.mark.parametrize("uncertain", [False, True])
def test_owner_retains_history_and_unknown_requests(tmp_path, uncertain):
    history = ["20261005_12"]
    database = ReadOnlyDatabase(history, uncertain)
    configuration = tree(tmp_path, "configuration")
    thumbnails = tree(tmp_path, "thumbnails")
    (thumbnails.root / "retained.webp").write_bytes(b"thumbnail")
    before = {
        "configuration": configuration.inventory(),
        "thumbnails": thumbnails.inventory(),
    }
    state = updater.inspect_domain(
        None,
        {
            "configuration": configuration,
            "database": database,
            "thumbnails": thumbnails,
        },
    )
    assert state.unknown_work is uncertain
    assert state.active_work is uncertain
    assert database.history == history
    assert configuration.inventory() == before["configuration"]
    assert thumbnails.inventory() == before["thumbnails"]
    assert database.statements[0] == "SET TRANSACTION READ ONLY"
    assert all(
        statement.startswith(("SET TRANSACTION READ ONLY", "SELECT "))
        for statement in database.statements
    )


def test_owner_rejects_unreviewed_schema_history(tmp_path):
    database = ReadOnlyDatabase(["older-schema"], False)
    with pytest.raises(UpdateError) as error:
        updater.inspect_domain(
            None,
            {
                "configuration": tree(tmp_path, "configuration"),
                "database": database,
                "thumbnails": tree(tmp_path, "thumbnails"),
            },
        )
    assert error.value.code == "unsupported_migration"
    assert len(database.statements) == 2


def test_owner_requires_thumbnail_storage(tmp_path):
    database = ReadOnlyDatabase(["20261005_12"], False)
    with pytest.raises(UpdateError) as error:
        updater.inspect_domain(
            None,
            {"configuration": tree(tmp_path, "configuration"), "database": database},
        )
    assert error.value.code == "invalid_profile"
