"""Deterministic research-evidence checks; these do not authenticate sources.

The v1 input contract is documented in references/research-review.md.  This
module deliberately has no report-text heuristics or third-party dependencies.
"""

from datetime import date
import math
from statistics import NormalDist

if __package__:
    from .dcf import montecarlo_spec, scenario_fcfs
else:
    from dcf import montecarlo_spec, scenario_fcfs


def number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def same(a, b):
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return number(a) and number(b) and math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)


def numeric_value(value):
    return number(value) or (isinstance(value, list) and bool(value) and all(number(v) for v in value))


def same_record(actual, recorded):
    """Compare complete snapshots, including their field set and distribution names."""
    if isinstance(actual, dict) and isinstance(recorded, dict):
        return actual.keys() == recorded.keys() and all(same_record(v, recorded[k]) for k, v in actual.items())
    if number(actual):
        return same(actual, recorded)
    return type(actual) is type(recorded) and actual == recorded


def montecarlo_summary(spec):
    """Exact marginal moments and tails for the calculator's fixed distributions.

    These are input-distribution statistics, not a forecast of realized returns.
    Normal draws are clipped at three sigma, not redrawn from a truncated normal.
    """
    low, mode, high = (spec[k] for k in ('margin_low', 'margin_mode', 'margin_high'))

    def triangular_quantile(p):
        if low == high:
            return low
        split = (mode - low) / (high - low)
        if p <= split:
            return low + math.sqrt(p * (high - low) * (mode - low))
        return high - math.sqrt((1 - p) * (high - low) * (high - mode))

    gm, gs = spec['growth_mean'], spec['growth_std']
    wl, wh = spec['wacc_low'], spec['wacc_high']
    result = {
        'growth_mean': gm, 'growth_low': gm - 3 * gs, 'growth_high': gm + 3 * gs,
        'growth_p10': gm + NormalDist().inv_cdf(.1) * gs,
        'growth_p90': gm + NormalDist().inv_cdf(.9) * gs,
        'margin_mean': low / 3 + mode / 3 + high / 3,
        'margin_low': low, 'margin_high': high,
        'margin_p10': triangular_quantile(.1), 'margin_p90': triangular_quantile(.9),
        'wacc_mean': wl / 2 + wh / 2, 'wacc_low': wl, 'wacc_high': wh,
        'wacc_p10': wl + .1 * (wh - wl), 'wacc_p90': wl + .9 * (wh - wl),
    }
    if not all(number(v) for v in result.values()):
        raise ValueError('distribution statistics must be finite')
    return result


def uplift(assumed, conservative):
    if isinstance(assumed, list) and isinstance(conservative, list):
        return len(assumed) == len(conservative) and any(a > b + 1e-6 for a, b in zip(assumed, conservative))
    return number(assumed) and number(conservative) and assumed > conservative + 1e-6


def resolve_pointer(cfg, pointer):
    """Resolve a JSON Pointer, including escaped object keys and array indices."""
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('parameter must be a JSON Pointer')
    value = cfg
    try:
        for raw in pointer[1:].split('/'):
            key = raw.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                if not key.isdecimal():
                    raise ValueError('array index must be nonnegative')
                value = value[int(key)]
            else:
                value = value[key]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError('unknown model parameter') from exc
    return value


def model_pointer(parameter):
    parts = parameter.split('/')
    return '/'.join(parts[:3]) if len(parts) > 2 and parts[1] == 'scenarios' else '/'.join(parts[:2])


def earnings_basis(value):
    value = str(value).upper()
    return 'NET_INCOME' if value in ('NI', 'NET_INCOME', 'NETINCOME') else value


