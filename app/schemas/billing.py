from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class CheckoutRequest(BaseModel):
    """Start a subscription. plan_id is validated against PLANS in the route so
    the catalog stays config-driven (adding a plan needs no schema change)."""
    plan_id: str
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class TopupRequest(BaseModel):
    """Buy a one-off Pay-As-You-Go credit pack (those credits never expire)."""
    pack_id: str
    success_url: Optional[str] = None


class CheckoutResponse(BaseModel):
    url: str


class PortalResponse(BaseModel):
    url: str


class PlanResponse(BaseModel):
    id: str
    name: str
    price_ngn: int
    credits_per_month: int   # 1 credit = 1 minute of finished video


class TopupPackResponse(BaseModel):
    id: str
    credits: int
    price_ngn: int


class BillingUsageResponse(BaseModel):
    credit_balance: int          # plan_credits + topup_credits (what's spendable)
    plan_credits: int = 0        # monthly allowance left; resets, no rollover
    topup_credits: int = 0       # purchased credits; never expire
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
