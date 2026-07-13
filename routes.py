from fastapi import APIRouter, Depends
from database import get_db

router = APIRouter()

@router.get("/data")
def get_data(db = Depends(get_db)):
    return {"message": "Success", "db_status": "Connected"}
