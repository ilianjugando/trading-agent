"""Thin wrapper around ib_async. Requires IB Gateway or TWS running locally
and logged in (paper account -> port 7497, live -> port 7496, configurable
in .env). This is a real, order-placing connection — always verify you're
pointed at the paper port before running --mode paper.
"""
from ib_async import IB, MarketOrder, Stock


class IBKRAdapter:
    def __init__(self, host: str, port: int, client_id: int):
        self.ib = IB()
        self.ib.connect(host, port, clientId=client_id)

    def get_account_value(self) -> float:
        for row in self.ib.accountSummary():
            if row.tag == "NetLiquidation":
                return float(row.value)
        raise RuntimeError("NetLiquidation not found in IBKR account summary")

    def get_positions(self) -> dict:
        return {p.contract.symbol: p.position for p in self.ib.positions()}

    def get_last_price(self, symbol: str) -> float:
        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        ticker = self.ib.reqMktData(contract)
        self.ib.sleep(2)
        price = ticker.last or ticker.close
        self.ib.cancelMktData(contract)
        if not price:
            raise RuntimeError(f"Could not fetch price for {symbol}")
        return float(price)

    def place_market_order(self, symbol: str, usd_amount: float, action: str) -> dict:
        """action: 'BUY' or 'SELL'. usd_amount is converted to whole shares
        at the last price (IBKR fractional shares need a different order
        type this adapter does not implement yet)."""
        price = self.get_last_price(symbol)
        qty = int(usd_amount // price)
        if qty < 1:
            raise ValueError(f"${usd_amount:.2f} is not enough for one share of {symbol} at ${price:.2f}")
        return self.place_market_order_by_qty(symbol, qty, action)

    def place_market_order_by_qty(self, symbol: str, qty: int, action: str) -> dict:
        """For exiting an exact held position (e.g. a stop-loss sell), where
        sizing by dollar amount would round to the wrong share count."""
        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        order = MarketOrder(action, qty)
        trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(2)
        return {
            "symbol": symbol,
            "action": action,
            "qty": qty,
            "price": self.get_last_price(symbol),
            "status": trade.orderStatus.status,
        }

    def disconnect(self) -> None:
        self.ib.disconnect()
