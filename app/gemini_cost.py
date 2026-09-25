from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

# -------------------------------------------------------------------
# 1. Models & Pricing Configurations
# -------------------------------------------------------------------

class GeminiModel(str, Enum):
    # Legacy / Previous Models
    FLASH_2_5 = "gemini-2.5-flash"
    PRO_2_5 = "gemini-2.5-pro"
    FLASH_2_5_NATIVE_AUDIO = "gemini-2.5-flash-native-audio-preview-12-2025"
    
    # Updated / Requested Gemini Models
    FLASH_3_1_LIVE_PREVIEW = "gemini-3.1-flash-live-preview"
    LIVE_3_8 = "gemini-3.8-live"
    LIVE_3_8_EXTENDED_THINKING = "gemini-3.8-live-extended-thinking"


class PricingRates(BaseModel):
    """Rates specified in USD per 1 Million tokens."""
    text_input_per_1m: float
    audio_input_per_1m: float
    audio_output_per_1m: float


# Pricing rates per 1M tokens by model tier
MODEL_PRICING_TABLE: Dict[GeminiModel, PricingRates] = {
    # 2.5 Series
    GeminiModel.FLASH_2_5: PricingRates(
        text_input_per_1m=0.50,
        audio_input_per_1m=3.00,
        audio_output_per_1m=12.00,
    ),
    # default GEMINI_MODEL in config.py; same rates as 2.5 Flash - verify against the current price list
    GeminiModel.FLASH_2_5_NATIVE_AUDIO: PricingRates(
        text_input_per_1m=0.50,
        audio_input_per_1m=3.00,
        audio_output_per_1m=12.00,
    ),
    GeminiModel.PRO_2_5: PricingRates(
        text_input_per_1m=1.25,
        audio_input_per_1m=5.00,
        audio_output_per_1m=20.00,
    ),
    
    # 3.x Series (Live & Real-time variants)
    GeminiModel.FLASH_3_1_LIVE_PREVIEW: PricingRates(
        text_input_per_1m=0.75,
        audio_input_per_1m=3.00,
        audio_output_per_1m=4.50,
    ),
    GeminiModel.LIVE_3_8: PricingRates(
        text_input_per_1m=0.75,
        audio_input_per_1m=3.00,
        audio_output_per_1m=3.75,
    ),
    GeminiModel.LIVE_3_8_EXTENDED_THINKING: PricingRates(
        text_input_per_1m=0.75,
        audio_input_per_1m=3.00,
        audio_output_per_1m=3.75,  # Note: thoughtsTokenCount is billed under response output rates
    ),
}


# -------------------------------------------------------------------
# 2. Pydantic Models for Live Usage Metadata
# -------------------------------------------------------------------

class ModalityTokenDetail(BaseModel):
    modality: str
    token_count: int = Field(..., alias="tokenCount")


class TurnUsageMetadata(BaseModel):
    prompt_token_count: int = Field(0, alias="promptTokenCount")
    response_token_count: int = Field(0, alias="responseTokenCount")
    total_token_count: int = Field(0, alias="totalTokenCount")
    thoughts_token_count: Optional[int] = Field(0, alias="thoughtsTokenCount")

    prompt_tokens_details: List[ModalityTokenDetail] = Field(
        default_factory=list, alias="promptTokensDetails"
    )
    response_tokens_details: List[ModalityTokenDetail] = Field(
        default_factory=list, alias="responseTokensDetails"
    )

    model_config =ConfigDict(populate_by_name=True)

    @property
    def text_input_tokens(self) -> int:
        return sum(d.token_count for d in self.prompt_tokens_details if d.modality.upper() == "TEXT")

    @property
    def audio_input_tokens(self) -> int:
        return sum(d.token_count for d in self.prompt_tokens_details if d.modality.upper() == "AUDIO")

    @property
    def audio_output_tokens(self) -> int:
        return sum(d.token_count for d in self.response_tokens_details if d.modality.upper() == "AUDIO")

    @property
    def total_output_tokens(self) -> int:
        return (self.thoughts_token_count or 0) + self.audio_output_tokens


