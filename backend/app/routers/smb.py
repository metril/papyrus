from fastapi import APIRouter

router = APIRouter()


@router.get("/shares")
async def list_shares():
    # TODO: implement SMB share listing
    return {"shares": []}
