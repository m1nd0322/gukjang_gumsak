from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class AssetSpec:
    key: str
    ticker: str
    name: str
    asset_class: str
    role: str
    max_weight: float


@dataclass(frozen=True)
class StrategySpec:
    key: str
    label: str
    description: str
    kind: str
    rebalance: str


ETF_ASSETS: Mapping[str, AssetSpec] = MappingProxyType({
    "kr_equity": AssetSpec("kr_equity", "069500", "KODEX 200", "equity", "risk", 0.40),
    "us_equity": AssetSpec("us_equity", "143850", "TIGER 미국S&P500선물(H)", "equity", "risk", 0.40),
    "us_tech": AssetSpec("us_tech", "133690", "TIGER 미국나스닥100", "equity", "risk", 0.30),
    "kr_bond_10y": AssetSpec("kr_bond_10y", "148070", "KIWOOM 국고채10년", "bond", "defensive", 0.40),
    "short_bond": AssetSpec("short_bond", "153130", "KODEX 단기채권", "cash_like", "defensive", 0.50),
    "gold": AssetSpec("gold", "132030", "KODEX 골드선물(H)", "commodity", "defensive", 0.30),
    "usd": AssetSpec("usd", "261240", "KODEX 미국달러선물", "currency", "defensive", 0.25),
    "oil": AssetSpec("oil", "130680", "TIGER 원유선물Enhanced(H)", "commodity", "real_asset", 0.10),
})

_LEGACY_STRATEGIES = (
    StrategySpec(
        "equal_weight",
        "동일 비중 Buy & Hold",
        "선택 종목을 동일 비중으로 매수 후 보유합니다.",
        "legacy",
        "none",
    ),
    StrategySpec(
        "rebalance",
        "월간 리밸런싱 (20일)",
        "20거래일마다 목표 동일 비중으로 리밸런싱합니다.",
        "legacy",
        "20d",
    ),
    StrategySpec(
        "vol_trailing_stop",
        "변동성 가중 + 트레일링 스탑",
        "변동성 가중 배분에 트레일링 스탑을 결합합니다.",
        "legacy",
        "signal",
    ),
    StrategySpec(
        "vol_trailing_stop_loss",
        "변동성 가중 + 트레일링 스탑 + 스탑로스",
        "변동성 가중, 트레일링 스탑, 사용자 스탑로스를 함께 적용합니다.",
        "legacy",
        "signal",
    ),
    StrategySpec(
        "ma_filter",
        "이동평균 필터 (MA20)",
        "20일 이동평균 위에 있는 종목만 보유합니다.",
        "legacy",
        "5d",
    ),
    StrategySpec(
        "composite",
        "복합 전략 (MA + 변동성 + 스탑)",
        "이동평균, 변동성 가중, 트레일링 스탑을 결합합니다.",
        "legacy",
        "10d",
    ),
)

_ETF_STRATEGIES = (
    StrategySpec(
        "defensive_dual_momentum",
        "방어형 듀얼 모멘텀",
        "상대 모멘텀과 절대 모멘텀으로 위험자산과 방어자산을 전환합니다.",
        "etf",
        "monthly",
    ),
    StrategySpec(
        "multi_asset_trend_rotation",
        "멀티에셋 추세 로테이션",
        "ETF 유니버스의 추세 강도에 따라 자산군을 순환합니다.",
        "etf",
        "monthly",
    ),
    StrategySpec(
        "trend_risk_parity",
        "추세 위험균형",
        "추세 필터를 통과한 자산을 위험 균형 관점으로 배분합니다.",
        "etf",
        "monthly",
    ),
    StrategySpec(
        "price_regime_ensemble",
        "가격 레짐 앙상블",
        "가격 레짐 신호를 조합해 위험 노출을 조절합니다.",
        "etf",
        "monthly",
    ),
)

STRATEGIES: Mapping[str, StrategySpec] = MappingProxyType({
    spec.key: spec
    for spec in (*_LEGACY_STRATEGIES, *_ETF_STRATEGIES)
})

ETF_STRATEGY_KEYS = frozenset(spec.key for spec in _ETF_STRATEGIES)


def get_strategy(key: str) -> StrategySpec:
    return STRATEGIES[key]


def is_etf_strategy(key: str) -> bool:
    return key in ETF_STRATEGY_KEYS


def strategy_groups() -> tuple[tuple[str, tuple[StrategySpec, ...]], ...]:
    return (
        ("기존 전략", _LEGACY_STRATEGIES),
        ("레짐·자산배분 전략", _ETF_STRATEGIES),
    )
