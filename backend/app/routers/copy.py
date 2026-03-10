from fastapi import APIRouter

router = APIRouter()


@router.post("")
async def create_copy():
    # TODO: implement copy workflow
    return {"message": "not implemented"}
