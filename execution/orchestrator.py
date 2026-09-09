"""Main entrypoint. One run = one pass over stocks and/or crypto:
compute signal -> Gemini review -> risk checks -> place order -> log.
Intended to be invoked on a schedule (Windows Task Scheduler) rather than
run as a long-lived process. See README.md for setup.
"""
import argparse
import json
import sys
import traceback
from dataclasses import asdict
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yfinance as yf

from brokers.ibkr_adapter import IBKRAdapter
from brokers.okx_adapter import OKXAdapter
from config.settings import load_settings
from config.universe import resolve_stock_universe
from execution import tournament
from execution.positions import PositionTracker
from execution.sizing import BUCKET_CAPS, classify_bucket, size_position
from risk.circuit_breaker import CircuitBreaker, TradingHalted
from risk.spend_guard import SpendGuard, SpendLimitError
from signals.crypto_fundamentals import fetch_market_fundamentals, okx_inst_id_to_symbol
from signals.crypto_trend import liquid_universe as liquid_crypto_universe
from signals.darvas import compute_box
from signals.opportunity_scanner import scan
from signals.stock_strategies import evaluate_all as evaluate_stock_strategies
from signals.indicators import rsi, sma_trend, volatility_regime
from signals.insider_signal import analyst_consensus, insider_sentiment
from signals.kronos_forecast import forecast as kronos_forecast
from signals.news_sentiment import fetch_sentiment as news_sentiment
from signals.llm_review import review_signal


def _indicator_confirmation(closes: list[float]) -> dict:
    """Traditional-TA context alongside the LLM panel's vote: momentum
    (RSI), trend direction (SMA crossover), and a volatility reading."""
    return {
        "rsi_14": rsi(closes),
        "sma_trend": sma_trend(closes),
        "volatility_20": volatility_regime(closes),
    }


def _fails_indicator_confirmation(indicators: dict) -> str | None:
    """None if the indicators don't object; otherwise the reason they do.
    Two well-worn TA rules: don't buy an already-overbought move, don't buy
    against the prevailing trend."""
    if indicators["rsi_14"] is not None and indicators["rsi_14"] >= 80:
        return f"overbought (RSI {indicators['rsi_14']} >= 80)"
    if indicators["sma_trend"] == "down":
        return "against prevailing trend (SMA fast < slow)"
    return None


def _market_is_open(now: datetime | None = None) -> bool:
    """NYSE/Nasdaq regular hours, 9:30-16:00 America/New_York, Mon-Fri.
    Reads the market's own clock instead of the host machine's, so
    Task Scheduler's fixed local start time doesn't drift when the US
    shifts for daylight saving and the host doesn't (or vice versa).
    Doesn't account for market holidays -- worst case on a holiday is
    the same as any other closed-market run (order queues instead of
    filling), not a crash."""
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    return time(9, 30) <= now.time() < time(16, 0)


def _log(logs_dir, filename: str, record: dict) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    with open(logs_dir / filename, "a") as f:
        f.write(json.dumps(record) + "\n")


# Cuantos candidatos del ranking se le ofrecen al panel por ciclo. El
# sistema anterior evaluaba exactamente 1 (de 243 pares escaneados en
# crypto, de 20 acciones en el otro pool), asi que el 99% del universo no
# llegaba nunca a una decision. El tope existe igual porque cada revision
# cuesta 3 llamadas a modelos externos con cuota diaria.
MAX_CANDIDATES_PER_CYCLE = 5

# Techos de cartera. Muchas posiciones chicas es la estrategia; infinitas
# posiciones chicas es dispersion sin tesis, y ademas hace imposible
# monitorear cada salida. Medido: sin este limite el sistema abria 5
# posiciones por ciclo cada 30 minutos.
MAX_OPEN_POSITIONS = 12

# Reserva de oportunidad. Quedarse sin efectivo significa no poder tomar la
# mejor oportunidad de la semana porque el capital ya esta en las quince
# anteriores, que eran peores.
MAX_DEPLOYED_PCT = 0.60


def _deployed_usd(positions) -> float:
    return sum(p.get("entry_price", 0.0) * p.get("qty", 0.0) for p in positions.all_open().values())


