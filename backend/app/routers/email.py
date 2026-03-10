from fastapi import APIRouter

router = APIRouter()


@router.get("/config")
async def get_email_config():
    # TODO: implement email config
    return {"configured": False, "smtp_host": None, "smtp_from": None}