class GeminiLiveSessionTokens(BaseModel):
    model: GeminiModel = GeminiModel.FLASH_3_1_LIVE_PREVIEW
    turns: List[TurnUsageMetadata] = Field(default_factory=list)

    def add_turn(self, raw_usage_dict: dict) -> TurnUsageMetadata:
        turn_metadata = TurnUsageMetadata.model_validate(raw_usage_dict)
        self.turns.append(turn_metadata)
        return turn_metadata


# -------------------------------------------------------------------
# 3. Dynamic Cost Calculator
# -------------------------------------------------------------------

class GeminiCostCalculator:
    def __init__(self, custom_rates: Optional[Dict[GeminiModel, PricingRates]] = None):
        self.rates_table = custom_rates or MODEL_PRICING_TABLE

    def _get_rates(self, model: GeminiModel) -> PricingRates:
        if model not in self.rates_table:
            raise ValueError(f"Pricing model '{model}' not configured in rates table.")
        return self.rates_table[model]

    def calculate_turn_cost(
        self, turn: TurnUsageMetadata, model: GeminiModel
    ) -> dict[str, float]:
        rates = self._get_rates(model)

        text_in_cost = (turn.text_input_tokens / 1_000_000) * rates.text_input_per_1m
        audio_in_cost = (turn.audio_input_tokens / 1_000_000) * rates.audio_input_per_1m
        output_cost = (turn.total_output_tokens / 1_000_000) * rates.audio_output_per_1m

        return {
            "model": model.value,
            "text_input_cost": round(text_in_cost, 6),
            "audio_input_cost": round(audio_in_cost, 6),
            "output_cost": round(output_cost, 6),
            "total_turn_cost": round(text_in_cost + audio_in_cost + output_cost, 6),
        }

    def calculate_session_cost(self, session: GeminiLiveSessionTokens) -> dict[str, float]:
        rates = self._get_rates(session.model)

        total_text_in = sum(turn.text_input_tokens for turn in session.turns)
        total_audio_in = sum(turn.audio_input_tokens for turn in session.turns)
        total_output = sum(turn.total_output_tokens for turn in session.turns)

        text_in_cost = (total_text_in / 1_000_000) * rates.text_input_per_1m
        audio_in_cost = (total_audio_in / 1_000_000) * rates.audio_input_per_1m
        output_cost = (total_output / 1_000_000) * rates.audio_output_per_1m

        return {
            "model_used": session.model.value,
            "total_text_input_cost": round(text_in_cost, 6),
            "total_audio_input_cost": round(audio_in_cost, 6),
            "total_output_cost": round(output_cost, 6),
            "grand_total_cost_usd": round(text_in_cost + audio_in_cost + output_cost, 6),
        }

    def calculate_cost(self, session: GeminiLiveSessionTokens) -> float:
        """Convenience method returning grand_total_cost_usd as float."""
        res = self.calculate_session_cost(session)
        return float(res.get("grand_total_cost_usd", 0.0))


# -------------------------------------------------------------------
# 4. Example Execution
# -------------------------------------------------------------------

if __name__ == "__main__":
    raw_log = {
        'usageMetadata': {
            'promptTokenCount': 8138, 
            'responseTokenCount': 76, 
            'totalTokenCount': 8214, 
            'promptTokensDetails': [
                {'modality': 'TEXT', 'tokenCount': 5480}, 
                {'modality': 'AUDIO', 'tokenCount': 2078}
            ],
            'responseTokensDetails': [{'modality': 'AUDIO', 'tokenCount': 21}], 
            'thoughtsTokenCount': 595
        }
    }

    calculator = GeminiCostCalculator()

    # Calculate cost across the requested 3.x models
    target_models = [
        GeminiModel.FLASH_3_1_LIVE_PREVIEW,
        GeminiModel.LIVE_3_8,
        GeminiModel.LIVE_3_8_EXTENDED_THINKING
    ]

    for model_tier in target_models:
        session = GeminiLiveSessionTokens(model=model_tier)
        session.add_turn(raw_log['usageMetadata'])

        session_cost = calculator.calculate_session_cost(session)
        print(f"--- Model: {model_tier.value} ---")
        print(f"Grand Total USD: ${session_cost['grand_total_cost_usd']:.6f}\n")
