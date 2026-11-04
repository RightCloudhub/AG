"""Agent advice is separate from authoritative human review and Schema approval."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.api.envelope import TypedEnvelope, ok
from agentic_graphrag.api.rbac import Role, require_role
from agentic_graphrag.api.routes.deps import knowledge_service, principal
from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.app.model_metadata import role_revision
from agentic_graphrag.app.review_advice import advice_store, advise, review_item, save_calibration
from agentic_graphrag.app.review_app import ReviewApp
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.review_advice import ReviewAdvice, ReviewCalibration

router = APIRouter(
    prefix="/v1",
    tags=["review-advice"],
    dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))],
)


class AdviceResult(BaseModel):
    advice: ReviewAdvice
    automation: str


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(min_length=1, max_length=200)
    review_ids: list[str] = Field(min_length=1, max_length=10000)
    real_corpus: bool = False
    held_out: bool = False


@router.post("/review-queue/{review_id}/retry", operation_id="retry_review_execution")
def retry_execution(review_id: str, request: Request) -> JsonObject:
    tenant, _ = principal(request)
    try:
        item = ReviewApp(knowledge_service(request)).retry(review_id, tenant_id=tenant)
    except KeyError as exc:
        raise ApiError(INVALID_INPUT, "Unknown review item", status_code=404) from exc
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=409) from exc
    return ok(item.to_dict())


@router.post(
    "/review-queue/{review_id}/advice",
    response_model=TypedEnvelope[AdviceResult],
    operation_id="generate_review_advice",
)
def generate_advice(review_id: str, request: Request) -> JsonObject:
    try:
        advice, automation = advise(knowledge_service(request), review_id)
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=409) from exc
    return ok(AdviceResult(advice=advice, automation=automation).model_dump(mode="json"))


@router.get(
    "/review-queue/{review_id}/advice",
    response_model=TypedEnvelope[ReviewAdvice],
    operation_id="get_review_advice",
)
def get_advice(review_id: str, request: Request) -> JsonObject:
    svc = knowledge_service(request)
    review_item(svc, review_id)
    assert svc.knowledge_base is not None
    with advice_store(svc) as store:
        result = store.get(svc.knowledge_base.scope, review_id)
    if result is None:
        raise ApiError(INVALID_INPUT, "No advice for this review", status_code=404)
    return ok(result.model_dump(mode="json"))


@router.post(
    "/review-calibration",
    response_model=TypedEnvelope[ReviewCalibration],
    operation_id="calibrate_review_advice",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def calibrate_advice(body: CalibrationRequest, request: Request) -> JsonObject:
    svc = knowledge_service(request)
    assert svc.knowledge_base is not None
    baseline = ReviewCalibration(
        scope=svc.knowledge_base.scope,
        dataset_id=body.dataset_id,
        proposer_revision=role_revision(svc, ModelRole.EXTRACTOR),
        verifier_revision=role_revision(svc, ModelRole.VERIFIER),
        real_corpus=body.real_corpus,
        held_out=body.held_out,
        reviewed=0,
        approved=0,
        false_approvals=0,
        false_approval_upper=1.0,
    )
    try:
        result = save_calibration(svc, baseline, review_ids=body.review_ids)
    except ValueError as exc:
        raise ApiError(INVALID_INPUT, str(exc), status_code=409) from exc
    return ok(result.model_dump(mode="json"))


@router.get(
    "/review-calibration",
    response_model=TypedEnvelope[ReviewCalibration | None],
    operation_id="get_review_calibration",
)
def get_calibration(request: Request) -> JsonObject:
    svc = knowledge_service(request)
    assert svc.knowledge_base is not None
    with advice_store(svc) as store:
        result = store.get_calibration(svc.knowledge_base.scope)
    return ok(result.model_dump(mode="json") if result else None)
