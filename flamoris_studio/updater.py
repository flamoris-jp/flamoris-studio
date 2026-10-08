"""Studio owns accounts, grants, request fences and its Alembic lineage."""

from flamoris_update_core.domain import check_resources, configuration_revision
from flamoris_update_core.errors import UpdateError
from flamoris_update_core.owner import ApplicationOwner, DomainState
from flamoris_update_core.owner_cli import serve

SCHEMAS = {"configuration": "studio-config-1", "database": "20261005_12"}


def inspect_domain(config, resources):
    check_resources(resources, ["configuration"], ["database"])
    database = resources["database"]
    if database.binding.schemas != ["public"]:
        raise UpdateError("invalid_profile")
    with database.connect() as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        if conn.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchall() != [("20261005_12",)]:
            raise UpdateError("unsupported_migration")
        uncertain = bool(
            conn.execute(
                "SELECT 1 FROM public.assistant_requests WHERE state NOT IN ('completed','rejected') "
                "UNION ALL SELECT 1 FROM public.assistant_model_switches WHERE state NOT IN ('completed','rejected') "
                "UNION ALL SELECT 1 FROM public.intelligence_requests WHERE state NOT IN ('completed','rejected') "
                "UNION ALL SELECT 1 FROM public.managed_inputs WHERE state NOT IN ('live','revoked') "
                "UNION ALL SELECT 1 FROM public.executions WHERE last_known_status NOT IN ('completed','failed','cancelled') LIMIT 1"
            ).fetchone()
        )
    return DomainState(
        schemas=SCHEMAS,
        active_work=uncertain,
        unknown_work=uncertain,
        configuration_digest=configuration_revision(resources),
    )


def factory(config):
    return ApplicationOwner(config, "flamoris-studio", "1.0.0", inspect_domain)


def main():
    serve(factory)
