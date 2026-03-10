from fastapi import APIRouter

router = APIRouter()


@router.get("/providers")
async def list_providers():
    # TODO: implement cloud provider listing
    return {"providers": []}
