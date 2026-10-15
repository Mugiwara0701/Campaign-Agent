from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Is the gateway up? No token, no database: it says nothing about the office machine (see GET /v1/ca for that)."""
    return {"status": "ok"}
