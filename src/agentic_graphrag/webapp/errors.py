"""Safe Web envelopes: no passwords, cookies or reset tokens in validation logs."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agentic_graphrag.api.envelope import fail
from agentic_graphrag.app.errors import ApiError


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def expected(_request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=fail(error.code, error.message, details=error.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation(_request: Request, error: RequestValidationError) -> JSONResponse:
        fields = {str(row["loc"][-1]): "invalid_value" for row in error.errors() if row["loc"]}
        return JSONResponse(
            status_code=422,
            content=fail(
                "INVALID_INPUT",
                "Check the highlighted fields",
                details={"field_errors": fields},
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected(_request: Request, error: Exception) -> JSONResponse:
        logging.getLogger(__name__).error("Web request failed: %s", type(error).__name__)
        return JSONResponse(
            status_code=500, content=fail("INTERNAL_ERROR", "Internal server error")
        )
