# Price reconciliation and paired backtest status

## Result

Partial diagnostic completed. Adjusted-price certification and the survivor/PIT
paired backtest remain blocked. No comparable performance result was produced.

## Price diagnostic

- Compared 256 local official/vendor series; 211 have OHLC differences.
- Of those 211, 149 have SEIBRO income-distribution records and 62 do not.
- Detected 682 adjacent-valid-observation close-ratio changes exceeding 0.2%.
- 674 changes fall within seven calendar days of an income record date; eight do not.
- 45,955 ticker-days among differing tickers do not admit one common OHLC scaling
  factor with an assumed one-won tolerance on each vendor field.
- Zero tickers were promoted to adjustment_verified.

These are numerical diagnostics, not evidence of the vendor's adjustment formula.
Record dates are not ex-distribution dates. The one-won tolerance and 0.2% threshold
are diagnostic assumptions, not source-certified rules. Invalid/nonpositive OHLC
observations are excluded from factor analysis, not silently repaired. Changes may
span gaps between valid observations. Corporate actions other than distributions
are not comprehensively accounted for. Absence of an event in this extract does
not establish that no event occurred.

Artifacts: krx_211_price_diagnostic_20260912.json and its companion .py script.
The script uses the current absolute workspace path and local input files; price
input SHA256 values are included per ticker. SEIBRO acquisition provenance remains
in seibro_acquisition_20260912.json.

## Actual backtest attempt

Executed evaluate_adaptive_strategies.py with --cached, --end 2026-09-11,
--universe-mode both, --selection-policy oldest_listing_v1, and
data/krx/etf_listing_registry_complete.csv.

Exit code: 1. Report status: blocked. The adjusted-price DB lookup rejected missing
ticker series before either comparison arm produced performance. Raw CSV acquisition
does not populate or certify this adjusted-price cache.

Artifacts: adaptive_same_conditions_attempt_20260912.json and .log.

## Additional comparison blockers

The existing evaluator independently derives each arm's observation calendar and
selects its risk budget. Running --universe-mode both alone therefore does not
guarantee identical comparison dates or frozen parameters, even after prices exist.
The complete listing registry is inventory-only, not a dated strategy allocation
mapping. The default legacy universe is explicitly survivor_only.

A defensible paired run still requires dated strategy eligibility, verified price
adjustments for both active and delisted instruments, verified trading-end and
liquidation handling, and a shared explicit evaluation calendar and frozen parameter
set. No inferred classifications, fabricated evidence flags, or arbitrary price
rescaling were used to bypass these gates. Missing liquidation records for 097740,
105450, 110550 and 124090, plus the 427110 amount conflict, remain unresolved from
the preceding source-coverage work.