def _snapshot_portfolio(settings, pool: str, pool_value: float, positions) -> None:
    """Un punto de la serie historica del portafolio, en un archivo propio
    y liviano (no decisions.log, que ya pesa 100KB+ y se re-parsea en cada
    poll del dashboard). Sin esto no existe ninguna base para el grafico de
    rendimiento, drawdown o volatilidad -- se escribe en cada ciclo,
    incluidos los que no compran nada, porque esos son la mayoria y son
    los que realmente arman la curva."""
    deployed = _deployed_usd(positions)
    _log(settings.logs_dir, "portfolio_history.jsonl", {
        "pool": pool,
        "total_value": round(pool_value, 2),
        "deployed": round(deployed, 2),
        "cash": round(pool_value - deployed, 2),
        "n_positions": len(positions.all_open()),
    })


def _evaluate_candidates(settings, scan_result, pool, pool_value, held, guard, positions,
                         place_order, kronos_ohlcv=None, get_price=None) -> int:
    """Ofrece los mejores candidatos al panel y ejecuta los aprobados,
    dimensionando cada posicion segun conviccion. Devuelve cuantas se
    ejecutaron."""
    executed = 0
    for candidate in scan_result.shortlist[:MAX_CANDIDATES_PER_CYCLE]:
        symbol = candidate.symbol
        if symbol in held:
            continue

        open_count = len(positions.all_open())
        deployed = _deployed_usd(positions)
        if open_count >= MAX_OPEN_POSITIONS:
            _log(settings.logs_dir, "decisions.log", {
                "pool": pool, "symbol": symbol, "result": "skipped_portfolio_full",
                "reason": f"{open_count} posiciones abiertas, tope {MAX_OPEN_POSITIONS}",
            })
            break
        if pool_value > 0 and deployed / pool_value >= MAX_DEPLOYED_PCT:
            _log(settings.logs_dir, "decisions.log", {
                "pool": pool, "symbol": symbol, "result": "skipped_capital_reserve",
                "reason": f"{deployed / pool_value:.0%} del capital desplegado, tope {MAX_DEPLOYED_PCT:.0%}",
            })
            break

        asym = candidate.asymmetry
        signal_payload = {
            "symbol": symbol,
            "opportunity_score": candidate.score,
            "strategies": candidate.strategies,
            "asymmetry": asdict(asym),
            **candidate.metrics,
        }

        if kronos_ohlcv is not None:
            try:
                kf = kronos_forecast(kronos_ohlcv(symbol))
                if kf is not None:
                    signal_payload["kronos_forecast"] = asdict(kf)
            except Exception as e:
                _log(settings.logs_dir, "decisions.log", {
                    "pool": pool, "symbol": symbol, "result": "kronos_error", "reason": str(e),
                })

        # El bucket se decide ANTES de la revision para poder decirle al
        # panel que tamano real tendria la posicion que esta evaluando.
        bucket = classify_bucket(candidate.metrics.get("size_bucket"), asym.reward_risk)
        cap_pct = min(BUCKET_CAPS[bucket], settings.max_trade_pct) * 100
        position_context = (
            f"Position sizing: if approved, this becomes a '{bucket}' position of at most "
            f"{cap_pct:.2f}% of the portfolio (scaled down further by your own confidence). "
            f"It is deliberately small -- the portfolio is built to absorb many losses of "
            f"this size while a few winners carry the return."
        )

        review = review_signal(settings.gemini_api_key, settings.nvidia_api_key,
                               signal_payload, position_context=position_context)
        decision_record = {"pool": pool, "signal": signal_payload, "review": asdict(review)}

        if review.action != "buy" or review.confidence < 0.6:
            _log(settings.logs_dir, "decisions.log", {**decision_record, "result": "skipped"})
            continue

        # La conviccion combina las dos lecturas independientes: cuanto cree
        # el panel en la tesis, y que tan buena es la oportunidad segun el
        # scanner. Que ambas coincidan es mas informativo que cualquiera sola.
        conviction = review.confidence * (candidate.score / 100)
        size = size_position(pool_value, bucket, conviction, settings.max_trade_pct,
                             expected_value_pct=asym.expected_value_pct)

        if not size.viable:
            _log(settings.logs_dir, "decisions.log", {
                **decision_record, "result": "skipped_sizing", "reason": size.reason,
            })
            continue

        try:
            guard.check_and_record(size.usd, pool_value)
            try:
                result = place_order(symbol, size.usd)
            except Exception as e:
                # Una orden rechazada por el broker no puede matar el ciclo:
                # antes tiraba la excepcion hasta arriba y se perdian todos
                # los candidatos siguientes. Caso real observado: el universo
                # se arma con tickers del mercado publico, pero las ordenes
                # van al entorno demo, que no lista todos esos instrumentos
                # (OKX 51001), incluidas las acciones tokenizadas tipo XMSTR.
                _log(settings.logs_dir, "decisions.log", {
                    **decision_record, "result": "order_rejected", "reason": str(e),
                })
                continue
            # El stop sale de la asimetria medida, no de un porcentaje fijo:
            # es el nivel que se uso para calcular la esperanza, asi que la
            # proteccion real coincide con lo que se prometio al decidir.
            #
            # OKXAdapter.place_market_order no devuelve precio ni cantidad
            # (solo ordId y estado), asi que se consultan aparte. Sin esto
            # la posicion no se registraba y quedaba sin stop ni salida.
            # Una orden cancelada o rechazada por el broker no puede quedar
            # registrada como posicion: seria una posicion fantasma, con un
            # stop vigilando algo que no se tiene y capital contabilizado
            # como desplegado que en realidad esta libre.
            status = str(result.get("status", "")).lower()
            if any(word in status for word in ("cancel", "reject", "inactive", "error")):
                _log(settings.logs_dir, "decisions.log", {
                    **decision_record, "result": "order_not_filled", "status": result.get("status"),
                })
                continue

            entry = result.get("price") or 0.0
            qty = result.get("qty") or 0.0
            if entry <= 0 and get_price is not None:
                try:
                    entry = get_price(symbol)
                    qty = size.usd / entry if entry > 0 else 0.0
                except Exception as e:
                    _log(settings.logs_dir, "decisions.log", {
                        "pool": pool, "symbol": symbol, "result": "position_record_error",
                        "reason": f"orden ejecutada pero no se pudo registrar la posicion: {e}",
                    })
            if entry > 0:
                positions.open(symbol, entry_price=entry, qty=qty,
                               stop=round(entry * (1 - asym.stop_pct / 100), 8))
            _log(settings.logs_dir, "decisions.log", {
                **decision_record, "result": "executed", "sizing": asdict(size),
            })
            _log(settings.logs_dir, "trades.log", {"pool": pool, "sizing": asdict(size), **result})
            executed += 1
        except SpendLimitError as e:
            # Sin presupuesto no tiene sentido seguir ofreciendo candidatos.
            _log(settings.logs_dir, "decisions.log", {
                **decision_record, "result": "rejected_by_spend_guard", "reason": str(e),
            })
            break

    return executed


