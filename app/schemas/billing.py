from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Literal


class CheckoutRequest(BaseModel):
    plan_id: Literal["starter", "creator", "scale", "creator_m2", "scale_m2"]
    success_url: str
    cancel_url: str


class CheckoutResponse(BaseModel):
    url: str


class PortalResponse(BaseModel):
    url: str


class BillingUsageResponse(BaseModel):
    credit_balance: int
    monthly_quota: Optional[int] = None
    plan_tier: str
    user_type: str
    videos_generated: int
    reset_date: Optional[datetime] = None


class CreditTransactionResponse(BaseModel):
    id: str
    amount: int
    type: str
    video_job_id: Optional[str] = None
    created_at: datetime
