"""Thin wrapper around python-okx. flag='1' hits OKX's Demo Trading
environment (paper mode, separate API keys); flag='0' is live.
"""
import time
import uuid

import pandas as pd
from okx.Account import AccountAPI
from okx.MarketData import MarketAPI
from okx.Trade import TradeAPI


class OKXAdapter:
    def __init__(self, api_key: str, api_secret: str, passphrase: str, demo_flag: str):
        self.account = AccountAPI(api_key, api_secret, passphrase, False, demo_flag)
        self.trade = TradeAPI(api_key, api_secret, passphrase, False, demo_flag)
        self.market = MarketAPI(api_key, api_secret, passphrase, False, demo_flag)

    def get_balance(self, ccy: str) -> float:
        """Actually-available balance of one currency, right now -- ground
        truth for "how much can I sell", immune to any drift in a locally
        tracked qty (a stale estimate, dust from fees, staking rewards...).
        """
        resp = self.account.get_account_balance(ccy=ccy)
        details = resp["data"][0]["details"]
        if not details:
            return 0.0
        return float(details[0]["availBal"])

    def get_usdt_balance(self) -> float:
        return self.get_balance("USDT")

    def get_equity(self) -> dict:
        """Valor TOTAL de la cuenta, calculado por nosotros, mas la cifra
        que reporta el exchange, mas la divergencia entre ambas.

        Por que no se usa `totalEq` de OKX directamente: medido en vivo
        (2026-09-10) OKX reportaba eqUsd de STX en $5.575,49 cuando el
        valor real a precio de mercado era $1.091,90 -- 5,1x de error en un
        campo del propio exchange, suficiente para inflar el pool_value un
        ~4% y con el todo el sizing y el baseline de drawdown.

        La cifra que manda es la que calculamos con `get_last_price`, que
        es la MISMA fuente de precio que usan los stops y el P&L. Asi el
        sistema entero valua con un solo criterio, en vez de que el sizing
        use la cuenta del exchange y los stops otra (seccion 45: un solo
        source of truth). La cifra del exchange se conserva para poder
        alertar cuando discrepen, nunca para corregir en silencio.

        Un solo par de llamadas: un balance + un get_tickers de todo el
        mercado spot (no una por simbolo).
        """
        resp = self.account.get_account_balance()
        account = resp["data"][0]
        exchange_reported = float(account.get("totalEq") or 0.0)

        tickers = self.market.get_tickers(instType="SPOT")
        prices = {
            row["instId"]: float(row["last"])
            for row in tickers["data"]
            if row.get("last")
        }

        computed = 0.0
        unpriced: dict[str, float] = {}
        for detail in account.get("details", []):
            ccy = detail["ccy"]
            qty = float(detail.get("availBal") or 0.0)
            if qty <= 0:
                continue
            if ccy in ("USDT", "USD", "USDC"):
                computed += qty
                continue
            price = prices.get(f"{ccy}-USDT")
            if price is None:
                # No se puede valuar: se informa en vez de asumir cero en
                # silencio (un cero silencioso es indistinguible de "no hay
                # nada", ver seccion 15).
                unpriced[ccy] = qty
                continue
            computed += qty * price

        divergence_pct = (
            (exchange_reported - computed) / computed * 100 if computed > 0 else 0.0
        )
        return {
            "equity_usd": round(computed, 2),
            "exchange_reported_usd": round(exchange_reported, 2),
            "divergence_pct": round(divergence_pct, 2),
            "unpriced": unpriced,
        }

    def get_last_price(self, inst_id: str) -> float:
        resp = self.market.get_ticker(instId=inst_id)
        return float(resp["data"][0]["last"])

    def get_spot_tickers(self) -> list[dict]:
        """Every USDT spot ticker in one call, each with `inst_id`,
        `volume_24h_usd` and `change_24h_pct`. Used to build the scan
        universe from live liquidity instead of a hardcoded list."""
        resp = self.market.get_tickers(instType="SPOT")
        out = []
        for row in resp["data"]:
            if not row["instId"].endswith("-USDT"):
                continue
            open_24h = float(row.get("open24h") or 0)
            last = float(row.get("last") or 0)
            if open_24h <= 0:
                continue
            out.append({
                "inst_id": row["instId"],
                "volume_24h_usd": float(row.get("volCcy24h") or 0),
                "change_24h_pct": (last - open_24h) / open_24h,
            })
        return out

    def get_24h_change_pct(self, inst_id: str) -> float:
        resp = self.market.get_ticker(instId=inst_id)
        d = resp["data"][0]
        open_24h = float(d["open24h"])
        last = float(d["last"])
        if open_24h == 0:
            return 0.0
        return (last - open_24h) / open_24h

    def get_candles(self, inst_id: str, bar: str = "1H", limit: int = 100) -> list[float]:
        """Closing prices, oldest to newest (OKX returns newest-first)."""
        resp = self.market.get_candlesticks(instId=inst_id, bar=bar, limit=str(limit))
        return [float(row[4]) for row in reversed(resp["data"])]

    def get_history_candles(self, inst_id: str, bar: str = "1H", days: int = 30) -> list[float]:
        """Like get_candles, but pages OKX's history-candles endpoint
        (100 rows/call) backwards to reach further back than the ~4 days
        get_candles's single call covers. Closing prices, oldest to
        newest. Only used by the backtest tools -- the live orchestrator
        path (get_candles) is untouched."""
        bars_needed = days * (24 if "H" in bar else 1)
        closes: list[float] = []
        after = ""
        while len(closes) < bars_needed:
            resp = self.market.get_history_candlesticks(instId=inst_id, bar=bar, limit="100", after=after)
            rows = resp["data"]
            if not rows:
                break
            closes = [float(row[4]) for row in reversed(rows)] + closes
            after = rows[-1][0]
        return closes

    def get_candles_ohlcv(self, inst_id: str, bar: str = "1H", limit: int = 300) -> pd.DataFrame:
        """Full OHLCV history (lowercase columns, DatetimeIndex, oldest to
        newest) -- only used by the optional Kronos forecast signal.
        get_candles() (closes-only, used by the live indicators path and
        the backtest) is untouched."""
        rows: list[list[str]] = []
        after = ""
        while len(rows) < limit:
            resp = self.market.get_history_candlesticks(instId=inst_id, bar=bar, limit="100", after=after)
            batch = resp["data"]
            if not batch:
                break
            rows = list(reversed(batch)) + rows
            after = batch[-1][0]

        rows = rows[-limit:]
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
        df.index = pd.to_datetime(df["ts"].astype("int64"), unit="ms")
        return df[["open", "high", "low", "close"]]

    def place_market_order(self, inst_id: str, usd_amount: float, side: str) -> dict:
        """side: 'buy' or 'sell'. Spot market order sized in quote currency
        (USDT, 2 decimals) for buys; base currency (e.g. BTC, needs finer
        precision -- rounding a small BTC amount to 2 decimals truncates
        it to 0 and the order is rejected) for sells.

        For a buy, `price`/`qty` in the returned dict are the ACTUAL fill
        (queried back from OKX), not an estimate. Bug found live
        (2026-09-10, forced-exit test on CAT-USDT): the caller used to
        estimate qty as usd_amount/current_price, which ignores that OKX
        takes its trading fee out of the received base currency -- the
        estimate was consistently ~0.1% too high. That doesn't show up
        until the position is later sold: the sell asks for slightly more
        than the account actually holds and OKX rejects it outright
        (51008, insufficient balance) -- exactly the moment a stop-loss
        needs to work. get_filled_base_qty() already existed and did this
        correctly, but was only ever wired into scripts/smoke_test_order.py,
        never into the live orchestrator path.
        """
        sz = str(round(usd_amount, 2)) if side == "buy" else str(round(usd_amount, 8))

        # Idempotency key. Sin esto, un fallo ambiguo de red (la peticion
        # salio, la respuesta se perdio) es indistinguible de un fallo
        # limpio: el codigo lo registra como rechazo y sigue, pero la orden
        # puede haberse ejecutado igual -- y una posicion real que el
        # sistema no registro es una posicion sin stop-loss. Con un clOrdId
        # propio se puede preguntarle despues a OKX si esa orden existe.
        cl_ord_id = f"ta{uuid.uuid4().hex[:24]}"

        try:
            resp = self.trade.place_order(
                instId=inst_id,
                tdMode="cash",
                side=side,
                ordType="market",
                sz=sz,
                tgtCcy="quote_ccy" if side == "buy" else "base_ccy",
                clOrdId=cl_ord_id,
            )
        except Exception as network_error:
            landed = self._find_order_by_client_id(inst_id, cl_ord_id)
            if landed is None:
                raise
            # La orden SI existe: el fallo era solo de la respuesta. Se
            # sigue adelante con la orden real en vez de perderle el rastro.
            resp = {"data": [{"sCode": "0", "ordId": landed.get("ordId"), "sMsg": ""}]}
            _ = network_error

        data = resp["data"][0]
        if data.get("sCode") != "0":
            raise RuntimeError(f"OKX order rejected ({data.get('sCode')}): {data.get('sMsg')}")

        result = {
            "instId": inst_id,
            "side": side,
            "usd_amount": usd_amount,
            "ordId": data.get("ordId"),
            "clOrdId": cl_ord_id,
            "status": "submitted",
        }
        if side == "buy":
            fill = self._wait_for_fill(inst_id, data.get("ordId"))
            result["qty"] = fill["qty"]
            result["price"] = fill["price"]
        return result

    def _find_order_by_client_id(self, inst_id: str, cl_ord_id: str) -> dict | None:
        """La orden con ese clOrdId, si OKX la tiene. None si no existe o
        si tampoco se puede consultar. Se usa solo para desambiguar un
        fallo de red: ver place_market_order."""
        try:
            resp = self.trade.get_order(instId=inst_id, clOrdId=cl_ord_id)
        except Exception:
            return None
        rows = resp.get("data") or []
        if not rows or not rows[0].get("ordId"):
            return None
        return rows[0]

    def _wait_for_fill(self, inst_id: str, ord_id: str) -> dict:
        """Actual fill for a just-placed market order -- `accFillSz` (base
        currency, net of fees) and `avgPx`. Market orders fill almost
        immediately; a short wait covers that without polling."""
        time.sleep(1)
        resp = self.trade.get_order(instId=inst_id, ordId=ord_id)
        data = resp["data"][0]
        return {"qty": float(data["accFillSz"]), "price": float(data["avgPx"])}

    def get_filled_base_qty(self, inst_id: str, ord_id: str) -> float:
        """Base-currency amount actually filled by a market order -- needed
        because a sell needs the base-currency amount received, and a buy
        sized in quote currency doesn't state it up front."""
        return self._wait_for_fill(inst_id, ord_id)["qty"]
