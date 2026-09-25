from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Any, Dict
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

class Base(DeclarativeBase):
    pass

JsonType = JSON().with_variant(JSONB, "postgresql")

class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    cdl: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    driver_full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    unit_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    company: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    event_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    truck_speed: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    speed_limit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    speeding_today: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    event_datetime: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    event_assessment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload_prompt_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    raw_payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JsonType, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="RECEIVED", index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    calls: Mapped[List[Call]] = relationship("Call", back_populates="event", cascade="all, delete-orphan")

class Call(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    call_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    server_call_id: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    cdl: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    call_type: Mapped[str] = mapped_column(String(32), default="SPECIAL")
    status: Mapped[str] = mapped_column(String(64), default="INITIATED", index=True)
    end_reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    driver_answered: Mapped[bool] = mapped_column(Boolean, default=False)

    ringing_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    talk_duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)

    recording_file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    recording_gcs_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recording_signed_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    gemini_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    gemini_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    gemini_tokens: Mapped[Optional[Dict[str, Any]]] = mapped_column(JsonType, nullable=True)

    end_conversation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    end_conversation_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    end_conversation_fatigue: Mapped[Optional[Dict[str, Any]]] = mapped_column(JsonType, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    event: Mapped[Optional[Event]] = relationship("Event", back_populates="calls")
    analyses: Mapped[List[CallAnalysis]] = relationship("CallAnalysis", back_populates="call", cascade="all, delete-orphan")

class CallAnalysis(Base):
    __tablename__ = "call_analyses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    call_db_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("calls.id", ondelete="CASCADE"), nullable=True, index=True
    )

    provider: Mapped[str] = mapped_column(String(64), default="gemini")
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_voice_call_empty: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    call_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    driver_tone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    assistant_tone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    fatigue_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    fatigue_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fatigue_evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    sentiment: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    conclusion: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    conflict_present: Mapped[bool] = mapped_column(Boolean, default=False)
    conflict_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JsonType, nullable=True)

    open_issues: Mapped[Optional[List[Any]]] = mapped_column(JsonType, nullable=True)
    solved_issues: Mapped[Optional[List[Any]]] = mapped_column(JsonType, nullable=True)
    segments: Mapped[Optional[List[Any]]] = mapped_column(JsonType, nullable=True)
    raw_response: Mapped[Optional[Dict[str, Any]]] = mapped_column(JsonType, nullable=True)

    webhook_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    webhook_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    webhook_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    call: Mapped[Optional[Call]] = relationship("Call", back_populates="analyses")
