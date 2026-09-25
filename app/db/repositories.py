from datetime import datetime
import logging
from typing import Optional, Dict, Any, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import async_session_factory
from app.db.models import Event, Call, CallAnalysis

logger = logging.getLogger("db.repositories")

class DBRepository:
    @staticmethod
    async def create_event(
        cdl: str,
        call_id: Optional[str] = None,
        driver_full_name: Optional[str] = None,
        unit_number: Optional[str] = None,
        company: Optional[str] = None,
        phone_number: Optional[str] = None,
        event_type: Optional[str] = None,
        truck_speed: Optional[str] = None,
        speed_limit: Optional[str] = None,
        speeding_today: Optional[int] = None,
        event_datetime: Optional[str] = None,
        event_assessment: Optional[str] = None,
        payload_prompt_type: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
        status: str = "RECEIVED",
    ) -> Optional[Event]:
        try:
            async with async_session_factory() as session:
                event = Event(
                    call_id=call_id,
                    cdl=cdl,
                    driver_full_name=driver_full_name,
                    unit_number=unit_number,
                    company=company,
                    phone_number=phone_number,
                    event_type=event_type,
                    truck_speed=truck_speed,
                    speed_limit=speed_limit,
                    speeding_today=int(speeding_today) if speeding_today is not None and str(speeding_today).isdigit() else None,
                    event_datetime=event_datetime,
                    event_assessment=event_assessment,
                    payload_prompt_type=payload_prompt_type,
                    raw_payload=raw_payload,
                    status=status,
                )
                session.add(event)
                await session.commit()
                await session.refresh(event)
                logger.info(f"[DB] Event created with ID={event.id}, cdl={cdl}, call_id={call_id}")
                return event
        except Exception as exc:
            logger.error(f"[DB] Failed to create Event in database: {exc}", exc_info=True)
            return None

    @staticmethod
    async def create_call(
        call_id: str,
        cdl: str,
        event_id: Optional[int] = None,
        call_type: str = "SPECIAL",
        status: str = "INITIATED",
        gemini_model: Optional[str] = None,
    ) -> Optional[Call]:
        try:
            async with async_session_factory() as session:
                call = Call(
                    event_id=event_id,
                    call_id=call_id,
                    cdl=cdl,
                    call_type=call_type,
                    status=status,
                    gemini_model=gemini_model,
                )
                session.add(call)
                await session.commit()
                await session.refresh(call)
                logger.info(f"[DB] Call created with ID={call.id}, call_id={call_id}")
                return call
        except Exception as exc:
            logger.error(f"[DB] Failed to create Call in database: {exc}", exc_info=True)
            return None

    @staticmethod
    async def update_call(
        call_id: str,
        **fields,
    ) -> Optional[Call]:
        try:
            async with async_session_factory() as session:
                stmt = select(Call).where(Call.call_id == call_id).order_by(Call.id.desc())
                result = await session.execute(stmt)
                call = result.scalars().first()

                if not call:
                    call = Call(call_id=call_id, cdl=fields.get("cdl", "unknown"))
                    session.add(call)

                for key, value in fields.items():
                    if hasattr(call, key) and value is not None:
                        setattr(call, key, value)

                await session.commit()
                await session.refresh(call)
                return call
        except Exception as exc:
            logger.error(f"[DB] Failed to update Call {call_id}: {exc}", exc_info=True)
            return None

    @staticmethod
    async def create_call_analysis(
        call_id: str,
        call_db_id: Optional[int] = None,
        provider: str = "gemini",
        model: Optional[str] = None,
        is_voice_call_empty: bool = False,
        language: Optional[str] = None,
        call_summary: Optional[str] = None,
        driver_tone: Optional[str] = None,
        assistant_tone: Optional[str] = None,
        fatigue_confirmed: bool = False,
        fatigue_reason: Optional[str] = None,
        fatigue_evidence: Optional[str] = None,
        sentiment: Optional[str] = None,
        conclusion: Optional[str] = None,
        conflict_present: bool = False,
        conflict_data: Optional[Dict[str, Any]] = None,
        open_issues: Optional[List[Any]] = None,
        solved_issues: Optional[List[Any]] = None,
        segments: Optional[List[Any]] = None,
        raw_response: Optional[Dict[str, Any]] = None,
        webhook_sent: bool = False,
        webhook_sent_at: Optional[datetime] = None,
        webhook_status_code: Optional[int] = None,
        webhook_response: Optional[str] = None,
    ) -> Optional[CallAnalysis]:
        try:
            async with async_session_factory() as session:
                if not call_db_id:
                    stmt = select(Call.id).where(Call.call_id == call_id).order_by(Call.id.desc())
                    res = await session.execute(stmt)
                    call_db_id = res.scalars().first()

                analysis = CallAnalysis(
                    call_id=call_id,
                    call_db_id=call_db_id,
                    provider=provider,
                    model=model,
                    is_voice_call_empty=is_voice_call_empty,
                    language=language,
                    call_summary=call_summary,
                    driver_tone=driver_tone,
                    assistant_tone=assistant_tone,
                    fatigue_confirmed=fatigue_confirmed,
                    fatigue_reason=fatigue_reason,
                    fatigue_evidence=fatigue_evidence,
                    sentiment=sentiment,
                    conclusion=conclusion,
                    conflict_present=conflict_present,
                    conflict_data=conflict_data,
                    open_issues=open_issues,
                    solved_issues=solved_issues,
                    segments=segments,
                    raw_response=raw_response,
                    webhook_sent=webhook_sent,
                    webhook_sent_at=webhook_sent_at,
                    webhook_status_code=webhook_status_code,
                    webhook_response=webhook_response,
                )
                session.add(analysis)
                await session.commit()
                await session.refresh(analysis)
                logger.info(f"[DB] CallAnalysis saved with ID={analysis.id} for call_id={call_id}")
                return analysis
        except Exception as exc:
            logger.error(f"[DB] Failed to create CallAnalysis in database: {exc}", exc_info=True)
            return None
