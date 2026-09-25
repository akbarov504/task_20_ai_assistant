import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Set

from fastapi import FastAPI, HTTPException, status
import uvicorn

from app.config import Config
from app.auth import CallAuth
from app.client import run_client, get_active_clients
from app.tools import DriverEventRequest
from app.service.cloud_service import get_cloud_service
from app.db.session import init_db
from app.db.repositories import DBRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("safety_ops_service")

base_cfg = Config()
shared_auth = CallAuth(base_cfg)
active_call_tasks: Set[asyncio.Task] = set()

async def sweep_pending_recordings():
    try:
        count = await get_cloud_service().upload_pending(base_cfg.recordings_dir)
        if count:
            logger.info(f"Startup sweep: uploaded and removed {count} leftover recording(s).")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Startup sweep of pending recordings failed")

@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = [
        name
        for name, value in {
            "CALL_SUPPORT_URL": base_cfg.call_support_url,
            "CALL_SERVICE_URL": base_cfg.call_service_url,
            "CALL_WSS_URL": base_cfg.call_wss_url,
            "CALL_AUTH_USERNAME": base_cfg.auth_username,
            "CALL_AUTH_PASSWORD": base_cfg.auth_password,
            "GEMINI_API_KEY": base_cfg.gemini_api_key,
        }.items()
        if not value
    ]

    if missing:
        logger.warning(
            f"Missing configuration values in .env: {', '.join(missing)}. Please ensure .env is properly configured."
        )

    try:
        await init_db()
    except Exception as db_exc:
        logger.warning(f"Database initialization warning: {db_exc}")

    logger.info("FastAPI service started. Ready to accept events via POST /api/event.")
    sweep_task = asyncio.create_task(sweep_pending_recordings())

    yield
    sweep_task.cancel()

    logger.info(f"Shutting down service. Cancelling {len(active_call_tasks)} active call tasks...")
    for task in list(active_call_tasks):
        task.cancel()

    if active_call_tasks:
        await asyncio.gather(*active_call_tasks, return_exceptions=True)
    logger.info("All call tasks shut down.")

app = FastAPI(
    title="Gemini WebRTC Safety Call Service",
    description="REST API service to trigger and manage AI voice safety check-in calls to truck drivers via WebRTC with automated call analysis, database logging, and webhook callbacks.",
    version="2.0.0",
    lifespan=lifespan,
)

@app.get("/")
@app.get("/health")
async def health_check():
    active_clients = get_active_clients()
    return {
        "status": "ok",
        "active_tasks": len(active_call_tasks),
        "active_calls_count": len(active_clients),
        "active_cdls": [client.cfg.driver_cdl for client in active_clients],
    }

@app.post("/api/event", status_code=status.HTTP_202_ACCEPTED)
async def handle_safety_event(event_req: DriverEventRequest):
    cdl = event_req.cdl
    if not cdl or not cdl.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Field 'cdl' is required and cannot be empty.",
        )

    clean_cdl = cdl.strip()
    call_id = event_req.call_id or f"{clean_cdl}_{int(time.time())}"

    event_payload = event_req.to_gemini_payload()
    try:
        client_cfg = base_cfg.with_event(
            driver_cdl=clean_cdl,
            payload=event_payload,
            payload_prompt_type=event_req.payload_prompt_type,
            call_id=call_id,
            ai_tone=event_req.ai_tone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    db_event = await DBRepository.create_event(
        cdl=clean_cdl,
        call_id=call_id,
        driver_full_name=event_req.driver_full_name,
        unit_number=event_req.unit_number,
        company=event_req.company,
        phone_number=event_req.phone_number,
        event_type=event_req.event,
        truck_speed=event_req.truck_speed,
        speed_limit=event_req.speed_limit,
        speeding_today=event_req.speeding_today if isinstance(event_req.speeding_today, int) else None,
        event_datetime=event_req.datetime,
        event_assessment=event_req.event_assessment,
        payload_prompt_type=event_req.payload_prompt_type,
        raw_payload=event_req.model_dump(),
        status="RECEIVED",
    )

    await DBRepository.create_call(
        call_id=call_id,
        cdl=clean_cdl,
        event_id=db_event.id if db_event else None,
        call_type=client_cfg.call_type,
        status="INITIATED",
        gemini_model=client_cfg.gemini_model,
    )

    task = asyncio.create_task(run_client(client_cfg, auth=shared_auth))
    active_call_tasks.add(task)
    task.add_done_callback(active_call_tasks.discard)

    logger.info(
        f"Triggered call for driver CDL={clean_cdl}, call_id='{call_id}', event='{event_req.event}'. "
        f"Active calls: {len(active_call_tasks)}"
    )

    return {
        "status": "accepted",
        "message": "Call initiated for driver",
        "call_id": call_id,
        "cdl": clean_cdl,
        "event": event_req.event,
        "active_calls_count": len(active_call_tasks),
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8787, workers=2, limit_concurrency=400)
