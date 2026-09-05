"""Thin wrapper around python-okx. flag='1' hits OKX's Demo Trading
environment (paper mode, separate API keys); flag='0' is live.
"""
from okx.Account import AccountAPI
from okx.MarketData import MarketAPI
from okx.Trade import TradeAPI


class OKXAdapter:
    def __init__(self, api_key: str, api_secret: str, passphrase: str, demo_flag: str):
        self.account = AccountAPI(api_key, api_secret, passphrase, False, demo_flag)
        self.trade = TradeAPI(api_key, api_secret, passphrase, False, demo_flag)
        self.market = MarketAPI(api_key, api_secret, passphrase, False, demo_flag)

    def get_usdt_balance(self) -> float:
        resp = self.account.get_account_balance(ccy="USDT")
        details = resp["data"][0]["details"]
        if not details:
            return 0.0
        return float(details[0]["availBal"])

    def get_last_price(self, inst_id: str) -> float:
        resp = self.market.get_ticker(instId=inst_id)
        return float(resp["data"][0]["last"])

    def get_24h_change_pct(self, inst_id: str) -> float:
        resp = self.market.get_ticker(instId=inst_id)
        d = resp["data"][0]
        open_24h = float(d["open24h"])
        last = float(d["last"])
        if open_24h == 0:
            return 0.0
        return (last - open_24h) / open_24h

    def place_market_order(self, inst_id: str, usd_amount: float, side: str) -> dict:
        """side: 'buy' or 'sell'. Spot market order sized in quote currency (USDT) for buys."""
        sz = str(round(usd_amount, 2))
        resp = self.trade.place_order(
            instId=inst_id,
            tdMode="cash",
            side=side,
            ordType="market",
            sz=sz,
            tgtCcy="quote_ccy" if side == "buy" else "base_ccy",
        )
        data = resp["data"][0]
        return {
            "instId": inst_id,
            "side": side,
            "usd_amount": usd_amount,
            "ordId": data.get("ordId"),
            "status": data.get("sMsg") or "submitted",
        }