def _check_deployment_alert(settings, pool, scan_result, executed, pool_value) -> None:
    """Distingue "no habia oportunidades" de "el sistema no fue capaz de
    encontrarlas". Son cosas opuestas y en el log viejo se veian igual:
    ambas eran silencio."""
    if executed > 0:
        return

    alert = {
        "pool": pool,
        "result": "capital_deployment_alert",
        "assets_scanned": scan_result.scanned,
        "shortlisted": len(scan_result.shortlist),
        "rejection_reasons": scan_result.rejection_summary,
        "pool_value": round(pool_value, 2),
    }
    if not scan_result.shortlist:
        alert["diagnosis"] = "NO_OPPORTUNITIES_FOUND"
        alert["detail"] = f"ninguno de los {scan_result.scanned} activos escaneados tuvo esperanza positiva"
    else:
        best = scan_result.shortlist[0]
        alert["diagnosis"] = "SYSTEM_FAILED_TO_DEPLOY"
        alert["detail"] = (
            f"habia {len(scan_result.shortlist)} candidatos con esperanza positiva "
            f"(mejor: {best.symbol} score {best.score}) y no se ejecuto ninguno -- "
            f"revisar umbral del panel, sizing o guards"
        )
    _log(settings.logs_dir, "decisions.log", alert)


