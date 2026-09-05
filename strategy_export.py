"""Shared adaptive result sections for web and daily-report CSV exports."""
import json


def write_adaptive_csv(writer, results):
    if not results.get('allocation_history'):
        return
    writer.writerow([])
    writer.writerow(['ETF 자산배분 (실험)', '보유기간과세 제외 세전 성과'])
    for key in ('config', 'cost_config', 'strategy_parameters', 'data_quality', 'validation',
                'max_drawdown_limit', 'max_drawdown_overshoot'):
        writer.writerow([key, json.dumps(results.get(key), ensure_ascii=False)])
    writer.writerow(['날짜', '레짐', '목표비중', '실제비중', '현금비중', '낙폭제어상태'])
    regimes = {r['date']: r['regime'] for r in results.get('regime_history', [])}
    for row in results['allocation_history']:
        writer.writerow([row['date'], regimes.get(row['date'], ''),
                         json.dumps(row['target_weights']), json.dumps(row['actual_weights']),
                         row['cash_weight'], row['guard_state']])
    writer.writerow(['날짜', '이전상태', '상태', '낙폭', '조치'])
    for row in results.get('drawdown_guard_events', []):
        writer.writerow([row[k] for k in ('date', 'previous_state', 'state', 'drawdown', 'action')])
