from fastapi import APIRouter

router = APIRouter()


@router.get("/status")
async def get_scanner_status():
    # TODO: implement SANE status
    return {"message": "not implemented"}
