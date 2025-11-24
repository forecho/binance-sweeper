from __future__ import annotations

from typing import List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_key: str = Field(validation_alias="BINANCE_API_KEY")
    api_secret: str = Field(validation_alias="BINANCE_API_SECRET")
    sweep_target: Literal["USDT", "BNB"] = Field(
        default="USDT", validation_alias="SWEEP_TARGET"
    )
    whitelist: str | List[str] = Field(
        default="BNB,USDT,BUSD,USDC,FDUSD",
        validation_alias="WHITELIST",
        description="Assets that should never be swapped.",
    )
    poll_seconds: int = Field(
        default=60,
        validation_alias="POLL_SECONDS",
        description="Seconds between balance checks.",
    )
    min_quote_notional: float = Field(
        default=5.0,
        validation_alias="MIN_QUOTE_NOTIONAL",
        description="Minimum notional (in quote asset) before selling.",
    )
    dry_run: bool = Field(
        default=True,
        validation_alias="DRY_RUN",
        description="If true, do not place real orders.",
    )
    api_url: str | None = Field(
        default=None,
        validation_alias="BINANCE_API_URL",
        description="Override the Binance API URL (leave empty for default).",
    )
    auto_transfer_from_funding: bool = Field(
        default=False,
        validation_alias="AUTO_TRANSFER_FROM_FUNDING",
        description="If true, automatically transfer assets from funding account to spot before selling.",
    )
    auto_redeem_flexible_savings: bool = Field(
        default=False,
        validation_alias="AUTO_REDEEM_FLEXIBLE_SAVINGS",
        description="If true, automatically redeem assets from flexible savings (Binance Earn) before selling.",
    )
    auto_convert_dust_to_bnb: bool = Field(
        default=False,
        validation_alias="AUTO_CONVERT_DUST_TO_BNB",
        description="If true, automatically convert small assets to BNB when they are below trading threshold.",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    @field_validator("whitelist", mode="before")
    @classmethod
    def _split_whitelist(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            parts = [chunk.strip().upper() for chunk in value.split(",") if chunk.strip()]
            return parts
        return [item.upper() for item in value]

    @field_validator("sweep_target")
    @classmethod
    def _uppercase_target(cls, value: str) -> str:
        return value.upper()

    def effective_whitelist(self) -> set[str]:
        """Ensure sweep target is never sold and normalize to a set."""
        always_keep = {self.sweep_target}
        return set(item.upper() for item in self.whitelist) | always_keep
