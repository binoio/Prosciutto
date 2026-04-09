import os
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv

from backend.db import create_db_and_tables, engine
from backend.routes import auth, accounts, messages, contacts, settings
from backend.services.gmail_service import check_new_messages_internal
from backend.services.notification_service import notify_all_subscriptions
from backend.models import NewMailNotification

# Initialize Limiter
limiter = Limiter(key_func=get_remote_address)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load .env file
from sqlmodel import Session, select
...
async def notification_worker():
    """Background worker to check for new messages and send push notifications."""
    logger.info("Starting notification worker...")
    while True:
        try:
            await asyncio.sleep(60) # Check every 60 seconds
            with Session(engine) as session:
                new_messages = await check_new_messages_internal(session)
                if new_messages:
                    logger.info(f"Found {len(new_messages)} new messages, sending push notifications.")
                    for msg in new_messages:
                        # Check if already notified
                        existing = session.exec(
                            select(NewMailNotification)
                            .where(NewMailNotification.message_id == msg.get("id"))
                            .where(NewMailNotification.account_id == msg.get("account_id"))
                        ).first()
                        if existing:
                            continue

                        # Save to DB for frontend polling
                        notification = NewMailNotification(
                            message_id=msg.get("id"),
                            account_id=msg.get("account_id"),
                            account_email=msg.get("account_email"),
                            subject=msg.get("subject"),
                            sender=msg.get("from")
                        )
                        session.add(notification)

                        push_data = {
                            "title": f"New Mail: {msg.get('subject', '(No Subject)')}",
                            "body": f"From: {msg.get('from')}\nAccount: {msg.get('account_email')}",
                            "id": msg.get("id"),
                            "account_id": msg.get("account_id")
                        }
                        await notify_all_subscriptions(push_data, session)
                    session.commit()

        except Exception as e:
            logger.error(f"Error in notification worker: {e}")
        except asyncio.CancelledError:
            logger.info("Notification worker cancelled.")
            break

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup database or other resources if needed
    if not os.getenv("TESTING"):
        create_db_and_tables()
    
    # Start background worker
    worker_task = None
    if not os.getenv("TESTING"):
        worker_task = asyncio.create_task(notification_worker())
    
    yield
    # Cleanup
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

app = FastAPI(title="Prosciutto", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Include Routers
app.include_router(auth)
app.include_router(accounts)
app.include_router(messages)
app.include_router(contacts)
app.include_router(settings)

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
