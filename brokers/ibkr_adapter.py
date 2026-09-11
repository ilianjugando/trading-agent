"""Thin wrapper around ib_async. Requires IB Gateway or TWS running locally
and logged in (paper account -> port 7497, live -> port 7496, configurable
in .env). This is a real, order-placing connection — always verify you're
pointed at the paper port before running --mode paper.
"""
import math

from ib_async import IB, MarketOrder, Stock


def _first_real(*values) -> float | None:
    """El primer valor que sea un numero utilizable.

    No se puede usar `a or b` para esto: cuando IBKR no tiene suscripcion
    de datos devuelve `nan`, y `nan` es truthy en Python, asi que el `or`
    se queda con el nan y lo arrastra hasta reventar mucho mas adelante
    con "cannot convert float NaN to integer" al calcular la cantidad.
    """
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isnan(number) and number > 0:
            return number
    return None


class IBKRAdapter:
    def __init__(self, host: str, port: int, client_id: int):
        self.ib = IB()
        self.ib.connect(host, port, clientId=client_id)
        # 3 = datos demorados (~15 min). Una cuenta sin suscripcion de datos
        # en tiempo real devuelve nan en todos los campos de precio y el
        # error 10089; los demorados son gratis y alcanzan de sobra para un
        # bot que evalua una vez cada 30 minutos.
        self.ib.reqMarketDataType(3)

    def get_account_value(self) -> float:
        for row in self.ib.accountSummary():
            if row.tag == "NetLiquidation":
                return float(row.value)
        raise RuntimeError("NetLiquidation not found in IBKR account summary")

    def get_positions(self) -> dict:
        return {p.contract.symbol: p.position for p in self.ib.positions()}

    def _contract(self, symbol: str) -> Stock:
        """qualifyContracts devuelve [] para un simbolo que IBKR no conoce
        y no levanta nada, asi que el contrato sin cualificar seguia de
        largo y el fallo recien aparecia mas tarde como "todos los campos
        vinieron vacios o NaN" -- un mensaje que culpa al dato cuando la
        causa es que el simbolo no existe (delistado, en quiebra, o sin
        listado en USD)."""
        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        # El chequeo va contra conId, no contra el valor de retorno:
        # qualifyContracts devuelve el contrato igual cuando IBKR no lo
        # reconoce (solo loguea "Unknown contract"), nada mas que con
        # conId=0. Verificado en vivo con ABB e IRBT.
        if not contract.conId:
            raise ValueError(f"IBKR no reconoce {symbol}: sin definicion en SMART/USD")
        return contract

    def get_last_price(self, symbol: str) -> float:
        contract = self._contract(symbol)
        ticker = self.ib.reqMktData(contract)
        self.ib.sleep(3)
        # Se prueban varios campos por orden de preferencia: con datos
        # demorados `last` suele venir vacio y el precio util aparece en
        # los campos delayed o en el cierre previo.
        price = _first_real(
            ticker.last, ticker.close, ticker.marketPrice(),
            getattr(ticker, "delayedLast", None), getattr(ticker, "delayedClose", None),
            ticker.bid, ticker.ask,
        )
        self.ib.cancelMktData(contract)
        if price is None:
            raise RuntimeError(
                f"No se pudo obtener precio para {symbol} (todos los campos vinieron vacios o NaN)"
            )
        return price

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
        contract = self._contract(symbol)
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
