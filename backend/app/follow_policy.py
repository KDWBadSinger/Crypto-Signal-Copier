"""Pure execution policy. No quote fetching, credentials or exchange writes."""
from decimal import Decimal, ROUND_DOWN, ROUND_UP


MAX_ENTRY_DEVIATION = Decimal('0.10')
PROTECTION_WAIT_SECONDS = 300


def exchange_leverage(maximum, minimum=1, override=None, percent=50):
    maximum, minimum = positive(maximum), positive(minimum)
    percent = Decimal(str(percent))
    if not percent.is_finite() or not 0 <= percent <= 100:
        raise ValueError('默认杠杆比例必须为 0%–100%')
    if override is None and percent == 0:
        raise ValueError('默认杠杆比例为 0%，未配置币种暂停新开仓')
    desired = (maximum * percent / 100).to_integral_value(rounding=ROUND_DOWN) if override is None else positive(override)
    if desired != desired.to_integral_value() or desired < minimum or maximum < minimum:
        raise ValueError('交易所杠杆范围或指定杠杆无效，默认比例计算结果不满足最小杠杆时拒单')
    return int(min(desired,maximum))


def positive(value):
    value = Decimal(str(value))
    if not value.is_finite() or value <= 0:
        raise ValueError('价格、数量及保证金必须是有限正数')
    return value


def correct_entry(reference, market):
    """Only decimal shifts, never arbitrary digit repairs. Caller ensures freshness."""
    reference, market = positive(reference), positive(market)
    candidates = [(reference * Decimal(10) ** shift, shift) for shift in range(-3, 4)]
    matches = [(price, shift) for price, shift in candidates
               if abs(market / price - 1) <= MAX_ENTRY_DEVIATION]
    if len(matches) != 1:
        raise ValueError('没有唯一的十进制纠错候选与实时价相差不超过 10%，拒绝开单')
    corrected, shift = matches[0]
    return {'original': str(reference), 'corrected': str(corrected),
            'decimal_shift': shift, 'market': str(market),
            'deviation': str(abs(market / corrected - 1))}


def temporary_stop(entry, quantity, initial_margin, side, fee_rate, tick):
    """Budget includes estimated round-trip fees; never promises a loss cap.

    Actual fills should replace the preliminary entry estimate after execution.
    Round toward entry so tick rounding cannot enlarge the intended loss budget.
    """
    entry, quantity, margin, tick = map(positive, (entry, quantity, initial_margin, tick))
    fee = Decimal(str(fee_rate))
    if not fee.is_finite() or not 0 <= fee < 1 or side not in {'long', 'short'}:
        raise ValueError('临时止损方向或手续费无效')
    per_unit = margin / quantity
    if side == 'long':
        stop = (entry * (1 + fee) - per_unit) / (1 - fee)
        stop = (stop / tick).to_integral_value(rounding=ROUND_UP) * tick
        valid = 0 < stop < entry
    else:
        stop = (entry * (1 - fee) + per_unit) / (1 + fee)
        stop = (stop / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        valid = stop > entry
    if not valid:
        raise ValueError('当前杠杆、手续费或价格精度无法设置有效临时止损')
    return stop


def validate_tp_percentages(values):
    if not isinstance(values, (list, tuple)) or len(values) != 3 or any(type(v) is not int or not 0 <= v <= 100 for v in values) or sum(values) != 100:
        raise ValueError('三档止盈比例必须是 0–100 的整数，合计为 100%')
    return list(values)


def target_sizes(quantity, count, step, percentages=None):
    """Allocate original fill; zero-weight legs are disabled, dust goes to last active leg."""
    quantity, step = positive(quantity), positive(step)
    if count < 1 or quantity % step:
        raise ValueError('止盈档数或成交数量步长无效')
    allocation = validate_tp_percentages(percentages if percentages is not None else [40,40,20])
    weights = [Decimal(v)/100 for v in allocation] if count == 3 else [Decimal(1) / count] * count
    sizes = [(quantity * weight / step).to_integral_value(rounding=ROUND_DOWN) * step for weight in weights]
    last = max(i for i,w in enumerate(weights) if w > 0)
    sizes[last] += quantity - sum(sizes)
    if any(size <= 0 and weights[i] > 0 for i,size in enumerate(sizes)):
        raise ValueError('成交数量不足以分配全部止盈档位')
    return sizes