def _loss_fraction(pos: dict, exit_price: float, pool_value: float) -> float | None:
    """Cuanto costo esta salida como fraccion del pool. None si no se puede
    calcular, en cuyo caso el breaker cuenta la perdida como material (el
    lado conservador)."""
    if pool_value <= 0:
        return None
    entry, qty = pos.get("entry_price", 0.0), pos.get("qty", 0.0)
    if entry <= 0 or qty <= 0:
        return None
    loss_usd = max(0.0, (entry - exit_price) * qty)
    return loss_usd / pool_value


def _manage_crypto_exits(settings, okx, positions, breaker, pool_value) -> None:
    """Cierra posiciones cuyo stop se toco. Antes de esto la pierna de
    crypto solo compraba: no tenia salidas ni registro de posiciones, asi
    que una posicion abierta quedaba a la deriva para siempre."""
    for inst_id, pos in list(positions.all_open().items()):
        try:
            price = okx.get_last_price(inst_id)
            # Se loguea el precio que YA se pidio para chequear el stop --
            # sin esto el dashboard no tiene forma de mostrar precio actual
            # ni P&L de una posicion sin hacer su propia llamada al broker.
            _log(settings.logs_dir, "position_marks.jsonl", {"pool": "crypto", "symbol": inst_id, "price": price})
        except Exception as e:
            _log(settings.logs_dir, "decisions.log", {
                "pool": "crypto", "symbol": inst_id, "result": "price_lookup_error", "reason": str(e),
            })
            continue

        if price > pos["stop"]:
            continue

        try:
            # OKX dimensiona las ventas en moneda base, no en USDT.
            result = okx.place_market_order(inst_id, pos["qty"], "sell")
            won = price > pos["entry_price"]
            positions.close(inst_id)
            breaker.record_trade_result(won, loss_pct_of_pool=_loss_fraction(pos, price, pool_value))
            _log(settings.logs_dir, "trades.log", {"pool": "crypto", "reason": "stop_loss", **result})
            _log(settings.logs_dir, "decisions.log", {
                "pool": "crypto", "symbol": inst_id, "result": "stopped_out",
                "won": won, "stop": pos["stop"], "price": price,
            })
        except Exception as e:
            _log(settings.logs_dir, "decisions.log", {
                "pool": "crypto", "symbol": inst_id, "result": "exit_error", "reason": str(e),
            })


