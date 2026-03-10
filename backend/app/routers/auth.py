from fastapi import APIRouter

router = APIRouter()


@router.get("/me")
async def get_current_user():
    # TODO: implement OIDC auth
    return {"message": "not implemented"}
