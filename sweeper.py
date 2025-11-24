from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, getcontext
from typing import Dict

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

from config import Settings

# Use higher precision to avoid rounding surprises with very small tokens.
getcontext().prec = 28


@dataclass
class AssetBalance:
    asset: str
    free: Decimal
    locked: Decimal

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


class BinanceSweeper:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = Client(settings.api_key, settings.api_secret)
        if settings.api_url:
            self.client.API_URL = settings.api_url

        self.exchange_info: Dict[str, dict] = {}
        self.refresh_exchange_info()

    def refresh_exchange_info(self) -> None:
        info = self.client.get_exchange_info()
        self.exchange_info = {
            symbol_info["symbol"]: symbol_info
            for symbol_info in info.get("symbols", [])
            if symbol_info.get("status") == "TRADING"
        }
        logging.info("Loaded %s tradable pairs", len(self.exchange_info))

    def run_forever(self) -> None:
        logging.info(
            "Sweeper started. Target=%s dry_run=%s poll=%ss min_quote_notional=%s",
            self.settings.sweep_target,
            self.settings.dry_run,
            self.settings.poll_seconds,
            self.settings.min_quote_notional,
        )
        while True:
            try:
                self.sweep_once()
            except (BinanceAPIException, BinanceRequestException) as exc:
                logging.error("Binance API error: %s", exc)
            except Exception:
                logging.exception("Unexpected error during sweep")
            time.sleep(self.settings.poll_seconds)

    def sweep_once(self) -> None:
        balances = self._fetch_balances()
        whitelist = self.settings.effective_whitelist()
        for balance in balances:
            if balance.total <= 0:
                continue
            if balance.asset in whitelist:
                continue
            self._process_asset(balance)

    def _process_asset(self, balance: AssetBalance) -> None:
        symbol = f"{balance.asset}{self.settings.sweep_target}"
        symbol_info = self.exchange_info.get(symbol)
        if not symbol_info:
            logging.info("No trading pair for %s -> %s; skipping", balance.asset, symbol)
            return

        price = self._get_price(symbol)
        if price is None or price <= 0:
            logging.warning("No valid price for %s; skipping", symbol)
            return

        quantity = self._normalize_quantity(balance.free, symbol_info)
        if quantity is None:
            logging.info(
                "Skip %s: free=%s below min lot size for %s",
                balance.asset,
                balance.free,
                symbol,
            )
            return

        notional = quantity * price
        min_notional = self._get_min_notional(symbol_info)
        threshold = max(Decimal(str(self.settings.min_quote_notional)), min_notional)
        if notional < threshold:
            logging.info(
                "Skip %s: notional %s below threshold %s in %s",
                balance.asset,
                notional,
                threshold,
                self.settings.sweep_target,
            )
            return

        self._execute_order(symbol, quantity, price, notional)

    def _fetch_balances(self) -> list[AssetBalance]:
        account = self.client.get_account()
        balances: list[AssetBalance] = []
        for entry in account.get("balances", []):
            try:
                free = Decimal(entry.get("free", "0"))
                locked = Decimal(entry.get("locked", "0"))
            except Exception:
                logging.warning("Unable to parse balance entry: %s", entry)
                continue
            balances.append(
                AssetBalance(
                    asset=entry.get("asset", "").upper(),
                    free=free,
                    locked=locked,
                )
            )
        return balances

    def _get_price(self, symbol: str) -> Decimal | None:
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
        except BinanceAPIException as exc:
            logging.error("Failed to fetch price for %s: %s", symbol, exc)
            return None
        price_str = ticker.get("price")
        if price_str is None:
            return None
        return Decimal(price_str)

    def _get_min_notional(self, symbol_info: dict) -> Decimal:
        for filter_ in symbol_info.get("filters", []):
            if filter_.get("filterType") == "MIN_NOTIONAL":
                return Decimal(filter_.get("minNotional", "0"))
        return Decimal("0")

    def _normalize_quantity(self, free_amount: Decimal, symbol_info: dict) -> Decimal | None:
        lot_filter = next(
            (f for f in symbol_info.get("filters", []) if f.get("filterType") == "LOT_SIZE"),
            None,
        )
        if not lot_filter:
            return free_amount

        step_size = Decimal(lot_filter.get("stepSize", "1"))
        min_qty = Decimal(lot_filter.get("minQty", "0"))
        max_qty = Decimal(lot_filter.get("maxQty", "0"))

        # Round down to the nearest allowed step size.
        quantized = free_amount.quantize(step_size, rounding=ROUND_DOWN)
        if max_qty > 0 and quantized > max_qty:
            quantized = max_qty
        if quantized < min_qty or quantized <= 0:
            return None
        return quantized

    def _execute_order(
        self,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        notional: Decimal,
    ) -> None:
        qty_str = self._decimal_to_str(quantity)
        base_asset = symbol[:-len(self.settings.sweep_target)] if symbol.endswith(self.settings.sweep_target) else symbol
        if self.settings.dry_run:
            logging.info(
                "[DRY RUN] Would sell %s %s at ~%s to receive %s %s",
                qty_str,
                base_asset,
                price,
                notional,
                self.settings.sweep_target,
            )
            return

        try:
            order = self.client.order_market_sell(symbol=symbol, quantity=qty_str)
            logging.info(
                "Sold %s %s via %s orderId=%s",
                qty_str,
                symbol,
                self.settings.sweep_target,
                order.get("orderId"),
            )
        except BinanceAPIException as exc:
            logging.error("Order failed for %s qty=%s: %s", symbol, qty_str, exc)

    @staticmethod
    def _decimal_to_str(value: Decimal) -> str:
        """Convert Decimal to string without scientific notation."""
        normalized = value.normalize()
        return format(normalized, "f")
