#!/usr/bin/env python3
"""
Custom Backtest Engine (커스텀 백테스트 엔진)
============================================
- 외부 백테스트 패키지(backtrader, zipline 등) 없이 직접 구현
- 다른 프로젝트에서도 재사용 가능하도록 설계
- 슬리피지, 거래 수수료, 증권거래세 지원

지원 전략:
  - run_equal_weight(): 동일 비중 매수 후 보유 (Buy & Hold)
  - run_rebalance(): 주기적 리밸런싱
  - run_custom(): 사용자 정의 시그널 기반

사용 예시:
    from backtester import BacktestEngine

    engine = BacktestEngine(
        initial_capital=100_000_000,
        slippage_pct=0.3,        # 슬리피지 0.3%
        commission_pct=0.015,    # 거래 수수료 0.015%
        tax_pct=0.20,            # 증권거래세 0.20% (매도 시만)
    )
    engine.add_price_data('005930', prices, name='삼성전자')
    engine.set_benchmark(kospi_prices)
    engine.run_equal_weight(['005930', '000660'])
    results = engine.get_results()
"""

import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ============================================================
# 거래 비용 설정
# ============================================================
@dataclass
class CostConfig:
    """거래 비용 설정"""
    slippage_pct: float = 0.0       # 슬리피지 (%)
    commission_pct: float = 0.0     # 거래 수수료 (%, 매수/매도 양쪽)
    tax_pct: float = 0.0            # 증권거래세 (%, 매도 시만)


# ============================================================
# 거래 기록
# ============================================================
@dataclass
class TradeRecord:
    """개별 거래 기록"""
    ticker: str
    name: str
    entry_date: str
    entry_price: float      # 원래 시장가
    exec_price: float       # 슬리피지 적용 실행가
    shares: int
    entry_cost: float = 0.0  # 매수 시 발생 비용 (수수료)
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    exec_exit_price: Optional[float] = None
    exit_cost: float = 0.0   # 매도 시 발생 비용 (수수료+세금)
    pnl: float = 0.0         # 비용 차감 후 순손익
    pnl_pct: float = 0.0
    status: str = 'open'


# ============================================================
# 포트폴리오
# ============================================================
class Portfolio:
    """포트폴리오 관리 - 현금, 포지션, 자산, 비용 추적"""

    def __init__(self, initial_capital: float, cost_config: CostConfig = None):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, dict] = {}
        self.equity_history: List[dict] = []
        self.trades: List[TradeRecord] = []
        self.cost = cost_config or CostConfig()

        # 누적 비용 추적
        self.total_slippage_cost = 0.0
        self.total_commission_cost = 0.0
        self.total_tax_cost = 0.0

    def buy(self, ticker: str, price: float, shares: int,
            date: str, name: str = '') -> int:
        """
        매수
        - 슬리피지: 시장가보다 높은 가격에 체결
        - 수수료: 체결 금액의 commission_pct% 차감
        반환: 실제 매수 주수
        """
        if shares <= 0 or price <= 0:
            return 0

        # 슬리피지 적용 (매수: 불리하게 높은 가격)
        exec_price = price * (1 + self.cost.slippage_pct / 100)

        # 수수료 포함 총 비용 계산
        gross_cost = exec_price * shares
        commission = gross_cost * (self.cost.commission_pct / 100)
        total_cost = gross_cost + commission

        # 자금 부족 시 매수 가능 수량 재계산
        if total_cost > self.cash:
            # 수수료 포함해서 살 수 있는 최대 주수
            max_shares = int(self.cash / (exec_price * (1 + self.cost.commission_pct / 100)))
            if max_shares <= 0:
                return 0
            shares = max_shares
            gross_cost = exec_price * shares
            commission = gross_cost * (self.cost.commission_pct / 100)
            total_cost = gross_cost + commission

        # 비용 차감
        slippage_cost = (exec_price - price) * shares
        self.total_slippage_cost += slippage_cost
        self.total_commission_cost += commission
        self.cash -= total_cost

        # 포지션 업데이트
        if ticker in self.positions:
            pos = self.positions[ticker]
            old_total = pos['shares']
            new_total = old_total + shares
            pos['avg_price'] = (pos['avg_price'] * old_total + exec_price * shares) / new_total
            pos['shares'] = new_total
        else:
            self.positions[ticker] = {
                'shares': shares,
                'avg_price': exec_price,
                'name': name or ticker,
            }

        self.trades.append(TradeRecord(
            ticker=ticker, name=name or ticker,
            entry_date=date, entry_price=price,
            exec_price=round(exec_price, 1),
            shares=shares, entry_cost=round(commission + slippage_cost, 0),
        ))
        return shares

    def sell(self, ticker: str, price: float, shares: int, date: str) -> int:
        """
        매도
        - 슬리피지: 시장가보다 낮은 가격에 체결
        - 수수료: 체결 금액의 commission_pct%
        - 세금: 체결 금액의 tax_pct% (증권거래세)
        반환: 실제 매도 주수
        """
        if ticker not in self.positions:
            return 0

        pos = self.positions[ticker]
        actual = min(shares, pos['shares'])

        # 슬리피지 적용 (매도: 불리하게 낮은 가격)
        exec_price = price * (1 - self.cost.slippage_pct / 100)

        gross_proceeds = exec_price * actual
        commission = gross_proceeds * (self.cost.commission_pct / 100)
        tax = gross_proceeds * (self.cost.tax_pct / 100)
        net_proceeds = gross_proceeds - commission - tax

        slippage_cost = (price - exec_price) * actual
        self.total_slippage_cost += slippage_cost
        self.total_commission_cost += commission
        self.total_tax_cost += tax

        self.cash += net_proceeds

        # 순손익 (비용 차감 후)
        pnl = net_proceeds - pos['avg_price'] * actual
        pnl_pct = (net_proceeds / (pos['avg_price'] * actual) - 1) * 100 if pos['avg_price'] > 0 else 0

        # 트레이드 기록 업데이트
        for t in reversed(self.trades):
            if t.ticker == ticker and t.status == 'open':
                t.exit_date = date
                t.exit_price = price
                t.exec_exit_price = round(exec_price, 1)
                t.exit_cost = round(commission + tax + slippage_cost, 0)
                t.pnl = round(pnl, 0)
                t.pnl_pct = round(pnl_pct, 2)
                t.status = 'closed'
                break

        pos['shares'] -= actual
        if pos['shares'] <= 0:
            del self.positions[ticker]
        return actual

    def sell_all(self, prices: Dict[str, float], date: str):
        """전량 매도"""
        for ticker in list(self.positions.keys()):
            if ticker in prices:
                self.sell(ticker, prices[ticker],
                          self.positions[ticker]['shares'], date)

    def equity(self, prices: Dict[str, float]) -> float:
        """현재 총 자산 평가 (미실현 슬리피지/수수료 미반영 시가평가)"""
        total = self.cash
        for ticker, pos in self.positions.items():
            p = prices.get(ticker, pos['avg_price'])
            total += p * pos['shares']
        return total

    def snapshot(self, date: str, prices: Dict[str, float]):
        """일별 자산 스냅샷"""
        eq = self.equity(prices)
        self.equity_history.append({
            'date': date,
            'equity': eq,
            'cash': self.cash,
            'invested': eq - self.cash,
        })

    def get_cost_summary(self) -> dict:
        """누적 거래 비용 요약"""
        total = self.total_slippage_cost + self.total_commission_cost + self.total_tax_cost
        return {
            'slippage': round(self.total_slippage_cost),
            'commission': round(self.total_commission_cost),
            'tax': round(self.total_tax_cost),
            'total': round(total),
        }


