"""API route modules."""

from fastapi import FastAPI


def install_business_routes(app: FastAPI, *, identity: bool = False) -> None:
    from agentic_graphrag.api.public_schema import ERROR_STATUSES, ErrorEnvelope, install_contract
    from agentic_graphrag.api.routes import (
        admin,
        knowledge,
        knowledge_bases,
        model_profiles,
        query,
        retrieval,
        review_advice,
        schemas,
        summaries,
        workspace,
    )

    for module in (
        query,
        knowledge,
        workspace,
        admin,
        knowledge_bases,
        model_profiles,
        retrieval,
        review_advice,
        summaries,
        schemas,
    ):
        app.include_router(
            module.router,
            responses={status: {"model": ErrorEnvelope} for status in ERROR_STATUSES},
        )
    if identity:
        app.include_router(workspace.identity_router)
    install_contract(app)