def review_issues(cfg, require_review=False):
    """Return issue dictionaries compatible with check_research_output.add()."""
    issues = []

    def issue(code, message, detail='', severity='P1'):
        issues.append(dict(severity=severity, code=code, message=message, detail=detail))

    review = cfg.get('research_review')
    if review is None:
        issue('RESEARCH_REVIEW_NOT_PERFORMED', '未完成 research_review 证据复核；计算可复算不代表研究依据已通过。',
              severity='P1' if require_review else 'P2')
        return issues
    if not isinstance(review, dict) or type(review.get('version')) is not int or review.get('version') != 1:
        issue('RESEARCH_REVIEW_VERSION', 'research_review 必须为 version=1 的对象。')
        return issues

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def iso_date(value):
        try:
            return date.fromisoformat(value) if isinstance(value, str) else None
        except ValueError:
            return None

    sources = {}
    records = review.get('sources', [])
    if not isinstance(records, list):
        records = []
    for source in records:
        if not isinstance(source, dict):
            issue('REVIEW_SOURCE_INVALID', '来源必须为对象。')
            continue
        sid = source.get('id')
        if not text(sid) or not text(source.get('locator')) or not iso_date(source.get('date')):
            issue('REVIEW_SOURCE_INVALID', '来源需要非空 id、定位信息 locator 和 ISO 日期 date。')
        elif sid in sources:
            issue('REVIEW_SOURCE_DUPLICATE', '来源 id 重复。', sid)
        else:
            sources[sid] = source

    def cited(record, context):
        refs = record.get('source_ids') if isinstance(record, dict) else None
        if not isinstance(refs, list) or not refs or any(not isinstance(s, str) or s not in sources for s in refs):
            issue('REVIEW_SOURCE_UNLINKED', '证据未关联到有效来源记录。', context)
            return False
        return True

    def check_benchmark(benchmark, context, requires_growth=False):
        """Apply the same provenance/cohort bar to scalar and distribution claims."""
        if not isinstance(benchmark, dict):
            issue('REVIEW_BENCHMARK_MISSING', '需披露保守参照及其适用限制。', str(context))
            benchmark = {}
        cited(benchmark, f'{context}: benchmark')
        if not text(benchmark.get('description')) or not text(benchmark.get('limitations')):
            issue('REVIEW_BENCHMARK_MISSING', '参照组需要描述及匹配限制。', str(context))
        if requires_growth:
            for field in ('industry', 'scale', 'business_model', 'stage', 'order_visibility'):
                if not text(benchmark.get(field)):
                    issue('REVIEW_COHORT_INCOMPLETE', '增长参照需说明行业、规模、模式、阶段及订单可见度。', f'{context}: {field}')
        percentile = benchmark.get('percentile')
        if percentile is not None:
            distribution = benchmark.get('distribution')
            if not number(percentile) or not 0 <= percentile <= 100 or not isinstance(distribution, dict):
                issue('REVIEW_UNSUPPORTED_PERCENTILE', '具体分位必须有可追溯分布；无分布请用 null。', str(context))
            else:
                size = distribution.get('sample_size')
                if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or any(
                    not text(distribution.get(k)) for k in ('dataset', 'period', 'metric')):
                    issue('REVIEW_UNSUPPORTED_PERCENTILE', '分位缺少数据集、样本量、样本期间或指标口径。', str(context))
                cited(distribution, f'{context}: distribution')

    baseline = review.get('baseline')
    if not isinstance(baseline, dict):
        issue('REVIEW_BASELINE_MISSING', '缺少实际经营基线 baseline。')
        baseline = {}
    for key in ('earnings_basis', 'currency', 'unit', 'annual_period'):
        if not text(baseline.get(key)):
            issue('REVIEW_BASELINE_FIELD', '经营基线缺少口径或期间。', key)
    if earnings_basis(baseline.get('earnings_basis')) not in ('NOPAT', 'NET_INCOME'):
        issue('REVIEW_BASELINE_FIELD', '经营基线盈利口径必须为 NOPAT 或 NET_INCOME。')
    annual = baseline.get('annual_earnings')
    if not number(annual):
        issue('REVIEW_BASELINE_FIELD', 'annual_earnings 必须是可比年度或 TTM 实际盈利。')
    cited(baseline, 'baseline')

    threshold = review.get('decline_threshold', 0.20)
    if not number(threshold) or not 0 < threshold <= 1:
        issue('REVIEW_DECLINE_THRESHOLD', 'decline_threshold 必须在 (0,1]。')
        threshold = 0.20
    elif not same(threshold, 0.20) and not text(review.get('threshold_rationale')):
        issue('REVIEW_THRESHOLD_RATIONALE', '调整 20% 筛查阈值必须说明行业依据。')

    periods = baseline.get('periods', [])
    if not isinstance(periods, list) or len(periods) != 2:
        issue('REVIEW_COMPARABLE_PERIODS', '需提供最近两个实际报告期及各自上年可比期间；不能使用预测或环比替代。')
        periods = []
    declines, period_dates = [], []
    recovery_triggered = False
    unknown_trend = False
    for row in periods:
        if not isinstance(row, dict):
            issue('REVIEW_COMPARABLE_PERIODS', '报告期记录必须为对象。')
            unknown_trend = True
            continue
        cited(row, str(row.get('period', 'period')))
        dates = [iso_date(row.get(k)) for k in ('start', 'end', 'prior_start', 'prior_end')]
        comparable = row.get('comparable') is True
        if row.get('actual') is not True or not text(row.get('period')) or not all(dates):
            issue('REVIEW_COMPARABLE_PERIODS', '报告期必须声明 actual=true，并提供实际与上年同期起止日期。')
            unknown_trend = True
            continue
        start, end, prior_start, prior_end = dates
        if start > end or prior_start > prior_end:
            issue('REVIEW_COMPARABLE_PERIODS', '报告期起止日期倒置。')
            unknown_trend = True
            continue
        period_dates.append((start, end))
        if not (330 <= (end - prior_end).days <= 400) or abs((end - start).days - (prior_end - prior_start).days) > 7:
            issue('REVIEW_COMPARABLE_PERIODS', '比较期应为同长度的上年同期，不能用季节性环比。')
            unknown_trend = True
            continue
        cur, prev = row.get('profit'), row.get('prior_profit')
        if not number(cur) or not number(prev):
            issue('REVIEW_COMPARABLE_PERIODS', '利润实际值及上年可比值必须是有限数字。')
            unknown_trend = True
            continue
        if not comparable:
            unknown_trend = True
            if not text(row.get('comparability_note')):
                issue('REVIEW_COMPARABILITY_NOTE', '不可比口径必须说明原因，不能当作未发生下滑。')
            continue
        if prev > 0:
            declines.append((prev - cur) / prev >= threshold - 1e-9)
            if cur <= 0:
                recovery_triggered = True
        else:
            # Percentage changes through zero or negative bases are not meaningful.
            unknown_trend = True
            if cur < prev:
                recovery_triggered = True
            if not text(row.get('comparability_note')):
                issue('REVIEW_NONPOSITIVE_BASE', '零或负利润基数需单独解释，不计算普通同比百分比。')
    if len(period_dates) == 2:
        period_dates.sort()
        if period_dates[0][1] >= period_dates[1][0]:
            issue('REVIEW_OVERLAPPING_PERIODS', '最近两个报告期不能使用重叠的累计期间或滚动 TTM。')
            unknown_trend = True
    recovery_triggered |= len(declines) == 2 and all(declines)
    signals = review.get('structural_signals', [])
    if not isinstance(signals, list):
        issue('REVIEW_STRUCTURAL_SIGNALS', 'structural_signals 必须为数组。')
        signals = []
    for signal in signals:
        if not isinstance(signal, dict) or not text(signal.get('description')):
            issue('REVIEW_STRUCTURAL_SIGNALS', '结构性恶化信号需要描述及来源。')
        else:
            cited(signal, 'structural_signals')
    recovery_triggered |= bool(signals)
    if recovery_triggered:
        issue('EARNINGS_RECOVERY_REVIEW_TRIGGERED', '利润下滑、转亏或结构性恶化触发盈利恢复审查。', severity='P3')
    if unknown_trend:
        issue('REVIEW_TREND_UNCERTAIN', '历史趋势存在不可比或非正基数，需人工审查；不等于未发生衰退。', severity='P2')

    decision = review.get('decision', {})
    if not isinstance(decision, dict):
        decision = {}
    if decision.get('action') not in ('none', 'buy', 'accumulate', 'hold', 'wait', 'reduce', 'avoid'):
        issue('REVIEW_DECISION_MISSING', 'decision.action 必须明确；纯估值分析可用 none。')
    models = decision.get('model_paths', [])
    if not isinstance(models, list) or any(not isinstance(p, str) for p in models):
        issue('REVIEW_DECISION_MODELS', 'decision.model_paths 需列明实际支撑估值或动作的模型。')
        models = []
    if not models and decision.get('action') not in ('none', 'wait'):
        issue('REVIEW_DECISION_MODELS', '仅条件测算可在 action=none/wait 时使用空决策模型列表。')
    if not models and ('range_low' in cfg or 'range_high' in cfg):
        issue('REVIEW_CONDITIONAL_DECISION_RANGE', '没有决策估值模型时不能填写综合决策区间；条件价值请保留在各模型结果中。')
    valid_models = set()
    for pointer in models:
        try:
            block = resolve_pointer(cfg, pointer)
        except ValueError:
            block = None
        if not isinstance(block, dict) or pointer not in ('/epv', '/eva', '/montecarlo') and not (
            pointer.startswith('/scenarios/') and len(pointer.split('/')) == 3):
            issue('REVIEW_DECISION_MODELS', '决策模型引用无效；反向 DCF/PVGO 不能作为独立估值支持。', str(pointer))
        elif block.get('role', 'decision') != 'decision':
            issue('REVIEW_CONDITIONAL_IN_DECISION', '条件情景不能进入决策估值。', pointer)
        else:
            valid_models.add(pointer)
    scenarios = cfg.get('scenarios', [])
    for method in ('epv', 'eva', 'montecarlo'):
        block = cfg.get(method)
        if isinstance(block, dict) and block.get('role', 'decision') == 'decision' and f'/{method}' not in valid_models:
            issue('REVIEW_DECISION_MODEL_OMITTED', '决策用途估值模块不能从证据复核列表中遗漏；纯条件测算须显式设置 role=conditional。', f'/{method}')
    if isinstance(scenarios, list):
        for index, sc in enumerate(scenarios):
            if isinstance(sc, dict) and sc.get('role', 'decision') == 'decision' and sc.get('prob', 0) != 0:
                pointer = f'/scenarios/{index}'
                if pointer not in valid_models:
                    issue('REVIEW_WEIGHTED_MODEL_OMITTED', '带决策概率的情景不能从证据复核中遗漏。', pointer)

    # Cash-flow recovery must start at actual FCFF, not at the first forecast.
    cash_baseline = baseline.get('cash_flow')
    cash_models = any(p.startswith('/scenarios/') or p == '/montecarlo' for p in valid_models)
    cash_valid = isinstance(cash_baseline, dict)
    cash_fields = ('revenue', 'nopat', 'da', 'capex', 'change_nwc', 'fcff')
    if cash_models:
        if not cash_valid or cash_baseline.get('actual') is not True or not all(
                number(cash_baseline.get(k)) for k in cash_fields):
            issue('REVIEW_CASH_BASELINE_MISSING', 'DCF/蒙特卡洛决策模型需提供实际收入与可复算的 FCFF 基线。')
            cash_valid = False
        else:
            cited(cash_baseline, 'baseline.cash_flow')
            if cash_baseline['revenue'] <= 0 or cash_baseline['da'] < 0 or cash_baseline['capex'] < 0:
                issue('REVIEW_CASH_BASELINE_INVALID', 'FCFF 基线需正收入、非负折旧摊销与资本开支；不适用时另建人工复核模型。')
                cash_valid = False
            expected = cash_baseline['nopat'] + cash_baseline['da'] - cash_baseline['capex'] - cash_baseline['change_nwc']
            if not same(expected, cash_baseline['fcff']) or (
                    earnings_basis(baseline.get('earnings_basis')) == 'NOPAT' and not same(cash_baseline['nopat'], annual)):
                issue('REVIEW_CASH_BASELINE_MISMATCH', '实际 FCFF 必须等于 NOPAT＋折旧摊销－全部资本开支－营运资本增加，并与 NOPAT 盈利基线勾稽。')
                cash_valid = False
            if cash_valid and not all(number(cash_baseline[k] / cash_baseline['revenue']) for k in ('fcff', 'nopat')):
                issue('REVIEW_CASH_BASELINE_INVALID', '实际 FCFF/NOPAT 利润率必须可计算且有限。')
                cash_valid = False

    def improving(path, actual):
        return isinstance(path, list) and bool(path) and all(number(v) for v in path) and (
            any(v > actual + 1e-6 for v in path) or any(b > a + 1e-6 for a, b in zip(path, path[1:])))

    required_cash_claims, actual_uplifts, cash_claim_kinds = set(), set(), {}
    for pointer in valid_models:
        if not pointer.startswith('/scenarios/'):
            continue
        block = resolve_pointer(cfg, pointer)
        if number(block.get('fade_g_start')) and block['fade_g_start'] > 0 and block.get('fade_years', 0):
            actual_uplifts.add(f'{pointer}/fade_g_start')
        if 'fcf' in block:
            bridge = block.get('operating_bridge')
            fields = ('revenue', 'nopat_margin', 'da', 'capex', 'change_nwc')
            fcfs = block.get('fcf')
            valid = isinstance(bridge, dict) and isinstance(fcfs, list) and bool(fcfs) and all(number(v) for v in fcfs)
            valid = valid and all(isinstance(bridge.get(k), list) and len(bridge[k]) == len(fcfs) and
                                  all(number(v) for v in bridge[k]) for k in fields)
            if not valid:
                issue('REVIEW_FCFF_BRIDGE_MISSING', '直接输入 FCF 的决策情景需提供等长收入、NOPAT 利润率、折旧摊销、资本开支、营运资本增加数组。', pointer)
                continue
            cited(bridge, f'{pointer}/operating_bridge')
            revenues = bridge['revenue']
            if any(v <= 0 for v in revenues) or any(v < 0 for k in ('da', 'capex') for v in bridge[k]):
                issue('REVIEW_FCFF_BRIDGE_INVALID', '经营桥需正收入、非负折旧摊销和资本开支。', pointer)
                continue
            bridged = [r * m + d - c - w for r, m, d, c, w in zip(*(bridge[k] for k in fields))]
            if not same(bridged, fcfs):
                issue('REVIEW_FCFF_BRIDGE_MISMATCH', '经营桥 NOPAT＋折旧摊销－全部资本开支－营运资本增加必须逐期等于模型 FCF。', pointer)
            revenue_parameter = f'{pointer}/operating_bridge/revenue'
            margin_parameter = f'{pointer}/operating_bridge/nopat_margin'
            required_cash_claims.update((revenue_parameter, margin_parameter))
            cash_claim_kinds.update({revenue_parameter: 'growth_persistence', margin_parameter: 'earnings_recovery'})
            if cash_valid:
                if improving(revenues, cash_baseline['revenue']):
                    actual_uplifts.add(revenue_parameter)
                if improving(bridge['nopat_margin'], cash_baseline['nopat'] / cash_baseline['revenue']):
                    actual_uplifts.add(margin_parameter)
                fcff_margins = [f / r for f, r in zip(fcfs, revenues)]
                if not all(number(v) for v in fcff_margins):
                    issue('REVIEW_FCFF_BRIDGE_INVALID', '桥接隐含 FCFF 利润率必须为有限数字。', pointer)
                if improving(fcfs, cash_baseline['fcff']) or improving(fcff_margins, cash_baseline['fcff'] / cash_baseline['revenue']):
                    actual_uplifts.add(f'{pointer}/fcf')
        else:
            revenue_parameter, margin_parameter = f'{pointer}/revenue', f'{pointer}/fcf_margin'
            required_cash_claims.add(margin_parameter)
            cash_claim_kinds.update({revenue_parameter: 'growth_persistence', margin_parameter: 'earnings_recovery'})
            if cash_valid:
                if improving(block.get('revenue'), cash_baseline['revenue']):
                    actual_uplifts.add(revenue_parameter)
                if improving(block.get('fcf_margin'), cash_baseline['fcff'] / cash_baseline['revenue']):
                    actual_uplifts.add(margin_parameter)

    if any(p.startswith('/scenarios/') for p in valid_models) and number(cfg.get('terminal_g')) and cfg['terminal_g'] > 0:
        actual_uplifts.add('/terminal_g')

    def supported_evidence(record, context):
        evidence = record.get('evidence', {})
        if not isinstance(evidence, dict):
            evidence = {}
        for category in ('business', 'cash_flow', 'competition'):
            observations = evidence.get(category)
            if not isinstance(observations, list) or not observations:
                issue('REVIEW_EVIDENCE_MISSING', '支持有利假设需提供业务、现金流及竞争证据，强度标签不能替代。', f'{context}: {category}')
                continue
            for observation in observations:
                if not isinstance(observation, dict) or not text(observation.get('observation')):
                    issue('REVIEW_EVIDENCE_MISSING', '证据必须记录具体观测。', f'{context}: {category}')
                else:
                    cited(observation, f'{context}: {category}')

    claims = review.get('claims', [])
    if not isinstance(claims, list):
        issue('REVIEW_CLAIMS_INVALID', 'claims 必须为数组。')
        claims = []
    covered, ids = set(), set()
    for claim in claims:
        if not isinstance(claim, dict):
            issue('REVIEW_CLAIM_INVALID', '主张必须为对象。')
            continue
        cid, parameter = claim.get('id'), claim.get('parameter')
        if not text(cid) or cid in ids:
            issue('REVIEW_CLAIM_ID', '主张 id 必须非空且唯一。')
        else:
            ids.add(cid)
        kind = claim.get('kind')
        if kind not in ('growth_persistence', 'earnings_recovery'):
            issue('REVIEW_CLAIM_KIND', '主张类型必须为 growth_persistence 或 earnings_recovery。', str(cid))
        try:
            actual = resolve_pointer(cfg, parameter)
        except ValueError:
            issue('REVIEW_PARAMETER_UNLINKED', '主张未关联到实际模型参数。', str(parameter))
            continue
        assumed, conservative = claim.get('assumed_value'), claim.get('conservative_value')
        if not numeric_value(actual) or not same(actual, assumed) or not numeric_value(conservative) or (
            isinstance(assumed, list) != isinstance(conservative, list)) or (
            isinstance(assumed, list) and len(assumed) != len(conservative)):
            issue('REVIEW_PARAMETER_MISMATCH', '主张数值必须匹配模型，保守比较值须使用相同形状与口径。', str(parameter))
            continue
        if parameter in covered:
            issue('REVIEW_PARAMETER_DUPLICATE', '同一参数不能提供相互覆盖的复核记录。', parameter)
        covered.add(parameter)
        owner = model_pointer(parameter)
        try:
            block = resolve_pointer(cfg, owner)
        except ValueError:
            block = {}
        role = block.get('role', 'decision') if isinstance(block, dict) else 'decision'
        if claim.get('use') != role or role not in ('decision', 'conditional'):
            issue('REVIEW_USE_MISMATCH', '主张使用位置与实际模型 role 不一致。', str(parameter))
        for key in ('rationale', 'counterevidence', 'falsifier'):
            if not text(claim.get(key)):
                issue('REVIEW_CLAIM_INCOMPLETE', '主张需要理由、反证和可证伪条件。', f'{cid}: {key}')
        if not iso_date(claim.get('review_date')):
            issue('REVIEW_CLAIM_INCOMPLETE', '主张需要 ISO 格式的下次复核日期。', str(cid))
        check_benchmark(claim.get('benchmark'), cid, requires_growth=kind == 'growth_persistence')
        status = claim.get('status')
        if status not in ('supported', 'unsupported', 'unknown'):
            issue('REVIEW_SUPPORT_STATUS', '主张状态必须为 supported、unsupported 或 unknown。', str(cid))
        objective_recovery = parameter in actual_uplifts or (
            parameter in ('/epv/normalized_earnings', '/eva/nopat') and number(annual) and number(assumed) and assumed > annual + 1e-6)
        if parameter in cash_claim_kinds and kind != cash_claim_kinds[parameter]:
            issue('REVIEW_CLAIM_KIND', '收入需按增长主张、利润率需按恢复主张复核。', parameter)
        if parameter in ('/epv/normalized_earnings', '/eva/nopat'):
            if kind != 'earnings_recovery':
                issue('REVIEW_CLAIM_KIND', '盈利水平必须按 earnings_recovery 复核。', parameter)
            if number(annual) and number(conservative) and conservative > annual + 1e-6:
                issue('REVIEW_RECOVERY_BASELINE_INFLATED', '不得把高于当前实际盈利的恢复值重新命名为保守基线。', parameter)
        if status == 'supported':
            supported_evidence(claim, cid)
        elif (objective_recovery or uplift(assumed, conservative)) and (role == 'decision' or owner in valid_models):
            issue('REVIEW_UNSUPPORTED_DECISION_UPLIFT', '未获支持的有利假设不能进入决策基准或概率加权估值。', str(parameter))

    def require_claim(parameter):
        if parameter not in covered:
            issue('REVIEW_KEY_ASSUMPTION_MISSING', '决策使用的关键增长或盈利参数缺少证据复核。', parameter)

    for parameter in required_cash_claims:
        require_claim(parameter)

    for pointer in valid_models:
        block = resolve_pointer(cfg, pointer)
        if pointer.startswith('/scenarios/'):
            driver = 'fcf' if 'fcf' in block else 'revenue'
            require_claim(f'{pointer}/{driver}')
            margins = block.get('fcf_margin')
            if isinstance(margins, list) and len(margins) > 1 and all(number(v) for v in margins) and any(
                b > a for a, b in zip(margins, margins[1:])):
                require_claim(f'{pointer}/fcf_margin')
            if number(block.get('fade_g_start')) and block['fade_g_start'] > 0 and block.get('fade_years', 0):
                require_claim(f'{pointer}/fade_g_start')
        elif pointer == '/epv':
            require_claim('/epv/normalized_earnings')
            growth = block.get('growth')
            if isinstance(growth, dict) and number(growth.get('g')) and growth['g'] > 0:
                require_claim('/epv/growth/g')
                if number(growth.get('roiic')):
                    require_claim('/epv/growth/roiic')
        elif pointer == '/eva':
            if earnings_basis(baseline.get('earnings_basis')) != 'NOPAT':
                issue('REVIEW_BASELINE_BASIS_MISMATCH', '企业 EVA 必须使用 NOPAT 实际盈利基线，不能与净利润直接比较。')
            if number(block.get('nopat')) and number(annual) and block['nopat'] > annual:
                require_claim('/eva/nopat')
            if number(block.get('nopat_growth')) and block['nopat_growth'] > 0:
                require_claim('/eva/nopat_growth')
        elif pointer == '/montecarlo':
            record = review.get('montecarlo_distribution')
            if not isinstance(record, dict):
                issue('REVIEW_MC_DISTRIBUTION_MISSING', '蒙特卡洛决策模型需整体复核有效分布规格、期望和尾部；均值/众数单独记录不足。')
                continue
            try:
                actual_spec = montecarlo_spec(block, cfg)
                actual_summary = montecarlo_summary(actual_spec)
                conservative_record = record.get('conservative_spec')
                if not isinstance(conservative_record, dict):
                    raise ValueError('conservative_spec must be an effective specification')
                conservative_spec = montecarlo_spec(conservative_record, conservative_record)
                conservative_summary = montecarlo_summary(conservative_spec)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                issue('REVIEW_MC_DISTRIBUTION_INVALID', '分布复核规格无效或其统计量不可计算。', str(exc))
                continue
            for key, actual in (('assumed_spec', actual_spec), ('conservative_spec', conservative_spec),
                                ('assumed_summary', actual_summary), ('conservative_summary', conservative_summary)):
                if not same_record(actual, record.get(key)):
                    issue('REVIEW_MC_DISTRIBUTION_MISMATCH', '分布复核需匹配完整有效规格及程序计算的期望/尾部，含继承默认值。', key)
            if cash_valid and not same(actual_spec['base_revenue'], cash_baseline['revenue']):
                issue('REVIEW_MC_BASELINE_MISMATCH', '蒙特卡洛 base_revenue 必须等于实际 FCFF 基线收入；预测收入不能冒充基期。')
            for key in ('rationale', 'counterevidence', 'falsifier', 'expectation_rationale', 'tail_rationale'):
                if not text(record.get(key)):
                    issue('REVIEW_MC_DISTRIBUTION_INCOMPLETE', '分布复核需说明预期、尾部概率/幅度依据、反证及失效条件。', key)
            if not iso_date(record.get('review_date')) or record.get('use') != 'decision':
                issue('REVIEW_MC_DISTRIBUTION_INCOMPLETE', '分布复核需 ISO 复核日期和 use=decision。')
            check_benchmark(record.get('benchmark'), 'montecarlo_distribution',
                            requires_growth=actual_summary['growth_high'] > 0 or actual_spec['terminal_g'] > 0)
            status = record.get('status')
            if status not in ('supported', 'unsupported', 'unknown'):
                issue('REVIEW_SUPPORT_STATUS', '分布复核状态必须为 supported、unsupported 或 unknown。')
            favorable = any(actual_summary[k] > conservative_summary[k] + 1e-6 for k in actual_summary if not k.startswith('wacc_'))
            favorable |= any(actual_summary[k] < conservative_summary[k] - 1e-6 for k in actual_summary if k.startswith('wacc_'))
            favorable |= any(actual_spec[k] > conservative_spec[k] + 1e-6 for k in ('base_revenue', 'terminal_g'))
            favorable |= any(actual_spec[k] < conservative_spec[k] - 1e-6 for k in ('annual_dilution', 'shares', 'net_debt'))
            favorable |= any(actual_spec[k] != conservative_spec[k] for k in ('years', 'fade_years'))
            # Even a renamed conservative distribution cannot erase recovery
            # relative to actual FCFF, or positive growth in the upper tail.
            favorable |= actual_summary['growth_high'] > 0 or actual_spec['terminal_g'] > 0
            if cash_valid:
                favorable |= actual_summary['margin_high'] > cash_baseline['fcff'] / cash_baseline['revenue'] + 1e-6
            if status == 'supported':
                supported_evidence(record, 'montecarlo_distribution')
            elif favorable:
                issue('REVIEW_UNSUPPORTED_DECISION_UPLIFT', '未支持的分布上行（含相对实际的恢复、期望或尾部改善）不能进入决策估值。', '/montecarlo')
    uses_top_g = any(p.startswith('/scenarios/') for p in valid_models)
    if uses_top_g:
        if number(cfg.get('terminal_g')) and cfg['terminal_g'] > 0:
            require_claim('/terminal_g')

    epv = cfg.get('epv')
    if isinstance(epv, dict) and epv:
        bridge = review.get('earnings_bridge')
        if not isinstance(bridge, dict):
            issue('EPV_EARNINGS_BRIDGE_MISSING', '所有 EPV 都必须提供常态化盈利桥接。')
        else:
            cited(bridge, 'earnings_bridge')
            basis = earnings_basis(epv.get('earnings_basis', 'NOPAT'))
            if earnings_basis(bridge.get('earnings_basis')) != basis or earnings_basis(baseline.get('earnings_basis')) != basis:
                issue('EPV_BRIDGE_BASIS_MISMATCH', '基线、桥接和 EPV 盈利口径必须一致。')
            if not same(bridge.get('current_earnings'), annual):
                issue('EPV_BRIDGE_BASELINE_MISMATCH', '桥接起点必须与当前可持续年度盈利一致。')
            current_revenue, current_margin = bridge.get('current_revenue'), bridge.get('current_margin')
            if not number(current_revenue) or not number(current_margin) or not same(current_revenue * current_margin, annual):
                issue('EPV_BRIDGE_BASELINE_MISMATCH', '当前收入×同口径利润率必须与基线盈利勾稽；据此区分收入和利润率贡献。')
            revenue, margin = bridge.get('target_revenue'), bridge.get('target_margin')
            adjustments = bridge.get('adjustments', [])
            valid = number(revenue) and number(margin) and isinstance(adjustments, list)
            total = revenue * margin if valid else 0
            for adjustment in adjustments if isinstance(adjustments, list) else []:
                if not isinstance(adjustment, dict) or not number(adjustment.get('amount')) or not text(adjustment.get('description')):
                    issue('EPV_BRIDGE_ADJUSTMENT', '盈利调整需要金额、说明和来源。')
                    valid = False
                    continue
                cited(adjustment, 'earnings_bridge.adjustments')
                if adjustment.get('kind') not in ('one_off', 'recurring'):
                    issue('EPV_BRIDGE_ADJUSTMENT', '盈利桥接仅接受明确的经常性/一次性调整；不能直接加回增长性资本开支。')
                    valid = False
                total += adjustment['amount']
            if not valid or not same(total, epv.get('normalized_earnings')):
                issue('EPV_EARNINGS_BRIDGE_MISMATCH', '目标收入×同口径利润率＋调整必须等于 normalized_earnings。')
            if recovery_triggered or unknown_trend or (number(annual) and number(epv.get('normalized_earnings')) and epv['normalized_earnings'] > annual):
                for key in ('maintenance_capex', 'cash_conversion'):
                    item = bridge.get(key)
                    if not isinstance(item, dict) or not number(item.get('value')) or not text(item.get('explanation')):
                        issue('EPV_RECOVERY_BRIDGE_INCOMPLETE', '恢复审查需要维护性资本开支和现金转化的数值及解释，单独桥接至现金口径。', key)
                    else:
                        cited(item, f'earnings_bridge.{key}')

    if recovery_triggered or unknown_trend:
        downside = review.get('downside')
        if not isinstance(downside, dict) or not text(downside.get('description')):
            issue('REVIEW_DOWNSIDE_MISSING', '恢复或不可比趋势审查必须展示未恢复/继续下滑的条件路径。')
        else:
            try:
                block = resolve_pointer(cfg, downside.get('model_path'))
            except ValueError:
                block = None
            if not isinstance(block, dict) or not downside.get('model_path', '').startswith('/scenarios/'):
                issue('REVIEW_DOWNSIDE_MISSING', '下行情景必须关联可复算的 scenarios 模型。')
            else:
                try:
                    path, _ = scenario_fcfs(block, cfg.get('terminal_g', 0.0))
                except (ValueError, OverflowError):
                    path = None
                if not isinstance(path, list) or len(path) < 2 or not all(number(x) for x in path) or path[-1] >= path[0]:
                    issue('REVIEW_DOWNSIDE_NOT_DECLINING', '未恢复路径需在完整显式期及衰减期保持期末现金流低于起点，不能只给情景改名。')
    return issues
