from pydantic import BaseModel, ConfigDict, Field

class ToolArgs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

class Fatigue(ToolArgs):
    reason: str = Field(min_length=1)
    evidence: str = Field(min_length=1)

class EndConversationArgs(ToolArgs):
    reason: str = Field(min_length=1)
    summary_conversation:str = Field(min_length=1)
    fatigue: Fatigue | None = None

class DriverEventRequest(BaseModel):
    call_id: str | None = None
    driver_full_name: str | None = None
    unit_number: str | None = None
    company: str | None = None
    cdl: str = Field(..., min_length=1, description="Driver CDL is required")
    phone_number: str | None = None
    event: str | None = None
    truck_speed: str | None = None
    speed_limit: str | None = None
    speeding_today: int | str | None = None
    datetime: str | None = None
    event_assessment: str | None = None
    payload_prompt_type: str | None = None
    ai_tone: str | None = Field(default=None, description="Tone/style for AI voice agent (e.g. 'polite', 'strict', 'friendly')")

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )

    def to_gemini_payload(self) -> str:
        if "DASH_CAM_EVENT_PROMPT" in self.payload_prompt_type:
            lines = ["--- SAFETY EVENT DETAILS ---"]
            if self.event:
                lines.append(f"Event: {self.event}")
            if self.driver_full_name:
                lines.append(f"Driver Name: {self.driver_full_name}")
            if self.cdl:
                lines.append(f"CDL: {self.cdl}")
            if self.unit_number:
                lines.append(f"Unit Number: {self.unit_number}")
            if self.company:
                lines.append(f"Company: {self.company}")
            if self.phone_number:
                lines.append(f"Phone Number: {self.phone_number}")
            if self.truck_speed:
                lines.append(f"Truck Speed: {self.truck_speed}")
            if self.speed_limit:
                lines.append(f"Speed Limit: {self.speed_limit}")
            if self.speeding_today is not None:
                lines.append(f"Speeding Violations Today: {self.speeding_today}")
            if self.datetime:
                lines.append(f"Event Datetime: {self.datetime}")
            if self.event_assessment:
                lines.append(f"Event Assessment: {self.event_assessment}")
            lines.append("----------------------------")
            return "\n".join(lines)

        
        if self.payload_prompt_type=="FATIGUE_SYSTEM_PROMPT":
            lines = ["--- SAFETY EVENT DETAILS ---"]
            if self.driver_full_name:
                lines.append(f"Driver Name: {self.driver_full_name}")
            if self.event_assessment:
                lines.append(f"Event Assessment: {self.event_assessment}")
            lines.append("----------------------------")
            return "\n".join(lines)


class CloseCallRequest(BaseModel):
    cdl: str = Field(..., min_length=1, description="Driver CDL is required")

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )

TOOL_DECLARATIONS = [
    {
        "name": "end_conversation",
        "description": (
            "End the conversation when the driver asks to stop, "
            "the check-in is complete, or fatigue is confirmed by driver. "
            "For confirmed fatigue, include its reason and evidence. "
            "Otherwise, set fatigue to null. Disconnects immediately."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "reason": {
                    "type": "STRING",
                    "description": "A short explanation of why the call is ending.",
                },
                "summary_conversation": {
                    "type": "STRING",
                    "description": "summary of conversation",
                },
                "fatigue": {
                    "type": "OBJECT",
                    "nullable": True,
                    "description": (
                        "Confirmed fatigue details, or null when "
                        "fatigue has not been confirmed."
                    ),
                    "properties": {
                        "reason": {
                            "type": "STRING",
                            "description": "Why fatigue was confirmed.",
                        },
                        "evidence": {
                            "type": "STRING",
                            "description": (
                                "The driver's relevant words or clear "
                                "audible observations supporting fatigue."
                            ),
                        },
                    },
                    "required": ["reason", "evidence"],
                },
            },
            "required": ["reason", "summary_conversation", "fatigue"],
        },
    },
]
