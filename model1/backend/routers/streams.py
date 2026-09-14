import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from jose import jwt

# Adjust these imports if your auth dependency is located elsewhere in Model 1
from auth import get_current_user

router = APIRouter(tags=["Streams"])

SECRET_KEY = os.getenv("SECRET_KEY", "insecure-dev-key-change-me")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

@router.post("/{camera_id}/ticket")
def create_stream_ticket(camera_id: str, current_user = Depends(get_current_user)):
    """Generates a 30-second disposable ticket for video streaming."""
    expires = datetime.now(timezone.utc) + timedelta(seconds=30)
    payload = {
        "sub": str(current_user.id),
        "aud": "stream_ticket", 
        "cam": camera_id,
        "exp": expires
    }
    ticket = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return {"ticket": ticket}
