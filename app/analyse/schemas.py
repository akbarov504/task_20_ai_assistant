# app/schemas.py
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FindingCategory = Literal[
    "HOS violation",
    "Certification gap",
    "Malfunction/diagnostic gap",
    "Data integrity anomaly",
    "Parsing uncertainty",
]

Severity = Literal["critical", "high", "medium", "low", "info"]
Confidence = Literal["high", "medium", "low"]
CheckStatus = Literal["passed", "failed", "not_evaluable", "not_present"]
Provider = Literal["openai", "anthropic", "gemini"]

class UploadResponse(BaseModel):
    provider: Provider
    file_id: str
    filename: str
    mime_type: str

class AskRequest(BaseModel):
    provider: Provider
    file_id: str
    filename: str = "document.pdf"
    mime_type: str = "application/pdf"
    model: str | None = None

class AskResponse(BaseModel):
    provider: Provider
    model: str | None
    answer: VoiceAnalysisResult

# ---------- Common ----------

class VoiceSegment(BaseModel):
    speaker: str
    timestamp: str
    text: str
    language: str | None = None
    emotion: Literal["happy","sad","angry","neutral","mixed"]="neutral"

# class SpeakerRole(BaseModel):
#     speaker: str
#     role: Literal[
#         "driver","dispatcher","broker","shipper","receiver","facility",
#         "shop","safety","fleet","fuel","accounting","updater","staff",
#         "personal_contact","auto_response","unknown"
#     ]="unknown"
#     name: str|None=None

class ConflictAnalysis(BaseModel):
    present: bool=False
    started_at: str|None=None
    rude_party: str|None=None
    assessment: Literal[ "party_a_supported","party_b_supported",
    "both_partially_supported","unable_to_determine"]="unable_to_determine"
    escalated: bool=False 
    severity: Literal["none","low","medium","high","critical"]="none"
    evidence: list[str]=Field(default_factory=list)
    detail: str|None=None

class CommonAnalysis(BaseModel):
    sentiment: Literal["positive","neutral","negative","mixed"]="neutral"
    # speaker_roles:list[SpeakerRole]|None=None
    segments:list[VoiceSegment]=Field(default_factory=list) 
    conflict:ConflictAnalysis=Field(default_factory=ConflictAnalysis)
    open_issues:list[str]|None=None
    solved_issues:list[str]|None=Field(default_factory=list)
    conclusion:str=Field(default_factory=str) 

# -------- AI_driver----------

class Fatigue(BaseModel):
    reason:str
    evidence:str

class AIAssistantDriver(BaseModel):
    tone_driver:Literal["positive","neutral","negative","mixed", "agressive", "polite"]="neutral"
    tone_assitant: Literal["positive","neutral","negative","mixed", "agressive", "polite"]="neutral"
    fatigue:Fatigue|None=None

# ---------- Root ----------

class VoiceAnalysisResult(BaseModel):
    is_this_voice_call_empty:bool=False
    language:Literal["en","es","ru","uk","uz","fa","other","unknown"]="unknown"
    common:CommonAnalysis=Field(default_factory=CommonAnalysis)
    ai_assistant_driver:AIAssistantDriver|None
    call_summary:str
