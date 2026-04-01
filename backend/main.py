import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv

from backend.db import create_db_and_tables
from backend.routes import auth, accounts, messages, contacts, settings

# Initialize Limiter
limiter = Limiter(key_func=get_remote_address)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load .env file
load_dotenv() 

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup database or other resources if needed
    if not os.getenv("TESTING"):
        create_db_and_tables()
    yield
    # Cleanup

app = FastAPI(title="Prosciutto", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Include Routers
app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(messages.router)
app.include_router(contacts.router)
app.include_router(settings.router)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/")
@limiter.limit("5/minute")
async def root(request: Request):
    return FileResponse(os.path.join(BASE_DIR, "../frontend/index.html"))

app.mount("/styles", StaticFiles(directory=os.path.join(BASE_DIR, "../frontend/styles")), name="styles")
app.mount("/js", StaticFiles(directory=os.path.join(BASE_DIR, "../frontend/js")), name="js")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
