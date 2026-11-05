from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.read import router as read_router
from app.database import get_engine

app = FastAPI(title="Copernicus API")
app.include_router(read_router)


@app.exception_handler(SQLAlchemyError)
def database_error_handler(_request: Request, _error: SQLAlchemyError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Baza danych jest niedostępna."})


@app.get("/api/health")
def health() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        database = "ok"
    except SQLAlchemyError:
        database = "unavailable"
    return {"status": "ok", "database": database}
