from __future__ import annotations

import logging
import time
import os
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, getcontext
from typing import Dict
from datetime import datetime, timedelta

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

from config import Settings

# Use higher precision to avoid rounding surprises with very small tokens.
getcontext().prec = 28

# Cache file for dust conversion cooldown
DUST_COOLDOWN_CACHE_FILE = ".dust_conversion_cache.json"


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
        # First, redeem assets from flexible savings if enabled
        if self.settings.auto_redeem_flexible_savings:
            self._redeem_from_flexible_savings()
        
        # Second, transfer assets from funding account if enabled
        if self.settings.auto_transfer_from_funding:
            self._transfer_from_funding()
        
        balances = self._fetch_balances()
        whitelist = self.settings.effective_whitelist()
        for balance in balances:
            if balance.total <= 0:
                continue
            if balance.asset in whitelist:
                continue
            self._process_asset(balance)
        
        # Finally, convert small assets to BNB if enabled
        if self.settings.auto_convert_dust_to_bnb:
            self._convert_dust_to_bnb()

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
        
        # Add 5% safety margin to account for price volatility
        # This prevents orders from failing due to slight price movements
        safe_threshold = threshold * Decimal("1.05")
        
        if notional < safe_threshold:
            logging.info(
                "Skip %s: notional %.4f below threshold %.4f (exchange min: %.4f, with 5%% margin) in %s",
                balance.asset,
                notional,
                threshold,
                min_notional,
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

    def _fetch_funding_balances(self) -> list[AssetBalance]:
        """Fetch balances from funding account."""
        try:
            # Use funding wallet endpoint to get funding account balances
            funding_wallet = self.client.funding_wallet()
            balances: list[AssetBalance] = []
            for entry in funding_wallet:
                try:
                    free = Decimal(entry.get("free", "0"))
                    locked = Decimal(entry.get("locked", "0"))
                except Exception:
                    logging.warning("Unable to parse funding balance entry: %s", entry)
                    continue
                balances.append(
                    AssetBalance(
                        asset=entry.get("asset", "").upper(),
                        free=free,
                        locked=locked,
                    )
                )
            return balances
        except BinanceAPIException as exc:
            logging.error("Failed to fetch funding account balances: %s", exc)
            return []
        except Exception as exc:
            logging.error("Unexpected error fetching funding balances: %s", exc)
            return []

    def _transfer_from_funding(self) -> None:
        """Transfer assets from funding account to spot account."""
        funding_balances = self._fetch_funding_balances()
        whitelist = self.settings.effective_whitelist()
        
        for balance in funding_balances:
            if balance.free <= 0:
                continue
            if balance.asset in whitelist:
                continue
            
            # Try to transfer this asset to spot account
            self._transfer_asset_to_spot(balance)

    def _transfer_asset_to_spot(self, balance: AssetBalance) -> None:
        """Transfer a single asset from funding to spot account."""
        amount_str = self._decimal_to_str(balance.free)
        
        if self.settings.dry_run:
            logging.info(
                "[DRY RUN] Would transfer %s %s from FUNDING to SPOT account",
                amount_str,
                balance.asset,
            )
            return
        
        try:
            result = self.client.universal_transfer(
                type="FUNDING_MAIN",  # FUNDING -> MAIN (Spot)
                asset=balance.asset,
                amount=amount_str,
            )
            tran_id = result.get("tranId", "unknown")
            logging.info(
                "Transferred %s %s from FUNDING to SPOT (tranId=%s)",
                amount_str,
                balance.asset,
                tran_id,
            )
        except BinanceAPIException as exc:
            logging.error(
                "Failed to transfer %s %s from FUNDING to SPOT: %s",
                amount_str,
                balance.asset,
                exc,
            )

    def _fetch_flexible_savings_balances(self) -> Dict[str, tuple[AssetBalance, str]]:
        """Fetch balances from flexible savings (Simple Earn Flexible).
        
        Returns a dict mapping asset symbol to (AssetBalance, productId).
        """
        try:
            # Use simple earn flexible product position to get savings balances
            positions = self.client.get_simple_earn_flexible_product_position()
            balances: Dict[str, tuple[AssetBalance, str]] = {}
            
            for entry in positions.get("rows", []):
                try:
                    # totalAmount includes both free and locked amounts in flexible savings
                    total = Decimal(entry.get("totalAmount", "0"))
                    if total <= 0:
                        continue
                    
                    asset = entry.get("asset", "").upper()
                    product_id = entry.get("productId", "")
                    
                    if not asset or not product_id:
                        continue
                    
                    balance = AssetBalance(
                        asset=asset,
                        free=total,  # Treat all as free since we can redeem flexible savings
                        locked=Decimal("0"),
                    )
                    balances[asset] = (balance, product_id)
                except Exception:
                    logging.warning("Unable to parse flexible savings entry: %s", entry)
                    continue
            
            return balances
        except BinanceAPIException as exc:
            logging.error("Failed to fetch flexible savings balances: %s", exc)
            return {}
        except Exception as exc:
            logging.error("Unexpected error fetching flexible savings: %s", exc)
            return {}

    def _redeem_from_flexible_savings(self) -> None:
        """Redeem assets from flexible savings to spot account."""
        savings_balances = self._fetch_flexible_savings_balances()
        whitelist = self.settings.effective_whitelist()
        
        for asset, (balance, product_id) in savings_balances.items():
            if balance.free <= 0:
                continue
            if asset in whitelist:
                continue
            
            # Try to redeem this asset from flexible savings
            self._redeem_flexible_savings_asset(balance, product_id)

    def _redeem_flexible_savings_asset(self, balance: AssetBalance, product_id: str) -> None:
        """Redeem a single asset from flexible savings."""
        amount_str = self._decimal_to_str(balance.free)
        
        if self.settings.dry_run:
            logging.info(
                "[DRY RUN] Would redeem %s %s from flexible savings to SPOT account (productId=%s)",
                amount_str,
                balance.asset,
                product_id,
            )
            return
        
        try:
            # Redeem from flexible savings (Simple Earn Flexible)
            # Use the correct productId from the position data
            result = self.client.redeem_simple_earn_flexible_product(
                productId=product_id,
                redeemAll=True,
            )
            
            # Check if redemption was successful
            success = result.get("success", False)
            if success:
                logging.info(
                    "Redeemed %s %s from flexible savings to SPOT (productId=%s)",
                    amount_str,
                    balance.asset,
                    product_id,
                )
            else:
                logging.warning(
                    "Redemption returned success=false for %s %s (productId=%s)",
                    amount_str,
                    balance.asset,
                    product_id,
                )
        except BinanceAPIException as exc:
            # If the asset is not in flexible savings or already redeemed, log as info not error
            if "not found" in str(exc).lower() or "insufficient" in str(exc).lower():
                logging.info(
                    "Cannot redeem %s %s from flexible savings (may not be in savings): %s",
                    amount_str,
                    balance.asset,
                    exc,
                )
            else:
                logging.error(
                    "Failed to redeem %s %s from flexible savings: %s",
                    amount_str,
                    balance.asset,
                    exc,
                )

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
        """Get minimum notional value from symbol filters.
        
        Checks both MIN_NOTIONAL and NOTIONAL filter types as Binance
        has been transitioning between these.
        """
        min_notional = Decimal("0")
        
        for filter_ in symbol_info.get("filters", []):
            filter_type = filter_.get("filterType")
            
            # Check old MIN_NOTIONAL filter
            if filter_type == "MIN_NOTIONAL":
                notional = Decimal(filter_.get("minNotional", "0"))
                if notional > min_notional:
                    min_notional = notional
            
            # Check new NOTIONAL filter
            elif filter_type == "NOTIONAL":
                notional = Decimal(filter_.get("minNotional", "0"))
                if notional > min_notional:
                    min_notional = notional
        
        return min_notional

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

        # Calculate how many steps fit into the free amount
        if step_size > 0:
            # Use floor division to ensure we stay within step size boundaries
            steps = int(free_amount / step_size)
            quantized = step_size * steps
            
            # Ensure the result has the same precision as step_size
            # This is critical for LOT_SIZE compliance
            quantized = quantized.quantize(step_size)
        else:
            quantized = free_amount

        # Ensure we don't exceed max quantity
        if max_qty > 0 and quantized > max_qty:
            quantized = max_qty.quantize(step_size)
        
        # Check if quantity meets minimum requirements
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

    def _convert_dust_to_bnb(self) -> None:
        """Convert small assets (dust) to BNB.
        
        This is useful for assets that are below the minimum trading threshold
        but can still be converted to BNB through Binance's dust transfer feature.
        """
        # Check cooldown cache
        if self._is_dust_conversion_on_cooldown():
            cooldown_until = self._get_dust_cooldown_time()
            if cooldown_until:
                remaining = cooldown_until - datetime.now()
                minutes = int(remaining.total_seconds() / 60)
                logging.info(
                    "Dust conversion is on cooldown (rate limit). "
                    "Next attempt available in ~%d minutes.",
                    minutes
                )
            return
        
        try:
            # Get list of assets that can be converted to BNB
            dust_response = self.client.get_dust_assets()
            
            # The API returns details directly, no "success" field
            dust_details = dust_response.get("details", [])
            if not dust_details:
                logging.info("No dust assets available to convert to BNB")
                return
            
            # Filter out whitelisted assets and BNB itself
            whitelist = self.settings.effective_whitelist()
            assets_to_convert = []
            
            for detail in dust_details:
                asset = detail.get("asset", "").upper()
                amount = detail.get("amountFree", "0")
                
                # Skip if in whitelist or is BNB
                if asset in whitelist or asset == "BNB":
                    continue
                
                # Only convert if there's actually some amount
                if Decimal(amount) > 0:
                    assets_to_convert.append(asset)
            
            if not assets_to_convert:
                logging.info("No eligible dust assets to convert to BNB (after whitelist filter)")
                return
            
            if self.settings.dry_run:
                total_bnb = sum(Decimal(d.get("toBNBOffExchange", "0")) 
                               for d in dust_details 
                               if d.get("asset", "").upper() in assets_to_convert)
                logging.info(
                    "[DRY RUN] Would convert %d dust assets to BNB (estimated: %s BNB): %s",
                    len(assets_to_convert),
                    total_bnb,
                    ", ".join(assets_to_convert),
                )
                return
            
            # Perform the dust transfer (batch conversion)
            # IMPORTANT: Binance API limits this to once per hour
            # So we MUST convert all assets at once, not individually
            logging.info("Attempting to convert %d assets: %s", len(assets_to_convert), assets_to_convert)
            
            # Workaround: python-binance has issues with list parameters
            # Try calling the underlying API directly with properly formatted parameters
            try:
                # First try the standard way (pass list)
                result = self.client.transfer_dust(asset=assets_to_convert)
            except BinanceAPIException as exc:
                if "signature" in str(exc).lower() or "illegal parameter" in str(exc).lower():
                    # Fallback: use direct API call with manual parameter construction
                    logging.info("Standard method failed, trying direct API call...")
                    result = self._transfer_dust_direct(assets_to_convert)
                else:
                    raise
            
            # Check for successful response
            total_transferred = result.get("totalTransfered", "0")
            transfer_result = result.get("transferResult", [])
            
            if transfer_result:
                logging.info(
                    "Converted %d dust assets to BNB (total: %s BNB)",
                    len(transfer_result),
                    total_transferred,
                )
                
                # Log individual conversions
                for item in transfer_result:
                    from_asset = item.get("fromAsset", "")
                    amount = item.get("amount", "0")
                    service_charge = item.get("serviceChargeAmount", "0")
                    logging.info(
                        "  Converted %s %s to BNB (fee: %s BNB)",
                        amount,
                        from_asset,
                        service_charge,
                    )
            else:
                logging.warning("Dust transfer returned empty result: %s", result)
                
        except BinanceAPIException as exc:
            error_code = getattr(exc, 'code', None)
            error_msg = str(exc)
            
            # Handle specific error codes
            if error_code == 32110 or "once within 1 hour" in error_msg:
                # Record cooldown time
                self._record_dust_conversion_cooldown()
                cooldown_until = self._get_dust_cooldown_time()
                logging.warning(
                    "Dust conversion rate limit reached (can only convert once per hour). "
                    "Cooldown cached until %s. Will skip attempts until then.",
                    cooldown_until.strftime("%Y-%m-%d %H:%M:%S") if cooldown_until else "unknown"
                )
            elif "illegal parameter" in error_msg.lower():
                logging.error(
                    "Invalid parameter format for dust conversion. "
                    "This may be a limitation with the current python-binance library version. "
                    "Please convert dust manually via Binance web/app (Account > Wallet > Convert Small Balance to BNB). "
                    "Assets attempted: %s",
                    assets_to_convert,
                )
            else:
                logging.error("Failed to convert dust to BNB (API error): %s", exc)
        except Exception as exc:
            logging.error("Unexpected error converting dust to BNB: %s", exc)

    def _transfer_dust_direct(self, assets: list[str]) -> dict:
        """Direct API call for dust transfer with manual parameter construction.
        
        This is a workaround for python-binance's issue with list parameters.
        """
        import time
        import hmac
        import hashlib
        from urllib.parse import urlencode
        
        # Build parameters manually - multiple asset parameters
        params = []
        for asset in assets:
            params.append(('asset', asset))
        
        # Add timestamp and recvWindow
        timestamp = int(time.time() * 1000)
        params.append(('timestamp', timestamp))
        params.append(('recvWindow', 60000))  # 60 seconds window
        
        # Build query string for signature
        query_string = urlencode(params)
        
        logging.debug("Query string for signature: %s", query_string)
        
        # Generate signature
        signature = hmac.new(
            self.settings.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # Add signature to params
        params.append(('signature', signature))
        
        # Make direct request
        import requests
        headers = {'X-MBX-APIKEY': self.settings.api_key}
        
        # Get base URL (remove /api if present)
        base_url = self.client.API_URL.rstrip('/')
        if base_url.endswith('/api'):
            base_url = base_url[:-4]
        
        url = f"{base_url}/sapi/v1/asset/dust"
        
        logging.info("Direct API call to: %s with %d assets", url, len(assets))
        response = requests.post(url, headers=headers, data=params)
        
        # Check response and handle errors
        if response.status_code != 200:
            logging.error("API response status: %d, body: %s", response.status_code, response.text)
            
            # Parse error response
            try:
                error_data = response.json()
                error_code = error_data.get('code')
                error_msg = error_data.get('msg', '')
                
                # Convert to BinanceAPIException so it's handled properly
                from binance.exceptions import BinanceAPIException
                raise BinanceAPIException(response, error_code, error_msg)
            except (ValueError, KeyError):
                # If JSON parsing fails, raise the original error
                response.raise_for_status()
        
        return response.json()

    def _is_dust_conversion_on_cooldown(self) -> bool:
        """Check if dust conversion is currently on cooldown."""
        cooldown_time = self._get_dust_cooldown_time()
        if cooldown_time is None:
            return False
        return datetime.now() < cooldown_time
    
    def _get_dust_cooldown_time(self) -> datetime | None:
        """Get the time when dust conversion cooldown expires."""
        try:
            if not os.path.exists(DUST_COOLDOWN_CACHE_FILE):
                return None
            
            with open(DUST_COOLDOWN_CACHE_FILE, 'r') as f:
                data = json.load(f)
            
            cooldown_until_str = data.get('cooldown_until')
            if not cooldown_until_str:
                return None
            
            return datetime.fromisoformat(cooldown_until_str)
        except Exception as exc:
            logging.debug("Failed to read dust cooldown cache: %s", exc)
            return None
    
    def _record_dust_conversion_cooldown(self) -> None:
        """Record that dust conversion hit rate limit and set cooldown."""
        try:
            # Set cooldown for 61 minutes (1 hour + 1 minute buffer)
            cooldown_until = datetime.now() + timedelta(minutes=61)
            
            data = {
                'cooldown_until': cooldown_until.isoformat(),
                'last_attempt': datetime.now().isoformat()
            }
            
            with open(DUST_COOLDOWN_CACHE_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            
            logging.info("Dust conversion cooldown cached until %s", cooldown_until.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as exc:
            logging.warning("Failed to write dust cooldown cache: %s", exc)

    @staticmethod
    def _decimal_to_str(value: Decimal) -> str:
        """Convert Decimal to string without scientific notation and trailing zeros."""
        # Strip trailing zeros and ensure no scientific notation
        value_str = format(value, "f")
        # Remove trailing zeros after decimal point
        if "." in value_str:
            value_str = value_str.rstrip("0").rstrip(".")
        return value_str
