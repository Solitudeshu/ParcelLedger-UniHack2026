from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Stage(StrEnum):
    DISPATCH = "DISPATCH"
    RECEIPT = "RECEIPT"
    SUPPLEMENT = "SUPPLEMENT"


class MatchStatus(StrEnum):
    MATCHED_SUFFICIENT = "MATCHED_SUFFICIENT"
    MATCHED_INSUFFICIENT = "MATCHED_INSUFFICIENT"
    NO_RELIABLE_MATCH = "NO_RELIABLE_MATCH"


Coordinate = Annotated[StrictInt, Field(ge=0, le=10_000)]
Point = tuple[Coordinate, Coordinate]
Polygon = Annotated[list[Point], Field(min_length=3, max_length=100)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Amount = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,77})$")]


class LoginRequest(Schema):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ShipmentCreate(Schema):
    reference: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    recipient_name: str = Field(min_length=1, max_length=120)
    reserve_amount: Amount = "0"
    notes: str = Field(default="", max_length=3000)

    @field_validator("reference", "title", "recipient_name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value must contain visible characters")
        return value


class CaptureCreate(Schema):
    stage: Stage


class UploadIntent(Schema):
    capture_session_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    byte_size: int = Field(gt=0, le=20 * 1024 * 1024)
    sha256: Sha256
    source: Literal["IN_APP_CAPTURE", "UPLOADED_ARCHIVE"] = "UPLOADED_ARCHIVE"
    parent_overview_id: UUID | None = None
    captured_at_claimed: datetime | None = None


class SealRequest(Schema):
    expected_version: int = Field(ge=1)


class AnalysisRequest(Schema):
    query_asset_id: UUID
    query_region: Polygon | None = None
    reference_asset_id: UUID | None = None

    @field_validator("query_region")
    @classmethod
    def nondegenerate_region(cls, value):
        if value:
            area = sum(
                value[i][0] * value[(i + 1) % len(value)][1]
                - value[(i + 1) % len(value)][0] * value[i][1]
                for i in range(len(value))
            )
            if abs(area) < 2:
                raise ValueError("Region must have positive area")
        return value


class MatchResult(Schema):
    query_asset_id: str
    reference_asset_id: str | None
    status: MatchStatus
    query_region: Polygon | None = None
    reference_region: Polygon | None = None
    match_score: float = Field(ge=0, le=1)
    sufficiency_score: float = Field(ge=0, le=1)
    score_kind: Literal["HEURISTIC", "CALIBRATED"]
    match_probability: float | None = Field(default=None, ge=0, le=1)
    sufficiency_probability: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[str]
    transform_type: Literal["LOCAL_PLANAR_HOMOGRAPHY"] | None = None
    transform: list[float] | None = None
    model_version: str
    checkpoint_sha256: Sha256
    artifact_kind: Literal["TRAINED_CHECKPOINT", "BASELINE_SOURCE"] = "TRAINED_CHECKPOINT"
    config_sha256: Sha256
    metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent_match(self):
        if self.status != MatchStatus.NO_RELIABLE_MATCH and not self.reference_asset_id:
            raise ValueError("A matched result requires a reference asset")
        if self.transform is not None and len(self.transform) != 9:
            raise ValueError("A homography contains nine coefficients")
        if self.score_kind == "HEURISTIC" and (
            self.match_probability is not None or self.sufficiency_probability is not None
        ):
            raise ValueError("Heuristic scores must not be represented as probabilities")
        return self


class Observation(Schema):
    id: str
    match_id: str | None = None
    model_run_id: str | None = None
    state: Literal["VISIBLE_CHANGE", "NO_VISIBLE_CHANGE", "INCONCLUSIVE"]
    type: str = "SURFACE_CONDITION"
    text: str = Field(max_length=3000)
    query_polygon: Polygon | None = None
    evidence_asset_ids: list[str] = Field(min_length=1)
    author_type: Literal["MODEL", "HUMAN"] = "MODEL"
    review_status: Literal["PENDING", "ACCEPTED", "EDITED"] = "PENDING"


class ReportEdit(Schema):
    expected_version: int = Field(ge=1)
    observations: list[Observation] = Field(max_length=50)
    user_notes: str = Field(default="", max_length=5000)


class ReportSignature(Schema):
    method: Literal["DRAWN", "TYPED"]
    name: str = Field(min_length=1, max_length=120)
    strokes: list[Annotated[list[Point], Field(min_length=2, max_length=512)]] = Field(
        default_factory=list, max_length=32
    )

    @model_validator(mode="after")
    def check_mark(self):
        self.name = self.name.strip()
        if not self.name or any(not char.isprintable() for char in self.name):
            raise ValueError("A visible signer name is required")
        if self.method == "DRAWN" and not any(len(set(line)) > 1 for line in self.strokes):
            raise ValueError("A drawn signature requires a stroke")
        if self.method == "TYPED" and self.strokes:
            raise ValueError("Typed signatures contain only a name")
        return self


class ReviewConfirmation(Schema):
    expected_version: int = Field(ge=1)
    report_hash: Sha256
    signature: ReportSignature | None = None


class InvitationClaim(Schema):
    token: str = Field(min_length=32, max_length=128)


class DemoLogin(Schema):
    role: Literal["MERCHANT", "BUYER"]


class ApprovalCreate(Schema):
    expected_version: int = Field(ge=1)
    report_hash: Sha256
    wallet_address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]+$", max_length=8194)
    expires_at: int = Field(gt=0)


class ApiError(Schema):
    code: str
    message: str
    retryable: bool = False
    correlation_id: str


class UserView(Schema):
    id: str
    name: str
    email: str
    organization_id: str


class SessionView(Schema):
    user: UserView
    csrf_token: str


class ShipmentView(Schema):
    id: str
    case_id: str
    reference: str
    title: str
    recipient_name: str
    reserve_amount: str
    notes: str
    status: str
    version: int
    report_version: int
    current_role: Literal["MERCHANT", "BUYER"]
    can_confirm: bool
    recipient_linked: bool
    created_at: datetime
    updated_at: datetime


class AssetView(Schema):
    id: str
    shipment_id: str
    capture_session_id: str
    stage: str
    filename: str
    parent_overview_id: str | None = None
    status: str
    sha256: str
    width: int | None
    height: int | None
    quality: dict
    created_at: datetime


class JobView(Schema):
    id: str
    shipment_id: str
    status: str
    progress: str
    attempts: int
    result: "AnalysisOutput | None"
    error: dict | None
    created_at: datetime


class Candidate(Schema):
    asset_id: str
    score: float
    geometry_valid: bool


class AnalysisOutput(Schema):
    match: MatchResult
    observations: list[Observation]
    change_metrics: dict[str, float]
    candidates: list[Candidate]
    inference_kind: Literal["UNTRAINED_BASELINE", "TRAINED_MODEL"]
    research_advice: dict | None = None
    research_change: dict | None = None
    research_transformer: dict | None = None
    report_composition: dict | None = None
    recapture_instructions: list[dict] = Field(default_factory=list)
    report_id: str | None = None
    report_version: int | None = None


JobView.model_rebuild()