def run_stocks(settings) -> None:
    if not _market_is_open():
        _log(settings.logs_dir, "decisions.log", {"pool": "stocks", "result": "market_closed"})
        return

    guard = SpendGuard("stocks", settings.state_dir, settings.max_trade_pct, settings.daily_loss_halt_pct)
    breaker = CircuitBreaker("stocks", settings.state_dir, settings.daily_loss_halt_pct, settings.max_consecutive_losses)
    positions = PositionTracker("stocks", settings.state_dir)

    ibkr = IBKRAdapter(settings.ibkr_host, settings.ibkr_port, settings.ibkr_client_id)
    try:
        pool_value = ibkr.get_account_value()
        breaker.check(pool_value)
        _snapshot_portfolio(settings, "stocks", pool_value, positions)

        # Manage existing positions first: exit on a stop-loss hit, otherwise
        # trail the stop up if a fresh, higher box has formed.
        for symbol, pos in list(positions.all_open().items()):
            price = ibkr.get_last_price(symbol)
            _log(settings.logs_dir, "position_marks.jsonl", {"pool": "stocks", "symbol": symbol, "price": price})
            if price <= pos["stop"]:
                result = ibkr.place_market_order_by_qty(symbol, pos["qty"], "SELL")
                won = price > pos["entry_price"]
                positions.close(symbol)
                breaker.record_trade_result(won, loss_pct_of_pool=_loss_fraction(pos, price, pool_value))
                _log(settings.logs_dir, "trades.log", {"pool": "stocks", "reason": "stop_loss", **result})
                _log(settings.logs_dir, "decisions.log", {
                    "pool": "stocks", "symbol": symbol, "result": "stopped_out", "won": won,
                    "stop": pos["stop"], "price": price,
                })
                continue

            box = compute_box(symbol)
            if box and box.box_bottom > pos["stop"]:
                positions.update_stop(symbol, box.box_bottom)
                _log(settings.logs_dir, "decisions.log", {
                    "pool": "stocks", "symbol": symbol, "result": "stop_trailed", "new_stop": box.box_bottom,
                })

        # Descubrimiento amplio sobre todo el watchlist. Antes esto era
        # scan_for_breakouts() -> candidates[0]: una sola estrategia (la caja
        # de Darvas) decidiendo sobre un solo simbolo. Medido en la auditoria:
        # los 20 tickers del watchlist daban 20 rechazos, porque el screener
        # los elige por ser los mayores movers del dia (ancho promedio 24%) y
        # Darvas exige consolidacion estrecha (<=12%). Buscaba en un lugar
        # donde su propia estrategia no podia encontrar nada.
        held = set(positions.all_open().keys())
        universe = resolve_stock_universe()

        price_data = {}
        histories = {}
        for symbol in universe:
            if symbol in held:
                continue
            try:
                hist = yf.Ticker(symbol).history(period="6mo")
            except Exception:
                continue
            if hist.empty or "Close" not in hist:
                continue
            histories[symbol] = hist
            stock_signals = [p.strategy for p in evaluate_stock_strategies(hist)]
            box = compute_box(symbol, history=hist)
            if box is not None and box.breakout:
                stock_signals.append("darvas_breakout")
            price_data[symbol] = {
                "closes": hist["Close"].tolist(),
                "highs": hist["High"].tolist(),
                "lows": hist["Low"].tolist(),
                "stock_strategies": stock_signals,
            }

        scan_result = scan(price_data)
        # Las estrategias especificas de acciones se suman a las genericas
        # del scanner: son evidencia adicional de confluencia, no un gate.
        for candidate in scan_result.shortlist:
            extra = candidate.metrics.get("stock_strategies") or []
            candidate.strategies = list(dict.fromkeys(candidate.strategies + extra))

        _log(settings.logs_dir, "decisions.log", {
            "pool": "stocks", "result": "scan", **scan_result.as_metrics(),
        })

        # Contexto fundamental solo para los que van a llegar al panel: son
        # llamadas de red por simbolo y no vale la pena pagarlas por los 20.
        for candidate in scan_result.shortlist[:MAX_CANDIDATES_PER_CYCLE]:
            try:
                candidate.metrics["insider"] = asdict(insider_sentiment(candidate.symbol))
                candidate.metrics["analyst"] = asdict(analyst_consensus(candidate.symbol))
            except Exception as e:
                _log(settings.logs_dir, "decisions.log", {
                    "pool": "stocks", "symbol": candidate.symbol,
                    "result": "insider_signal_error", "reason": str(e),
                })
            try:
                candidate.metrics["news"] = asdict(news_sentiment(candidate.symbol))
            except Exception as e:
                _log(settings.logs_dir, "decisions.log", {
                    "pool": "stocks", "symbol": candidate.symbol,
                    "result": "news_sentiment_error", "reason": str(e),
                })

        executed = _evaluate_candidates(
            settings, scan_result, pool="stocks", pool_value=pool_value, held=held,
            guard=guard, positions=positions,
            place_order=lambda symbol, usd: ibkr.place_market_order(symbol, usd, "BUY"),
            kronos_ohlcv=(lambda symbol: histories[symbol]) if settings.enable_kronos_forecast else None,
            get_price=ibkr.get_last_price,
        )

        _check_deployment_alert(settings, "stocks", scan_result, executed, pool_value)

    except TradingHalted as e:
        _log(settings.logs_dir, "decisions.log", {"pool": "stocks", "result": "halted", "reason": str(e)})
    except SpendLimitError as e:
        _log(settings.logs_dir, "decisions.log", {"pool": "stocks", "result": "rejected_by_spend_guard", "reason": str(e)})
    finally:
        ibkr.disconnect()


