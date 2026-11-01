"""Daily portfolio backtest with next-session orders and realistic constraints."""

import json
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from a_share_quant.accounting.account import Account
from a_share_quant.accounting.config import AccountConfig, load_account_config
from a_share_quant.execution.costs import CostConfig, CostModel
from a_share_quant.execution.order import OrderRequest, TradingConstraintEngine
from a_share_quant.execution.rules import TradingRuleBook
from a_share_quant.strategy.config import StrategyConfig, load_strategy_config
from a_share_quant.strategy.momentum import SignalBundle


@dataclass(frozen=True)
class BacktestResult:
    scenario: str
    snapshots: pd.DataFrame
    orders: pd.DataFrame
    summary: dict[str, object]


def _zero_cost_model(stress_multiplier: Decimal) -> CostModel:
    return CostModel(
        CostConfig(
            commission_rate=Decimal("0"),
            minimum_commission=Decimal("0"),
            stamp_duty_rate=Decimal("0"),
            transfer_fee_rate=Decimal("0"),
            base_slippage_bps=Decimal("0"),
            impact_coefficient_bps=Decimal("0"),
            impact_reference_participation=Decimal("0.01"),
            impact_exponent=Decimal("0.5"),
            max_participation=Decimal("0.10"),
            stress_multipliers=(stress_multiplier,),
        )
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _adjust_target_weights(
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    config: StrategyConfig,
) -> dict[str, float]:
    symbols = set(current_weights) | set(target_weights)
    desired = {symbol: float(target_weights.get(symbol, 0.0)) for symbol in symbols}
    current = {symbol: float(current_weights.get(symbol, 0.0)) for symbol in symbols}
    exits = {symbol for symbol in symbols if current[symbol] > 0 and desired[symbol] == 0}
    forced_turnover = 0.5 * sum(current[symbol] for symbol in exits)
    adjustable = symbols - exits
    adjustable_turnover = 0.5 * sum(
        abs(desired[symbol] - current[symbol]) for symbol in adjustable
    )
    if not exits and adjustable_turnover < config.min_rebalance_turnover:
        desired = current
        adjustable_turnover = 0.0
    available_turnover = max(0.0, config.max_rebalance_turnover - forced_turnover)
    if adjustable_turnover > available_turnover:
        scale = available_turnover / adjustable_turnover if adjustable_turnover > 0 else 0
        for symbol in adjustable:
            desired[symbol] = current[symbol] + scale * (
                desired[symbol] - current[symbol]
            )
    for symbol in symbols:
        difference = abs(desired[symbol] - current[symbol])
        if (
            current[symbol] > 0
            and desired[symbol] > 0
            and difference < config.min_trade_weight
        ):
            desired[symbol] = current[symbol]
    return {symbol: weight for symbol, weight in desired.items() if weight > 0}


def _update_risk_state(
    risk_scale: float,
    risk_cooldown: int,
    peak_equity: float,
    equity: float,
    config: StrategyConfig,
) -> tuple[float, int, float]:
    if risk_scale == 0:
        risk_cooldown -= 1
        if risk_cooldown <= 0:
            return config.drawdown_reentry_exposure, 0, equity
        return risk_scale, risk_cooldown, peak_equity

    peak_equity = max(peak_equity, equity)
    drawdown = equity / peak_equity - 1
    if drawdown <= -config.drawdown_stop:
        return 0.0, config.drawdown_cooldown_days, equity
    if drawdown <= -config.drawdown_reduce_2:
        return config.drawdown_level_2_exposure, 0, peak_equity
    if drawdown <= -config.drawdown_reduce_1:
        return config.drawdown_level_1_exposure, 0, peak_equity
    if drawdown >= -0.04:
        return 1.0, 0, peak_equity
    return risk_scale, risk_cooldown, peak_equity


def _financial_summary(
    snapshots: pd.DataFrame,
    orders: pd.DataFrame,
) -> dict[str, object]:
    equity = snapshots["equity"].astype(float)
    daily_returns = equity.pct_change(fill_method=None).dropna()
    start = pd.Timestamp(snapshots["trade_date"].iloc[0])
    end = pd.Timestamp(snapshots["trade_date"].iloc[-1])
    years = max((end - start).days / 365.25, 1 / 252)
    total_return = float(equity.iloc[-1] / float(snapshots["initial_cash"].iloc[0]) - 1)
    annual_return = (1 + total_return) ** (1 / years) - 1
    volatility = float(daily_returns.std(ddof=0) * np.sqrt(252))
    sharpe = (
        float(daily_returns.mean() / daily_returns.std(ddof=0) * np.sqrt(252))
        if daily_returns.std(ddof=0) > 0
        else 0.0
    )
    drawdown = equity / equity.cummax() - 1
    max_drawdown = float(drawdown.min())
    benchmark_return = float(snapshots["benchmark_total_return"].iloc[-1])
    filled = orders[orders["status"] == "FILLED"]
    rejected = orders[orders["status"] == "REJECTED"]
    return {
        "start_date": start.date(),
        "end_date": end.date(),
        "trading_days": len(snapshots),
        "initial_cash": float(snapshots["initial_cash"].iloc[0]),
        "final_equity": round(float(equity.iloc[-1]), 2),
        "total_return": round(total_return, 8),
        "annualized_return": round(annual_return, 8),
        "annualized_volatility": round(volatility, 8),
        "sharpe_ratio": round(sharpe, 6),
        "max_drawdown": round(max_drawdown, 8),
        "calmar_ratio": round(annual_return / abs(max_drawdown), 6)
        if max_drawdown < 0
        else None,
        "benchmark_total_return": round(benchmark_return, 8),
        "excess_return": round(total_return - benchmark_return, 8),
        "average_gross_exposure": round(float(snapshots["gross_exposure"].mean()), 8),
        "average_cash_weight": round(float(snapshots["cash_weight"].mean()), 8),
        "average_holding_count": round(float(snapshots["holding_count"].mean()), 4),
        "annualized_one_way_turnover": round(
            float(snapshots["rebalance_turnover"].sum()) / years, 6
        ),
        "filled_orders": len(filled),
        "rejected_orders": len(rejected),
        "commission_paid": round(float(filled["commission"].sum()), 2),
        "total_transaction_cost": round(float(filled["total_cost"].sum()), 2),
        "rejection_reasons": rejected["reason"].value_counts().to_dict()
        if not rejected.empty
        else {},
    }


class StrategyBacktestEngine:
    def __init__(
        self,
        project_root: Path,
        *,
        strategy_config: StrategyConfig | None = None,
        account_config: AccountConfig | None = None,
        drawdown_control: bool = False,
    ) -> None:
        self.project_root = project_root
        self.strategy_config = strategy_config or load_strategy_config(project_root)
        self.account_config = account_config or load_account_config(project_root)
        self.drawdown_control = drawdown_control

    def _apply_actions(
        self,
        account: Account,
        trade_date: pd.Timestamp,
        actions: pd.DataFrame,
    ) -> None:
        if actions.empty:
            return
        for action in actions[
            (actions["event_date"] == trade_date)
            & (actions["action_type"] == "share_adjustment")
        ].itertuples(index=False):
            if str(action.symbol) in account.positions:
                account.apply_share_adjustment(
                    trade_date,
                    str(action.symbol),
                    action.share_multiplier,
                    note=f"derived share adjustment; {action.derivation}",
                )
        for action in actions[
            (actions["event_date"] == trade_date)
            & (actions["action_type"] == "cash_distribution")
        ].itertuples(index=False):
            if str(action.symbol) in account.positions:
                account.apply_cash_distribution(
                    trade_date,
                    str(action.symbol),
                    action.cash_per_unit,
                    note=f"cash distribution; {action.derivation}",
                )

    def run(
        self,
        *,
        daily: pd.DataFrame,
        actions: pd.DataFrame,
        calendar: pd.DataFrame,
        benchmark: pd.DataFrame,
        signal_bundle: SignalBundle,
        rule_book: TradingRuleBook,
        cost_model: CostModel,
        scenario: str,
        stress_multiplier: Decimal,
        with_costs: bool,
        start_date: object | None = None,
        end_date: object | None = None,
    ) -> BacktestResult:
        daily = daily.copy()
        daily["trade_date"] = pd.to_datetime(daily["trade_date"]).dt.normalize()
        previous_close_lookup: dict[tuple[str, pd.Timestamp], float] = {}
        for symbol, frame in daily.groupby("symbol"):
            prior = frame.sort_values("trade_date")["close"].shift(1)
            for trade_date, previous_close in zip(
                frame["trade_date"], prior, strict=True
            ):
                if pd.notna(previous_close):
                    previous_close_lookup[(str(symbol), pd.Timestamp(trade_date))] = float(
                        previous_close
                    )
        actions = actions.copy()
        actions["event_date"] = pd.to_datetime(actions["event_date"]).dt.normalize()
        account = Account(
            self.account_config.initial_cash,
            lot_size=self.account_config.lot_size,
            settlement_days=1,
        )
        active_cost_model = cost_model if with_costs else _zero_cost_model(stress_multiplier)
        engine = TradingConstraintEngine(
            rule_book,
            cost_model=active_cost_model,
            stress_multiplier=stress_multiplier,
        )
        bars_by_date = {
            pd.Timestamp(trade_date).normalize(): frame.set_index("symbol")
            for trade_date, frame in daily.groupby("trade_date")
        }
        benchmark_series = benchmark.set_index("trade_date")["close"].sort_index()
        open_dates = pd.DatetimeIndex(
            pd.to_datetime(calendar.loc[calendar["is_open"], "calendar_date"]).sort_values()
        )
        execution_targets = {
            trade_date: weights
            for trade_date, weights in signal_bundle.execution_targets.items()
            if (start_date is None or trade_date >= pd.Timestamp(start_date))
            and (end_date is None or trade_date <= pd.Timestamp(end_date))
        }
        execution_signals = {
            trade_date: signal_date
            for trade_date, signal_date in signal_bundle.execution_signals.items()
            if trade_date in execution_targets
        }
        invested_dates = [
            trade_date
            for trade_date, weights in execution_targets.items()
            if weights
        ]
        if not invested_dates and start_date is None:
            raise ValueError("Strategy produced no investable target portfolios")
        if start_date is not None:
            eligible_starts = open_dates[open_dates >= pd.Timestamp(start_date)]
            if eligible_starts.empty:
                raise ValueError("Requested backtest start is outside the trading calendar")
            start_date = pd.Timestamp(eligible_starts[0]).normalize()
        else:
            start_date = min(invested_dates)
        effective_end = (
            min(pd.Timestamp(end_date), daily["trade_date"].max())
            if end_date is not None
            else daily["trade_date"].max()
        )
        backtest_dates = open_dates[
            (open_dates >= start_date) & (open_dates <= effective_end)
        ]
        last_close: dict[str, float] = {}
        benchmark_base: float | None = None
        last_benchmark_close: float | None = None
        snapshots: list[dict[str, object]] = []
        orders: list[dict[str, object]] = []
        risk_scale = 1.0
        risk_cooldown = 0
        peak_equity = float(account.initial_cash)
        previous_session_date: pd.Timestamp | None = None
        trailing_peaks: dict[str, float] = {}

        for trade_date in backtest_dates:
            trade_date = pd.Timestamp(trade_date).normalize()
            account.begin_session(trade_date)
            self._apply_actions(account, trade_date, actions)
            bars = bars_by_date.get(trade_date)
            bar_by_symbol = bars if bars is not None else pd.DataFrame()
            has_rebalance = trade_date in execution_targets
            rebalance_turnover = 0.0
            rebalance_orders = 0
            risk_exit_symbols: set[str] = set()

            stop_loss_pct = self.strategy_config.position_stop_loss_pct
            trailing_stop_pct = self.strategy_config.trailing_stop_pct
            trailing_activation_pct = self.strategy_config.trailing_activation_pct
            has_risk_exit = stop_loss_pct is not None or trailing_stop_pct is not None
            if has_risk_exit and previous_session_date is not None:
                for symbol, position in sorted(account.positions.items()):
                    if position.quantity <= 0 or symbol not in last_close:
                        continue
                    previous_close = last_close[symbol]
                    fixed_stop_triggered = (
                        stop_loss_pct is not None
                        and previous_close
                        <= float(position.average_cost) * (1 - stop_loss_pct)
                    )
                    trailing_peak = trailing_peaks.get(symbol)
                    trailing_stop_triggered = bool(
                        trailing_stop_pct is not None
                        and trailing_peak is not None
                        and trailing_peak
                        >= float(position.average_cost) * (1 + trailing_activation_pct)
                        and previous_close <= trailing_peak * (1 - trailing_stop_pct)
                    )
                    if not fixed_stop_triggered and not trailing_stop_triggered:
                        continue
                    bar = (
                        bar_by_symbol.loc[symbol]
                        if not bar_by_symbol.empty and symbol in bar_by_symbol.index
                        else None
                    )
                    order = OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        signal_date=previous_session_date,
                        execution_date=trade_date,
                        reason=(
                            "position stop loss"
                            if fixed_stop_triggered
                            else "position trailing stop"
                        ),
                    )
                    result = engine.execute(
                        account,
                        order,
                        bar=bar,
                        previous_close=last_close.get(symbol),
                        limit_reference_close=self._limit_reference(
                            symbol,
                            trade_date,
                            last_close.get(symbol),
                            actions,
                        ),
                    )
                    order_row = self._order_row(
                        scenario,
                        previous_session_date,
                        trade_date,
                        order,
                        result,
                    )
                    if result.status == "FILLED":
                        order_row["reason"] = (
                            "POSITION_STOP_LOSS"
                            if fixed_stop_triggered
                            else "POSITION_TRAILING_STOP"
                        )
                        risk_exit_symbols.add(symbol)
                        rebalance_turnover += float(
                            result.fill_quantity * (result.market_price or Decimal("0"))
                        )
                    orders.append(order_row)
                    rebalance_orders += 1

            if has_rebalance:
                signal_date = execution_signals[trade_date]
                target_weights = execution_targets[trade_date]
                if self.drawdown_control:
                    target_weights = {
                        symbol: weight * risk_scale
                        for symbol, weight in target_weights.items()
                    }
                open_prices: dict[str, float] = {}
                for symbol in account.positions:
                    if not bar_by_symbol.empty and symbol in bar_by_symbol.index:
                        open_prices[symbol] = float(bar_by_symbol.loc[symbol, "open"])
                    elif symbol in last_close:
                        open_prices[symbol] = last_close[symbol]
                opening_market_value = sum(
                    float(position.quantity) * open_prices.get(symbol, last_close.get(symbol, 0))
                    for symbol, position in account.positions.items()
                )
                opening_equity = _money(account.cash + Decimal(str(opening_market_value)))
                if opening_equity <= 0:
                    raise ValueError(f"Portfolio equity is not positive on {trade_date.date()}")
                current_weights = {
                    symbol: (
                        float(position.quantity) * open_prices[symbol] / float(opening_equity)
                        if symbol in open_prices
                        else 0.0
                    )
                    for symbol, position in account.positions.items()
                    if position.quantity > 0
                }
                adjusted_weights = _adjust_target_weights(
                    current_weights,
                    target_weights,
                    self.strategy_config,
                )
                adjusted_weights = {
                    symbol: weight
                    for symbol, weight in adjusted_weights.items()
                    if symbol not in risk_exit_symbols
                }
                target_quantities: dict[str, Decimal] = {}
                for symbol, weight in adjusted_weights.items():
                    if bar_by_symbol.empty or symbol not in bar_by_symbol.index:
                        continue
                    price = Decimal(str(bar_by_symbol.loc[symbol, "open"]))
                    lots = (
                        Decimal(str(opening_equity))
                        * Decimal(str(weight))
                        / price
                        / Decimal(self.account_config.lot_size)
                    ).to_integral_value(rounding=ROUND_DOWN)
                    target_quantities[symbol] = lots * Decimal(self.account_config.lot_size)

                sell_symbols = set(account.positions) | set(target_quantities)
                for symbol in sorted(sell_symbols):
                    position = account.positions.get(symbol)
                    current_quantity = position.quantity if position is not None else Decimal("0")
                    target_quantity = target_quantities.get(symbol, Decimal("0"))
                    quantity = current_quantity - target_quantity
                    if quantity <= 0:
                        continue
                    lot = Decimal(rule_book.for_date(symbol, trade_date).sell_lot_size)
                    if quantity != current_quantity:
                        quantity = (quantity / lot).to_integral_value(
                            rounding=ROUND_DOWN
                        ) * lot
                    if quantity <= 0:
                        continue
                    order = OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=quantity,
                        signal_date=signal_date,
                        execution_date=trade_date,
                        reason="portfolio rebalance",
                    )
                    bar = bar_by_symbol.loc[symbol] if symbol in bar_by_symbol.index else None
                    result = engine.execute(
                        account,
                        order,
                        bar=bar,
                        previous_close=previous_close_lookup.get((symbol, trade_date)),
                        limit_reference_close=self._limit_reference(
                            symbol,
                            trade_date,
                            previous_close_lookup.get((symbol, trade_date)),
                            actions,
                        ),
                    )
                    orders.append(self._order_row(scenario, signal_date, trade_date, order, result))
                    rebalance_orders += 1
                    if result.status == "FILLED":
                        rebalance_turnover += float(
                            result.fill_quantity * (result.market_price or Decimal("0"))
                        )

                for symbol, target_quantity in sorted(target_quantities.items()):
                    position = account.positions.get(symbol)
                    current_quantity = position.quantity if position is not None else Decimal("0")
                    quantity = target_quantity - current_quantity
                    lot = Decimal(rule_book.for_date(symbol, trade_date).buy_lot_size)
                    quantity = (quantity / lot).to_integral_value(rounding=ROUND_DOWN) * lot
                    if quantity <= 0:
                        continue
                    order = OrderRequest(
                        symbol=symbol,
                        side="BUY",
                        quantity=quantity,
                        signal_date=signal_date,
                        execution_date=trade_date,
                        reason="portfolio rebalance",
                    )
                    bar = bar_by_symbol.loc[symbol]
                    result = engine.execute(
                        account,
                        order,
                        bar=bar,
                        previous_close=previous_close_lookup.get((symbol, trade_date)),
                        limit_reference_close=self._limit_reference(
                            symbol,
                            trade_date,
                            previous_close_lookup.get((symbol, trade_date)),
                            actions,
                        ),
                    )
                    orders.append(self._order_row(scenario, signal_date, trade_date, order, result))
                    rebalance_orders += 1
                    if result.status == "FILLED":
                        rebalance_turnover += float(
                            result.fill_quantity * (result.market_price or Decimal("0"))
                        )

            for symbol, row in bar_by_symbol.iterrows():
                last_close[str(symbol)] = float(row["close"])
            for symbol, position in account.positions.items():
                if position.quantity <= 0 or symbol not in last_close:
                    trailing_peaks.pop(symbol, None)
                    continue
                trailing_peaks[symbol] = max(
                    trailing_peaks.get(symbol, float(position.average_cost)),
                    last_close[symbol],
                )
            if trade_date in benchmark_series.index:
                last_benchmark_close = float(benchmark_series.loc[trade_date])
            if last_benchmark_close is None:
                benchmark_value = float(self.account_config.initial_cash)
            else:
                if benchmark_base is None:
                    benchmark_base = last_benchmark_close
                benchmark_value = (
                    float(self.account_config.initial_cash)
                    * last_benchmark_close
                    / benchmark_base
                )
            snapshot = account.mark_to_market(trade_date, last_close)
            market_value = float(snapshot.market_value)
            equity = float(snapshot.equity)
            one_way_turnover = rebalance_turnover / (2 * equity) if equity > 0 else 0.0
            snapshots.append(
                {
                    "scenario": scenario,
                    "signal_date": (
                        execution_signals[trade_date]
                        if has_rebalance
                        else pd.NaT
                    ),
                    "trade_date": trade_date,
                    "initial_cash": float(account.initial_cash),
                    "cash": float(snapshot.cash),
                    "market_value": market_value,
                    "equity": equity,
                    "total_return": float(snapshot.total_return),
                    "gross_exposure": market_value / equity if equity > 0 else 0.0,
                    "cash_weight": float(snapshot.cash) / equity if equity > 0 else 0.0,
                    "holding_count": len(snapshot.position_quantities),
                    "rebalance_turnover": one_way_turnover,
                    "rebalance_orders": rebalance_orders,
                    "benchmark_close": last_benchmark_close,
                    "benchmark_equity": benchmark_value,
                    "benchmark_total_return": benchmark_value
                    / float(account.initial_cash)
                    - 1,
                    "risk_scale": risk_scale,
                    "reconciled": snapshot.reconciled,
                    "positions_json": json.dumps(
                        {key: float(value) for key, value in snapshot.position_quantities},
                        sort_keys=True,
                    ),
                    "available_positions_json": json.dumps(
                        {key: float(value) for key, value in snapshot.available_quantities},
                        sort_keys=True,
                    ),
                }
            )
            if self.drawdown_control:
                risk_scale, risk_cooldown, peak_equity = _update_risk_state(
                    risk_scale,
                    risk_cooldown,
                    peak_equity,
                    equity,
                    self.strategy_config,
                )
            previous_session_date = trade_date

        snapshot_frame = pd.DataFrame(snapshots)
        order_columns = [
            "scenario",
            "signal_date",
            "execution_date",
            "symbol",
            "side",
            "requested_quantity",
            "status",
            "reason",
            "market_price",
            "fill_price",
            "fill_quantity",
            "gross_notional",
            "commission",
            "slippage_cost",
            "impact_cost",
            "total_cost",
            "stress_multiplier",
        ]
        order_frame = pd.DataFrame(orders, columns=order_columns)
        summary = {
            "scenario": scenario,
            "with_costs": with_costs,
            "stress_multiplier": float(stress_multiplier),
            **_financial_summary(snapshot_frame, order_frame),
        }
        return BacktestResult(
            scenario=scenario,
            snapshots=snapshot_frame,
            orders=order_frame,
            summary=summary,
        )

    @staticmethod
    def _limit_reference(
        symbol: str,
        trade_date: pd.Timestamp,
        previous_close: float | None,
        actions: pd.DataFrame,
    ) -> float | None:
        if previous_close is None:
            return None
        reference = float(previous_close)
        for action in actions[
            (actions["symbol"].astype(str).str.zfill(6) == symbol)
            & (actions["event_date"] == trade_date)
        ].itertuples(index=False):
            if action.action_type == "share_adjustment":
                reference /= float(action.share_multiplier)
            elif action.action_type == "cash_distribution":
                reference -= float(action.cash_per_unit)
        return reference

    @staticmethod
    def _order_row(
        scenario: str,
        signal_date: pd.Timestamp,
        execution_date: pd.Timestamp,
        order: OrderRequest,
        result: object,
    ) -> dict[str, object]:
        return {
            "scenario": scenario,
            "signal_date": pd.Timestamp(signal_date).normalize(),
            "execution_date": pd.Timestamp(execution_date).normalize(),
            "symbol": order.symbol,
            "side": order.side,
            "requested_quantity": float(order.quantity),
            "status": result.status,
            "reason": result.reason,
            "market_price": float(result.market_price) if result.market_price else None,
            "fill_price": float(result.fill_price) if result.fill_price else None,
            "fill_quantity": float(result.fill_quantity),
            "gross_notional": float(result.gross_notional),
            "commission": float(result.commission),
            "slippage_cost": float(result.slippage_cost),
            "impact_cost": float(result.impact_cost),
            "total_cost": float(result.total_cost),
            "stress_multiplier": float(result.stress_multiplier),
        }