# ============================================================
# 백테스트 엔진
# ============================================================
class BacktestEngine:
    """
    백테스트 엔진

    Args:
        initial_capital: 초기 자본금 (기본: 1억원)
        slippage_pct: 슬리피지 (%, 기본: 0)
        commission_pct: 거래 수수료 (%, 매수/매도 양쪽, 기본: 0)
        tax_pct: 증권거래세 (%, 매도 시만, 기본: 0)

    사용법:
        engine = BacktestEngine(
            initial_capital=100_000_000,
            slippage_pct=0.3,
            commission_pct=0.015,
            tax_pct=0.20,
        )
        engine.add_price_data('005930', data, name='삼성전자')
        engine.set_benchmark(kospi_data)
        engine.run_equal_weight(['005930', '000660'])
        results = engine.get_results()
    """

    def __init__(self, initial_capital: float = 100_000_000,
                 slippage_pct: float = 0.0,
                 commission_pct: float = 0.0,
                 tax_pct: float = 0.0):
        self.initial_capital = initial_capital
        self.cost_config = CostConfig(
            slippage_pct=slippage_pct,
            commission_pct=commission_pct,
            tax_pct=tax_pct,
        )
        self.portfolio = Portfolio(initial_capital, self.cost_config)
        self.price_data: Dict[str, List[dict]] = {}
        self.ticker_names: Dict[str, str] = {}
        self.all_dates: List[str] = []
        self.benchmark_data: List[dict] = []
        self._price_idx: Dict[str, Dict[str, dict]] = {}

    def add_price_data(self, ticker: str, data: List[dict], name: str = ''):
        """
        가격 데이터 추가

        Args:
            ticker: 종목코드 (예: '005930')
            data: [{'date': 'YYYY-MM-DD', 'open': float, 'high': float,
                     'low': float, 'close': float, 'volume': int}, ...]
            name: 종목명 (예: '삼성전자')
        """
        sorted_data = sorted(data, key=lambda x: x['date'])
        self.price_data[ticker] = sorted_data
        if name:
            self.ticker_names[ticker] = name
        self._price_idx[ticker] = {row['date']: row for row in sorted_data}

    def set_benchmark(self, data: List[dict]):
        """벤치마크 데이터 설정 [{'date': 'YYYY-MM-DD', 'close': float}, ...]"""
        self.benchmark_data = sorted(data, key=lambda x: x['date'])

    def _build_dates(self):
        dates = set()
        for ticker_data in self.price_data.values():
            for row in ticker_data:
                dates.add(row['date'])
        self.all_dates = sorted(dates)

    def _price(self, ticker: str, date: str, field: str = 'close') -> Optional[float]:
        idx = self._price_idx.get(ticker, {})
        row = idx.get(date)
        return row.get(field) if row else None

    def _prices_on_date(self, date: str) -> Dict[str, float]:
        result = {}
        for ticker in self.price_data:
            p = self._price(ticker, date)
            if p is not None:
                result[ticker] = p
        return result

    def _last_known_prices(self, date: str) -> Dict[str, float]:
        result = {}
        for ticker, data in self.price_data.items():
            last = None
            for row in data:
                if row['date'] <= date:
                    last = row['close']
                else:
                    break
            if last is not None:
                result[ticker] = last
        return result

    # ----------------------------------------------------------
    # 전략 1: 동일 비중 매수 후 보유
    # ----------------------------------------------------------
    def run_equal_weight(self, tickers: List[str],
                         start_date: str = None, end_date: str = None):
        """
        동일 비중 매수 후 보유 (Buy & Hold)

        - 첫 거래일에 전 종목을 동일 금액으로 매수
        - 슬리피지/수수료 반영하여 실제 매수 가능 수량 결정
        - 기간 종료까지 보유
        """
        self._build_dates()
        if not self.all_dates:
            return

        start = start_date or self.all_dates[0]
        end = end_date or self.all_dates[-1]
        dates = [d for d in self.all_dates if start <= d <= end]
        if not dates:
            return

        valid = [t for t in tickers if t in self.price_data and self.price_data[t]]
        if not valid:
            return

        alloc = self.initial_capital / len(valid)
        buy_date = dates[0]

        for ticker in valid:
            price = self._price(ticker, buy_date)
            if price and price > 0:
                # 수수료/슬리피지 고려해서 매수 가능 수량 계산
                exec_p = price * (1 + self.cost_config.slippage_pct / 100)
                shares = int(alloc / (exec_p * (1 + self.cost_config.commission_pct / 100)))
                name = self.ticker_names.get(ticker, ticker)
                self.portfolio.buy(ticker, price, shares, buy_date, name)

        for date in dates:
            prices = self._last_known_prices(date)
            self.portfolio.snapshot(date, prices)

    # ----------------------------------------------------------
    # 전략 2: 주기적 리밸런싱
    # ----------------------------------------------------------
    def run_rebalance(self, tickers: List[str],
                      start_date: str = None, end_date: str = None,
                      period: int = 20):
        """
        주기적 리밸런싱

        - period 거래일마다 전량 매도 후 동일 비중 재매수
        - 매도 시 슬리피지/수수료/세금, 매수 시 슬리피지/수수료 반영
        """
        self._build_dates()
        if not self.all_dates:
            return

        start = start_date or self.all_dates[0]
        end = end_date or self.all_dates[-1]
        dates = [d for d in self.all_dates if start <= d <= end]

        valid = [t for t in tickers if t in self.price_data and self.price_data[t]]
        if not valid or not dates:
            return

        last_rebal = -period

        for i, date in enumerate(dates):
            prices = self._last_known_prices(date)

            if i - last_rebal >= period:
                self.portfolio.sell_all(prices, date)
                eq = self.portfolio.equity(prices)
                alloc = eq / len(valid)
                for ticker in valid:
                    if ticker in prices and prices[ticker] > 0:
                        exec_p = prices[ticker] * (1 + self.cost_config.slippage_pct / 100)
                        shares = int(alloc / (exec_p * (1 + self.cost_config.commission_pct / 100)))
                        name = self.ticker_names.get(ticker, ticker)
                        self.portfolio.buy(ticker, prices[ticker], shares, date, name)
                last_rebal = i

            self.portfolio.snapshot(date, prices)

    # ----------------------------------------------------------
    # 전략 3: 사용자 정의 시그널
    # ----------------------------------------------------------
    def run_custom(self, signals: List[dict]):
        """
        사용자 정의 시그널 기반 실행

        Args:
            signals: [{'date': 'YYYY-MM-DD', 'ticker': '005930',
                        'action': 'buy'/'sell', 'weight': 0.1}, ...]
        """
        self._build_dates()
        if not self.all_dates:
            return

        signal_map: Dict[str, List[dict]] = {}
        for s in signals:
            signal_map.setdefault(s['date'], []).append(s)

        for date in self.all_dates:
            prices = self._last_known_prices(date)

            if date in signal_map:
                for sig in signal_map[date]:
                    ticker = sig['ticker']
                    action = sig['action']
                    weight = sig.get('weight', 1.0 / max(len(self.price_data), 1))

                    if action == 'buy' and ticker in prices:
                        eq = self.portfolio.equity(prices)
                        alloc = eq * weight
                        exec_p = prices[ticker] * (1 + self.cost_config.slippage_pct / 100)
                        shares = int(alloc / (exec_p * (1 + self.cost_config.commission_pct / 100)))
                        name = self.ticker_names.get(ticker, ticker)
                        self.portfolio.buy(ticker, prices[ticker], shares, date, name)
                    elif action == 'sell' and ticker in self.portfolio.positions:
                        pos = self.portfolio.positions[ticker]
                        self.portfolio.sell(ticker, prices[ticker], pos['shares'], date)

            self.portfolio.snapshot(date, prices)

    # ----------------------------------------------------------
    # 전략 4: 변동성 가중 + 트레일링 스탑
    # ----------------------------------------------------------
    def run_volatility_trailing_stop(self, tickers: List[str],
                                      start_date: str = None,
                                      end_date: str = None,
                                      lookback: int = 20,
                                      stop_pct: float = -10.0,
                                      cooldown: int = 5,
                                      reentry: bool = True):
        """
        변동성 가중 배분 + 트레일링 스탑

        MDD 줄이는 핵심:
        - 변동성이 낮은 종목에 더 많은 비중 → 포트폴리오 전체 변동성 감소
        - 트레일링 스탑: 고점 대비 stop_pct% 이상 하락하면 매도 → 큰 손실 차단
        - 쿨다운: 매도 후 cooldown 거래일 동안 재진입 금지 → 휩소 방지

        Args:
            tickers: 종목코드 리스트
            lookback: 변동성 계산 기간 (기본 20일)
            stop_pct: 트레일링 스탑 비율 (기본 -10%, 고점 대비)
            cooldown: 매도 후 재진입 금지 일수 (기본 5일)
            reentry: 스탑 후 재진입 허용 여부 (기본 True)
        """
        self._build_dates()
        if not self.all_dates:
            return

        start = start_date or self.all_dates[0]
        end = end_date or self.all_dates[-1]
        dates = [d for d in self.all_dates if start <= d <= end]
        valid = [t for t in tickers if t in self.price_data and self.price_data[t]]
        if not valid or not dates:
            return

        # 상태 추적
        peaks: Dict[str, float] = {}          # 보유 중 최고가
        sold_day: Dict[str, int] = {}         # 마지막 매도 일 인덱스
        holding: Dict[str, bool] = {}         # 현재 보유 여부

        for t in valid:
            holding[t] = False
            sold_day[t] = -cooldown - 1

        initial_buy_done = False

        for i, date in enumerate(dates):
            prices = self._last_known_prices(date)

            # ---- 트레일링 스탑 체크 (매도) ----
            for t in valid:
                if not holding[t]:
                    continue
                p = prices.get(t)
                if p is None:
                    continue
                # 피크 갱신
                if p > peaks.get(t, 0):
                    peaks[t] = p
                # 스탑 체크
                pk = peaks.get(t, p)
                if pk > 0:
                    dd_pct = (p / pk - 1) * 100
                    if dd_pct <= stop_pct:
                        if t in self.portfolio.positions:
                            self.portfolio.sell(t, p, self.portfolio.positions[t]['shares'], date)
                        holding[t] = False
                        sold_day[t] = i

            # ---- 변동성 가중 매수 ----
            # 첫 매수 또는 재진입
            buyable = []
            for t in valid:
                if holding[t]:
                    continue
                if not reentry and sold_day[t] >= 0 and initial_buy_done:
                    continue
                if i - sold_day[t] <= cooldown:
                    continue
                if t in prices and prices[t] > 0:
                    buyable.append(t)

            if buyable:
                # 변동성 계산 (최근 lookback일 수익률의 표준편차)
                inv_vols = {}
                for t in buyable:
                    closes = []
                    for row in self.price_data[t]:
                        if row['date'] <= date:
                            closes.append(row['close'])
                    closes = closes[-(lookback + 1):]
                    if len(closes) >= 2:
                        rets = [closes[j] / closes[j - 1] - 1 for j in range(1, len(closes))]
                        vol = statistics.stdev(rets) if len(rets) > 1 else 1.0
                        inv_vols[t] = 1.0 / max(vol, 1e-8)
                    else:
                        inv_vols[t] = 1.0

                # 역변동성 비중 (변동성 낮을수록 큰 비중)
                total_inv = sum(inv_vols.values())
                eq = self.portfolio.equity(prices)
                # 기존 보유 종목 가치 차감
                available = self.portfolio.cash

                for t in buyable:
                    weight = inv_vols[t] / total_inv if total_inv > 0 else 1 / len(buyable)
                    alloc = available * weight
                    if alloc <= 0:
                        continue
                    p = prices[t]
                    exec_p = p * (1 + self.cost_config.slippage_pct / 100)
                    shares = int(alloc / (exec_p * (1 + self.cost_config.commission_pct / 100)))
                    if shares > 0:
                        name = self.ticker_names.get(t, t)
                        bought = self.portfolio.buy(t, p, shares, date, name)
                        if bought > 0:
                            holding[t] = True
                            peaks[t] = p
                            initial_buy_done = True

            self.portfolio.snapshot(date, prices)

    # ----------------------------------------------------------
    # 전략 5: 이동평균 필터
    # ----------------------------------------------------------
    def run_ma_filter(self, tickers: List[str],
                      start_date: str = None, end_date: str = None,
                      ma_period: int = 20,
                      rebalance_period: int = 5):
        """
        이동평균 필터 전략

        MDD 줄이는 핵심:
        - 종가 > MA(n)일 때만 매수/보유, 아래로 내려가면 매도
        - 하락 추세 종목을 자동 회피 → 큰 드로다운 방지
        - rebalance_period마다 MA 조건 재평가

        Args:
            tickers: 종목코드 리스트
            ma_period: 이동평균 기간 (기본 20일)
            rebalance_period: 리밸런싱 주기 (기본 5 거래일)
        """
        self._build_dates()
        if not self.all_dates:
            return

        start = start_date or self.all_dates[0]
        end = end_date or self.all_dates[-1]
        dates = [d for d in self.all_dates if start <= d <= end]
        valid = [t for t in tickers if t in self.price_data and self.price_data[t]]
        if not valid or not dates:
            return

        last_check = -rebalance_period

        for i, date in enumerate(dates):
            prices = self._last_known_prices(date)

            if i - last_check >= rebalance_period:
                last_check = i

                # MA 기반 필터: 현재가 > MA → 매수 대상
                above_ma = []
                below_ma = []

                for t in valid:
                    closes = []
                    for row in self.price_data[t]:
                        if row['date'] <= date:
                            closes.append(row['close'])
                    if len(closes) >= ma_period:
                        ma_val = sum(closes[-ma_period:]) / ma_period
                        current = closes[-1]
                        if current > ma_val:
                            above_ma.append(t)
                        else:
                            below_ma.append(t)
                    elif closes:
                        # MA 기간 부족 → 일단 보유
                        above_ma.append(t)

                # MA 아래 종목 전량 매도
                for t in below_ma:
                    if t in self.portfolio.positions and t in prices:
                        self.portfolio.sell(t, prices[t],
                                            self.portfolio.positions[t]['shares'], date)

                # MA 위 종목에 동일 비중 배분
                if above_ma:
                    eq = self.portfolio.equity(prices)
                    target_alloc = eq / len(above_ma)

                    # 기존 보유 중인 MA 위 종목의 현재 가치
                    for t in above_ma:
                        current_value = 0
                        if t in self.portfolio.positions and t in prices:
                            current_value = self.portfolio.positions[t]['shares'] * prices[t]

                        diff = target_alloc - current_value
                        if t not in prices or prices[t] <= 0:
                            continue

                        if diff > prices[t] * 2:  # 충분한 차이가 있을 때만 추가 매수
                            exec_p = prices[t] * (1 + self.cost_config.slippage_pct / 100)
                            shares = int(diff / (exec_p * (1 + self.cost_config.commission_pct / 100)))
                            if shares > 0:
                                name = self.ticker_names.get(t, t)
                                self.portfolio.buy(t, prices[t], shares, date, name)
                        elif diff < -prices[t] * 2 and t in self.portfolio.positions:
                            # 비중 초과 → 일부 매도
                            sell_shares = int(abs(diff) / prices[t])
                            if sell_shares > 0:
                                self.portfolio.sell(t, prices[t], sell_shares, date)

            self.portfolio.snapshot(date, prices)

    # ----------------------------------------------------------
    # 전략 6: 복합 전략 (변동성 가중 + MA 필터 + 트레일링 스탑)
    # ----------------------------------------------------------
    def run_composite(self, tickers: List[str],
                      start_date: str = None, end_date: str = None,
                      ma_period: int = 20,
                      lookback: int = 20,
                      stop_pct: float = -8.0,
                      cooldown: int = 5,
                      rebalance_period: int = 10):
        """
        복합 리스크 관리 전략

        3중 방어:
        1. MA 필터: 하락 추세 종목 진입 금지
        2. 변동성 가중: 안정적 종목에 더 많은 비중
        3. 트레일링 스탑: 보유 중 급락 시 즉시 매도

        Args:
            tickers: 종목코드 리스트
            ma_period: 이동평균 기간
            lookback: 변동성 계산 기간
            stop_pct: 트레일링 스탑 비율 (기본 -8%)
            cooldown: 매도 후 재진입 금지 일수
            rebalance_period: 리밸런싱 주기
        """
        self._build_dates()
        if not self.all_dates:
            return

        start = start_date or self.all_dates[0]
        end = end_date or self.all_dates[-1]
        dates = [d for d in self.all_dates if start <= d <= end]
        valid = [t for t in tickers if t in self.price_data and self.price_data[t]]
        if not valid or not dates:
            return

        peaks: Dict[str, float] = {}
        sold_day: Dict[str, int] = {}
        holding: Dict[str, bool] = {}
        last_rebal = -rebalance_period

        for t in valid:
            holding[t] = False
            sold_day[t] = -cooldown - 1

        for i, date in enumerate(dates):
            prices = self._last_known_prices(date)

            # ---- 1단계: 트레일링 스탑 (매일 체크) ----
            for t in valid:
                if not holding[t]:
                    continue
                p = prices.get(t)
                if p is None:
                    continue
                if p > peaks.get(t, 0):
                    peaks[t] = p
                pk = peaks.get(t, p)
                if pk > 0:
                    dd_pct = (p / pk - 1) * 100
                    if dd_pct <= stop_pct:
                        if t in self.portfolio.positions:
                            self.portfolio.sell(t, p,
                                                self.portfolio.positions[t]['shares'], date)
                        holding[t] = False
                        sold_day[t] = i

            # ---- 2단계: 리밸런싱 주기에 MA 필터 + 변동성 가중 ----
            if i - last_rebal >= rebalance_period:
                last_rebal = i

                # MA 필터
                above_ma = []
                below_ma = []
                for t in valid:
                    closes = []
                    for row in self.price_data[t]:
                        if row['date'] <= date:
                            closes.append(row['close'])
                    if len(closes) >= ma_period:
                        ma_val = sum(closes[-ma_period:]) / ma_period
                        if closes[-1] > ma_val:
                            above_ma.append(t)
                        else:
                            below_ma.append(t)
                    elif closes:
                        above_ma.append(t)

                # MA 아래 종목 매도
                for t in below_ma:
                    if holding[t] and t in self.portfolio.positions and t in prices:
                        self.portfolio.sell(t, prices[t],
                                            self.portfolio.positions[t]['shares'], date)
                        holding[t] = False
                        sold_day[t] = i

                # MA 위 & 쿨다운 해제 종목에 변동성 가중 매수
                buyable = []
                for t in above_ma:
                    if holding[t]:
                        continue
                    if i - sold_day[t] <= cooldown:
                        continue
                    if t in prices and prices[t] > 0:
                        buyable.append(t)

                if buyable:
                    inv_vols = {}
                    for t in buyable:
                        closes = []
                        for row in self.price_data[t]:
                            if row['date'] <= date:
                                closes.append(row['close'])
                        closes = closes[-(lookback + 1):]
                        if len(closes) >= 2:
                            rets = [closes[j] / closes[j - 1] - 1
                                    for j in range(1, len(closes))]
                            vol = statistics.stdev(rets) if len(rets) > 1 else 1.0
                            inv_vols[t] = 1.0 / max(vol, 1e-8)
                        else:
                            inv_vols[t] = 1.0

                    total_inv = sum(inv_vols.values())
                    available = self.portfolio.cash

                    for t in buyable:
                        weight = inv_vols[t] / total_inv if total_inv > 0 else 1 / len(buyable)
                        alloc = available * weight
                        if alloc <= 0:
                            continue
                        p = prices[t]
                        exec_p = p * (1 + self.cost_config.slippage_pct / 100)
                        shares = int(alloc / (exec_p * (1 + self.cost_config.commission_pct / 100)))
                        if shares > 0:
                            name = self.ticker_names.get(t, t)
                            bought = self.portfolio.buy(t, p, shares, date, name)
                            if bought > 0:
                                holding[t] = True
                                peaks[t] = p

            self.portfolio.snapshot(date, prices)

    # ----------------------------------------------------------
    # 결과 산출
    # ----------------------------------------------------------
    def get_results(self) -> dict:
        """백테스트 결과 반환 (JSON 직렬화 가능)"""
        curve = self.portfolio.equity_history
        if not curve:
            return {'error': '데이터가 없습니다'}

        equities = [c['equity'] for c in curve]
        dates = [c['date'] for c in curve]

        metrics = self._calc_metrics(equities, dates)
        dd_curve = metrics.pop('_dd_curve')

        return {
            'equity_curve': [
                {'date': d, 'equity': round(e)}
                for d, e in zip(dates, equities)
            ],
            'drawdown_curve': dd_curve,
            'metrics': metrics,
            'cost_summary': self.portfolio.get_cost_summary(),
            'cost_config': {
                'slippage_pct': self.cost_config.slippage_pct,
                'commission_pct': self.cost_config.commission_pct,
                'tax_pct': self.cost_config.tax_pct,
            },
            'stock_performance': self._calc_stock_performance(),
            'benchmark': self._calc_benchmark(equities, dates),
            'trades': self._build_trade_details(),
            'trades_by_stock': self._group_trades_by_stock(),
        }

    def _build_trade_details(self) -> List[dict]:
        """
        매매 상세 이력 생성
        필드: 종목코드, 종목명, 매수일, 매수가, 매수수량, 매입금액,
              평균단가, 총매입금액, 평가금액, 평가손익,
              매도일, 매도가, 매도비용, 실현손익, 수익률(%), 상태
        """
        details = []
        # 종목별 누적 매입 추적 (평균단가, 총매입금액 계산용)
        stock_accum: Dict[str, dict] = {}

        for t in self.portfolio.trades:
            buy_amount = round(t.exec_price * t.shares)  # 매입금액 (체결가 × 수량)
            total_buy_amount = round(buy_amount + t.entry_cost)  # 총매입금액 (매입금액 + 매수비용)
            avg_price = round(t.exec_price)  # 이 거래의 평균단가 = 체결가

            # 종목별 누적 (여러 번 매수 시 평균단가 업데이트)
            ticker = t.ticker
            if ticker not in stock_accum:
                stock_accum[ticker] = {
                    'total_shares': 0,
                    'total_cost': 0,  # 체결가 기준 누적
                    'total_buy_with_cost': 0,  # 비용 포함 누적
                }
            acc = stock_accum[ticker]
            acc['total_shares'] += t.shares
            acc['total_cost'] += t.exec_price * t.shares
            acc['total_buy_with_cost'] += buy_amount + t.entry_cost

            # 누적 평균단가
            cumulative_avg = round(acc['total_cost'] / acc['total_shares']) if acc['total_shares'] > 0 else 0
            cumulative_total_buy = round(acc['total_buy_with_cost'])

            # 평가금액: 보유중이면 마지막 종가 기준, 청산이면 매도 체결가 기준
            if t.status == 'closed' and t.exec_exit_price:
                eval_amount = round(t.exec_exit_price * t.shares)
            else:
                # 보유중 → 마지막 종가로 평가
                last_price = 0
                if ticker in self.price_data and self.price_data[ticker]:
                    last_price = self.price_data[ticker][-1]['close']
                eval_amount = round(last_price * t.shares)

            # 평가손익 (비용 차감 전 시가 기준)
            eval_pnl = eval_amount - buy_amount

            # 매도 관련
            sell_date = t.exit_date
            sell_price = round(t.exit_price) if t.exit_price else None
            sell_cost = round(t.exit_cost) if t.exit_cost else 0

            # 실현손익 (비용 모두 차감)
            realized_pnl = round(t.pnl) if t.status == 'closed' else None

            # 수익률
            return_pct = t.pnl_pct if t.status == 'closed' else None
            if t.status == 'open' and buy_amount > 0:
                return_pct = round((eval_amount / buy_amount - 1) * 100, 2)

            # 매도 시 누적에서 차감
            if t.status == 'closed':
                acc['total_shares'] -= t.shares
                acc['total_cost'] -= t.exec_price * t.shares
                acc['total_buy_with_cost'] -= (buy_amount + t.entry_cost)
                if acc['total_shares'] <= 0:
                    stock_accum.pop(ticker, None)

            details.append({
                'ticker': ticker,
                'name': t.name,
                'entry_date': t.entry_date,
                'entry_price': round(t.entry_price),
                'shares': t.shares,
                'buy_amount': buy_amount,
                'avg_price': cumulative_avg,
                'total_buy_amount': cumulative_total_buy,
                'eval_amount': eval_amount,
                'eval_pnl': eval_pnl,
                'exit_date': sell_date,
                'exit_price': sell_price,
                'exit_cost': sell_cost,
                'realized_pnl': realized_pnl,
                'return_pct': return_pct,
                'status': t.status,
            })
        return details

    def _group_trades_by_stock(self) -> Dict[str, list]:
        """종목별 매매 이력 그룹핑"""
        details = self._build_trade_details()
        grouped: Dict[str, list] = {}
        for d in details:
            grouped.setdefault(d['ticker'], []).append(d)
        return grouped

    def _calc_metrics(self, equities: List[float], dates: List[str]) -> dict:
        """핵심 성과 지표 계산"""
        n = len(equities)
        if n == 0 or equities[0] == 0:
            return {'_dd_curve': []}

        total_ret = (equities[-1] / equities[0] - 1) * 100

        # MDD
        peak = equities[0]
        mdd = 0.0
        dd_curve = []
        mdd_peak_d = mdd_trough_d = tmp_peak_d = dates[0]

        for i, eq in enumerate(equities):
            if eq > peak:
                peak = eq
                tmp_peak_d = dates[i]
            dd = (eq / peak - 1) * 100 if peak > 0 else 0
            dd_curve.append(round(dd, 2))
            if dd < mdd:
                mdd = dd
                mdd_peak_d = tmp_peak_d
                mdd_trough_d = dates[i]

        # 일별 수익률
        daily_rets = []
        for i in range(1, n):
            if equities[i - 1] > 0:
                daily_rets.append(equities[i] / equities[i - 1] - 1)

        ann_ret = ((equities[-1] / equities[0]) ** (252 / max(n, 1)) - 1) * 100

        vol = (statistics.stdev(daily_rets) * math.sqrt(252) * 100
               if len(daily_rets) > 1 else 0)

        if daily_rets and len(daily_rets) > 1:
            avg_d = statistics.mean(daily_rets)
            std_d = statistics.stdev(daily_rets)
            sharpe = ((avg_d - 0.035 / 252) / std_d * math.sqrt(252)
                      if std_d > 0 else 0)
        else:
            sharpe = 0

        closed = [t for t in self.portfolio.trades if t.status == 'closed']
        wins = sum(1 for t in closed if t.pnl > 0)
        win_rate = wins / len(closed) * 100 if closed else 0

        # 현재가치 계산 (마지막 자산)
        current_value = round(equities[-1])
        profit_loss = round(equities[-1] - self.initial_capital)

        return {
            'initial_capital': self.initial_capital,
            'final_equity': current_value,
            'current_value': current_value,
            'profit_loss': profit_loss,
            'total_return': round(total_ret, 2),
            'annual_return': round(ann_ret, 2),
            'mdd': round(mdd, 2),
            'mdd_period': f"{mdd_peak_d} ~ {mdd_trough_d}",
            'sharpe': round(sharpe, 2),
            'volatility': round(vol, 2),
            'win_rate': round(win_rate, 2),
            'total_trades': len(self.portfolio.trades),
            'start_date': dates[0],
            'end_date': dates[-1],
            'trading_days': n,
            '_dd_curve': [
                {'date': d, 'dd': dd}
                for d, dd in zip(dates, dd_curve)
            ],
        }

    def get_daily_detail(self) -> List[dict]:
        """
        일자별 종목별 상세 데이터 (CSV 저장용)

        반환: [
            {
                'date': '2025-01-02',
                'ticker': '005930',
                'name': '삼성전자',
                'open': 70000, 'high': 72000, 'low': 69000,
                'close': 71000, 'volume': 100000,
                'action': 'BUY' / 'SELL' / 'HOLD' / '',
                'shares_traded': 100,
                'exec_price': 70210,
                'trade_cost': 1234,
                'holding_shares': 100,
                'holding_value': 7100000,
                'portfolio_equity': 100000000,
                'portfolio_cash': 30000000,
            }, ...
        ]
        """
        if not self.all_dates:
            self._build_dates()

        # 거래를 날짜+종목으로 인덱싱
        buy_map: Dict[str, Dict[str, list]] = {}   # date -> ticker -> [trades]
        sell_map: Dict[str, Dict[str, list]] = {}
        for t in self.portfolio.trades:
            buy_map.setdefault(t.entry_date, {}).setdefault(t.ticker, []).append(t)
            if t.exit_date:
                sell_map.setdefault(t.exit_date, {}).setdefault(t.ticker, []).append(t)

        # equity_history를 날짜 맵으로
        eq_map = {e['date']: e for e in self.portfolio.equity_history}

        # 날짜별 보유현황 추적 (시뮬레이션 재현)
        holdings: Dict[str, int] = {}  # ticker -> shares
        rows = []

        for date in self.all_dates:
            eq_snap = eq_map.get(date, {})
            portfolio_equity = eq_snap.get('equity', 0)
            portfolio_cash = eq_snap.get('cash', 0)

            # 이 날짜의 매수 처리
            day_buys = buy_map.get(date, {})
            for ticker, trades in day_buys.items():
                for tr in trades:
                    holdings[ticker] = holdings.get(ticker, 0) + tr.shares

            # 이 날짜의 매도 처리
            day_sells = sell_map.get(date, {})
            for ticker, trades in day_sells.items():
                for tr in trades:
                    holdings[ticker] = holdings.get(ticker, 0) - tr.shares
                    if holdings[ticker] <= 0:
                        holdings.pop(ticker, None)

            # 각 종목별 행 생성
            for ticker in self.price_data:
                row_data = self._price_idx.get(ticker, {}).get(date)
                if not row_data:
                    continue

                name = self.ticker_names.get(ticker, ticker)
                close_p = row_data.get('close', 0)
                h_shares = holdings.get(ticker, 0)

                # 이 날짜의 매수/매도 이벤트
                action = ''
                shares_traded = 0
                exec_price = 0
                trade_cost = 0

                if ticker in day_buys:
                    for tr in day_buys[ticker]:
                        action = 'BUY'
                        shares_traded += tr.shares
                        exec_price = tr.exec_price
                        trade_cost += tr.entry_cost

                if ticker in day_sells:
                    for tr in day_sells[ticker]:
                        action = 'SELL' if not action else 'BUY+SELL'
                        shares_traded += tr.shares
                        exec_price = tr.exec_exit_price or 0
                        trade_cost += tr.exit_cost

                if not action and h_shares > 0:
                    action = 'HOLD'

                # 포지션 없고 매매도 없는 종목은 제외
                # (SELL 이후 다음 BUY 전까지 리스트에 안 나옴)
                if not action:
                    continue

                rows.append({
                    'date': date,
                    'ticker': ticker,
                    'name': name,
                    'open': row_data.get('open', 0),
                    'high': row_data.get('high', 0),
                    'low': row_data.get('low', 0),
                    'close': close_p,
                    'volume': row_data.get('volume', 0),
                    'action': action,
                    'shares_traded': shares_traded,
                    'exec_price': round(exec_price),
                    'trade_cost': round(trade_cost),
                    'holding_shares': h_shares,
                    'holding_value': round(h_shares * close_p),
                    'portfolio_equity': round(portfolio_equity),
                    'portfolio_cash': round(portfolio_cash),
                })

        return rows

    def _calc_stock_performance(self) -> List[dict]:
        """종목별 성과 계산"""
        perf = []
        for ticker, data in self.price_data.items():
            if len(data) < 2:
                continue
            first_close = data[0]['close']
            last_close = data[-1]['close']
            ret = (last_close / first_close - 1) * 100 if first_close > 0 else 0

            pk = first_close
            smdd = 0
            for row in data:
                if row['close'] > pk:
                    pk = row['close']
                dd = (row['close'] / pk - 1) * 100 if pk > 0 else 0
                if dd < smdd:
                    smdd = dd

            perf.append({
                'ticker': ticker,
                'name': self.ticker_names.get(ticker, ticker),
                'return_pct': round(ret, 2),
                'mdd': round(smdd, 2),
                'start_price': round(first_close),
                'end_price': round(last_close),
            })

        perf.sort(key=lambda x: -x['return_pct'])
        return perf

    def _calc_benchmark(self, equities: List[float],
                        dates: List[str]) -> Optional[dict]:
        """벤치마크 대비 성과 계산"""
        if not self.benchmark_data or not dates:
            return None

        bd = [b for b in self.benchmark_data
              if dates[0] <= b['date'] <= dates[-1]]
        if not bd:
            return None

        base = bd[0]['close']
        if base <= 0:
            return None

        bench_ret = (bd[-1]['close'] / base - 1) * 100

        start_eq = equities[0]
        curve = [
            {'date': b['date'], 'equity': round(start_eq * b['close'] / base)}
            for b in bd
        ]

        pk = bd[0]['close']
        bmdd = 0
        for b in bd:
            if b['close'] > pk:
                pk = b['close']
            dd = (b['close'] / pk - 1) * 100
            if dd < bmdd:
                bmdd = dd

        return {
            'return_pct': round(bench_ret, 2),
            'mdd': round(bmdd, 2),
            'curve': curve,
        }
