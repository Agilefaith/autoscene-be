"""Reference-image upload (AutoScene character consistency).

Stores a user-provided reference image on Backblaze B2 and returns its URL. The
URL is saved on the project (reference_image_url) and later used to build a fixed
character description for best-effort consistency across scenes.
"""

import uuid

from fastapi import APIRouter, UploadFile, File, HTTPException, status

from app.core.deps import CurrentUserId
from app.services.backblaze import upload_bytes

router = APIRouter(prefix="/uploads", tags=["uploads"])

_ALLOWED = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_MAX_BYTES = 8 * 1024 * 1024  # 8 MB


@router.post("/reference")
async def upload_reference(user_id: CurrentUserId, file: UploadFile = File(...)):
    ext = _ALLOWED.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reference must be a PNG, JPG, or WEBP image.",
        )
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Reference image must be under 8 MB.",
        )
    key = f"references/{user_id}/{uuid.uuid4().hex}.{ext}"
    url = upload_bytes(data, key, file.content_type or f"image/{ext}")
    return {"url": url}
