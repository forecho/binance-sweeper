from __future__ import annotations

from typing import List, Literal

from pydantic import BaseSettings, Field, SettingsConfigDict, field_validator


class Settings(BaseSettings):
    api_key: str = Field(validation_alias="BINANCE_API_KEY")
    api_secret: str = Field(validation_alias="BINANCE_API_SECRET")
    sweep_target: Literal["USDT", "BNB"] = Field(
        default="USDT", validation_alias="SWEEP_TARGET"
    )
    whitelist: List[str] = Field(
        default_factory=lambda: ["BNB", "USDT", "BUSD", "USDC", "FDUSD"],
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
