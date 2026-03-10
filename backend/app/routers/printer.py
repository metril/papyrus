from fastapi import APIRouter

router = APIRouter()


@router.get("/status")
async def get_printer_status():
    # TODO: implement CUPS status
    return {"message": "not implemented"}