def run_crypto(settings) -> None:
    guard = SpendGuard("crypto", settings.state_dir, settings.max_trade_pct, settings.daily_loss_halt_pct)
    breaker = CircuitBreaker("crypto", settings.state_dir, settings.daily_loss_halt_pct, settings.max_consecutive_losses)

    positions = PositionTracker("crypto", settings.state_dir)

    okx = OKXAdapter(settings.okx_api_key, settings.okx_api_secret, settings.okx_api_passphrase, settings.okx_demo_flag)

    pool_value = okx.get_usdt_balance()
    breaker.check(pool_value)
    _snapshot_portfolio(settings, "crypto", pool_value, positions)

    # Las salidas van primero: liberar capital de posiciones que tocaron su
    # stop antes de evaluar en que entrar.
    _manage_crypto_exits(settings, okx, positions, breaker, pool_value)

    universe = liquid_crypto_universe(okx, top_n=settings.crypto_universe_size)

    # One fetch of daily history for the whole universe, reused by both the
    # tournament and the live indicator check below.
    closes_by_symbol = {}
    for inst_id in universe:
        try:
            closes_by_symbol[inst_id] = okx.get_candles(inst_id, bar="1D", limit=100)
        except Exception:
            continue

    # Shadow tournament: settle what's come due, then record what every
    # strategy would buy right now. Places no orders -- this is the
    # evidence layer that will eventually say which method to trust.
    tournament_db = settings.state_dir / "tournament.db"
    try:
        settled = tournament.score_due(tournament_db, okx.get_last_price)
        proposed = tournament.record_universe(tournament_db, "crypto", closes_by_symbol)
        _log(settings.logs_dir, "decisions.log", {
            "pool": "crypto", "result": "tournament", "settled": settled,
            "proposed": proposed, "scanned": len(closes_by_symbol),
        })
    except Exception as e:
        # The tournament is observational -- it must never be able to stop
        # the live path from running.
        _log(settings.logs_dir, "decisions.log", {"pool": "crypto", "result": "tournament_error", "reason": str(e)})

    # Capitalizacion de mercado para clasificar el bucket de riesgo. Una
    # sola llamada para todo el universo: el tier gratuito de CoinGecko
    # tira 429 a partir de ~5 llamadas seguidas.
    fundamentals = {}
    try:
        fundamentals = fetch_market_fundamentals()
    except Exception as e:
        _log(settings.logs_dir, "decisions.log", {
            "pool": "crypto", "result": "fundamentals_error", "reason": str(e),
        })

    price_data = {}
    for inst_id, closes in closes_by_symbol.items():
        fund = fundamentals.get(okx_inst_id_to_symbol(inst_id))
        price_data[inst_id] = {
            "closes": closes,
            "size_bucket": fund.size_bucket if fund else None,
            "market_cap_usd": fund.market_cap_usd if fund else None,
        }

    scan_result = scan(price_data)
    _log(settings.logs_dir, "decisions.log", {
        "pool": "crypto", "result": "scan", **scan_result.as_metrics(),
    })

    held = set(positions.all_open().keys())
    executed = _evaluate_candidates(
        settings, scan_result, pool="crypto", pool_value=pool_value, held=held,
        guard=guard, positions=positions,
        place_order=lambda inst_id, usd: okx.place_market_order(inst_id, usd, "buy"),
        kronos_ohlcv=(lambda inst_id: okx.get_candles_ohlcv(inst_id)) if settings.enable_kronos_forecast else None,
        get_price=okx.get_last_price,
    )

    _check_deployment_alert(settings, "crypto", scan_result, executed, pool_value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["paper", "live"], required=True)
    parser.add_argument("--pool", choices=["stocks", "crypto", "both"], default="both")
    args = parser.parse_args()

    import os
    os.environ["TRADING_MODE"] = args.mode
    settings = load_settings()

    try:
        if args.pool in ("stocks", "both"):
            run_stocks(settings)
        if args.pool in ("crypto", "both"):
            run_crypto(settings)
    except Exception:
        _log(settings.logs_dir, "decisions.log", {"pool": args.pool, "result": "error", "traceback": traceback.format_exc()})
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
