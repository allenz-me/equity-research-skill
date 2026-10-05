#!/usr/bin/env python3
"""equity-research skill · 估值计算器 v2（禁止心算的替代物）

用法：
    python dcf.py --config assumptions.json        # 完整运行
    python dcf.py --demo                           # 内置示例自检

模块：三阶段情景 DCF、概率加权、WACC×g 敏感性、反向 DCF、EPV/三要素、
EVA/剩余收益、PVGO 分解、蒙特卡洛、仓位思维（EV/不对称比/Kelly-lite）、
预注册标定标签。所有输入校验用显式异常（python -O 下依然生效）。

config JSON 顶层字段：price, shares, net_debt, wacc, terminal_g,
scenarios[], sensitivity{}, reverse{}, epv{}, eva{}, pvgo{}, montecarlo{},
range_low, range_high
"""
import argparse, json, math, random, sys

LABELS = ["显著低估", "低估", "合理", "高估", "显著高估"]

def die(msg):
    raise ValueError(f"[配置错误] {msg}")

def need(d, key, ctx):
    if not isinstance(d, dict):
        die(f"{ctx} 必须是对象")
    if key not in d:
        die(f"{ctx} 缺少必需字段 `{key}`")
    return d[key]

def object_input(value, ctx):
    if not isinstance(value, dict): die(f"{ctx} 必须是对象")
    return value

def number(value, ctx, *, positive=False, minimum=None):
    """JSON numbers only; reject NaN/Infinity and bools before doing arithmetic."""
    try:
        valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        die(f"{ctx} 必须是有限数字")
    if positive and value <= 0:
        die(f"{ctx} 必须为正")
    if minimum is not None and value < minimum:
        die(f"{ctx} 不得小于 {minimum}")
    return value

def year_count(value, ctx, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        die(f"{ctx} 必须是 ≥{minimum} 的整数")
    return value

def numeric_path(value, ctx):
    if not isinstance(value, list) or not value:
        die(f"{ctx} 必须是非空数组")
    return [number(v, f"{ctx}[{i}]") for i, v in enumerate(value)]

def growth_rate(value, ctx):
    value = number(value, ctx)
    if value <= -1:
        die(f"{ctx} 必须大于 -100%")
    return value

def power(base, exponent, ctx):
    try:
        return number(base ** exponent, ctx)
    except OverflowError:
        die(f"{ctx} 计算溢出，请检查年数和增长率")

def token(value, ctx):
    if not isinstance(value, str):
        die(f"{ctx} 必须是字符串")
    return value.strip().upper().replace(" ", "_").replace("-", "_")

def rate_basis(c, expected, ctx):
    if "discount_rate_basis" in c:
        actual = token(c["discount_rate_basis"], f"{ctx}.discount_rate_basis")
        if actual == "COST_OF_EQUITY":
            actual = "COE"
        if actual != expected:
            die(f"{ctx}: 折现率口径必须是 {expected}，收到 {actual}")

def firm_basis(c, ctx):
    """This calculator does not implement an FCFE or equity-residual-income engine."""
    object_input(c, ctx)
    if "valuation_basis" in c and token(c["valuation_basis"], ctx) not in ("FIRM", "ENTERPRISE"):
        die(f"{ctx}: 仅支持企业口径，股权估值请使用可审计的补充脚本")
    if "cashflow_basis" in c and token(c["cashflow_basis"], ctx) != "FCFF":
        die(f"{ctx}: 仅支持 FCFF，不支持 FCFE")
    if "earnings_basis" in c and token(c["earnings_basis"], ctx) != "NOPAT":
        die(f"{ctx}: 仅支持 NOPAT 企业口径")
    rate_basis(c, "WACC", ctx)

def earnings_basis(value):
    basis = token(value, "earnings_basis")
    if basis == "NOPAT":
        return basis
    if basis in ("NET_INCOME", "NI"):
        return "NET_INCOME"
    die(f"earnings_basis={value}: 仅支持 NOPAT 或 NET_INCOME")

def equity_basis(c, ctx):
    if "valuation_basis" in c and token(c["valuation_basis"], ctx) != "EQUITY":
        die(f"{ctx}: 仅支持股权口径")
    if "cashflow_basis" in c and token(c["cashflow_basis"], ctx) != "FCFE":
        die(f"{ctx}: 股权现金流不能标为 FCFF")
    rate_basis(c, "COE", ctx)

def debt_bridge(net_debt, excess_cash, ctx):
    """net_debt already deducts the non-operating cash used in the equity bridge."""
    nd = number(net_debt, f"{ctx}.net_debt")
    if number(excess_cash, f"{ctx}.excess_cash") != 0:
        die(f"{ctx}: net_debt 已扣除非经营/过剩现金；请将现金并入 net_debt，excess_cash 必须为 0")
    return nd

EQUITY_FLOOR_NOTE = "普通股有限责任归零仅为简化，不估计破产成本、清算/重整回收或股权期权价值；困境估值需独立模型"

def common_equity(raw_equity, ctx):
    """Keep the model's residual claim for diagnosis; common shareholders cannot owe the shortfall."""
    raw_equity = number(raw_equity, f'{ctx}.raw_equity')
    return {'raw_equity': raw_equity, 'equity': max(0.0, raw_equity),
            'equity_shortfall': max(0.0, -raw_equity)}

def print_equity_shortfall(result, label):
    if result['equity_shortfall'] > 0:
        print(f"  {label}：原始权益 {result['raw_equity']:,.2f} | 权益缺口 {result['equity_shortfall']:,.2f}；{EQUITY_FLOOR_NOTE}")

def model_role(c, ctx):
    role = c.get('role', 'decision')
    if role not in ('decision', 'conditional'):
        die(f'{ctx}.role 必须是 decision 或 conditional')
    label = 'decision 决策测算' if role == 'decision' else 'conditional 条件测算（不作为买入基准）'
    return role, label

# ---------- 核心计算 ----------

def scenario_fcfs(sc, terminal_g):
    """显式期+衰减期完整 FCF 序列与末年营收。"""
    object_input(sc, 'scenario')
    name = sc.get("name", "?")
    firm_basis(sc, f"情景 {name}")
    terminal_g = growth_rate(terminal_g, "terminal_g")
    if "fcf" in sc:
        fcfs = numeric_path(sc["fcf"], f"情景 {name}.fcf"); rev = None
    else:
        rev_path = numeric_path(need(sc, "revenue", f"情景 {name}"), f"情景 {name}.revenue")
        m = numeric_path(need(sc, "fcf_margin", f"情景 {name}"), f"情景 {name}.fcf_margin")
        if len(rev_path) != len(m):
            die(f"情景 {name}: revenue({len(rev_path)}) 与 fcf_margin({len(m)}) 长度不等")
        if any(r < 0 for r in rev_path):
            die(f"情景 {name}: revenue 不得为负")
        fcfs = [number(r * mm, f"情景 {name}.FCF") for r, mm in zip(rev_path, m)]
        rev = rev_path[-1]
    fade = year_count(sc.get("fade_years", 0), f"情景 {name}.fade_years")
    g0 = growth_rate(sc.get("fade_g_start", terminal_g), f"情景 {name}.fade_g_start")
    mm_last = m[-1] if rev is not None else None
    for k in range(fade):
        gr = g0 + (terminal_g - g0) * (k + 1) / fade
        if rev is not None:
            rev = number(rev * (1 + gr), f"情景 {name}.fade_revenue")
            fcfs.append(number(rev * mm_last, f"情景 {name}.fade_fcf"))
        else:
            fcfs.append(number(fcfs[-1] * (1 + gr), f"情景 {name}.fade_fcf"))
    return fcfs, rev

def dcf_value(sc, wacc, g, shares, net_debt):
    object_input(sc, 'scenario')
    wacc = number(wacc, "WACC", positive=True)
    g = growth_rate(g, "terminal_g")
    shares = number(shares, "shares", positive=True)
    net_debt = debt_bridge(net_debt, sc.get("excess_cash", 0.0), "dcf")
    if wacc <= g:
        die(f"WACC({wacc}) 必须大于永续增长 g({g})")
    fcfs, _ = scenario_fcfs(sc, g)
    n = len(fcfs)
    pv = sum(f / power(1 + wacc, i + 1, "DCF 折现因子") for i, f in enumerate(fcfs))
    tv = fcfs[-1] * (1 + g) / (wacc - g)
    terminal_pv = tv / power(1 + wacc, n, "DCF 终值折现因子")
    ev = number(pv + terminal_pv, "DCF EV")
    dilution = growth_rate(sc.get("annual_dilution", 0.0), "annual_dilution")
    # Compatibility convention: all equity PV uses end-of-forecast shares. No further terminal dilution.
    sh = number(shares * power(1 + dilution, n, "稀释因子"), "期末股本", positive=True)
    tv_share = terminal_pv / ev if ev else float("nan")
    bridge = common_equity(ev - net_debt, 'DCF')
    return {"ev": ev, **bridge, "per_share": number(bridge['equity'] / sh, 'DCF per_share'),
            "raw_per_share": number(bridge['raw_equity'] / sh, 'DCF raw_per_share'),
            "shares_end": sh, "terminal_fcf": fcfs[-1],
            "exit_pfcf": tv / fcfs[-1] if fcfs[-1] else float("nan"),
            "tv_share": tv_share, "forecast_years": n}

def reverse_dcf(price, shares, net_debt, wacc, g, interim_fcf, steady_margins, base_revenue):
    price = number(price, "reverse.price", positive=True)
    shares = number(shares, "reverse.shares", positive=True)
    net_debt = number(net_debt, "reverse.net_debt")
    wacc = number(wacc, "reverse.wacc", positive=True)
    g = growth_rate(g, "reverse.terminal_g")
    interim_fcf = numeric_path(interim_fcf, "reverse.interim_fcf")
    steady_margins = numeric_path(steady_margins, "reverse.steady_margins")
    if base_revenue is not None:
        base_revenue = number(base_revenue, "reverse.base_revenue", positive=True)
    if wacc <= g:
        die(f"反向 DCF: WACC({wacc}) 必须大于 g({g})")
    ev = number(price * shares + net_debt, 'reverse.ev')
    n = len(interim_fcf)
    pv_interim = sum(f / power(1 + wacc, i + 1, "反向 DCF 折现因子") for i, f in enumerate(interim_fcf))
    tv_needed = (ev - pv_interim) * power(1 + wacc, n, "反向 DCF 终值因子")
    fcf_req = number(tv_needed * (wacc - g), 'reverse.fcf_required')
    rows = []
    for m in steady_margins:
        if m <= 0:
            die(f"反向 DCF: steady_margin 必须为正（{m}）")
        rev_req = fcf_req / m
        cagr = power(rev_req / base_revenue, 1 / (n + 1), "反向 DCF CAGR") - 1 if base_revenue is not None and rev_req >= 0 else None
        rows.append({"margin": m, "revenue_required": rev_req, "implied_cagr": cagr})
    return {"ev": ev, "pv_interim": pv_interim, "fcf_required": fcf_req,
            "required_year": n + 1, "rows": rows}

def calibrate(price, lo, hi):
    """预注册标定（与 check_research_output.py 及 valuation-methods.md §9 完全一致）。"""
    price = number(price, "price", positive=True)
    lo, hi = number(lo, "range_low"), number(hi, "range_high")
    if lo > hi:
        die(f"标定: range_low({lo}) > range_high({hi})")
    if price < lo * 0.50: return "显著低估"
    if price < lo * 0.85: return "低估"
    if price <= hi * 1.15: return "合理"
    if price <= hi * 1.50: return "高估"
    return "显著高估"

# ---------- EPV / 三要素（Greenwald）----------

def epv_value(e, coc, basis='NOPAT', net_debt=0.0, excess_cash=0.0):
    e = number(e, "EPV.normalized_earnings")
    coc = number(coc, "EPV.coc", positive=True)
    basis = earnings_basis(basis)
    net_debt = debt_bridge(net_debt, excess_cash, "EPV")
    if basis == "NET_INCOME" and net_debt != 0:
        die("EPV: 净利润资本化已经得到权益价值，不得再次减净债")
    ev = number(e / coc, 'EPV value')
    if basis == 'NOPAT':
        return {'ev': ev, **common_equity(ev - net_debt, 'EPV')}
    return {'ev': None, **common_equity(ev, 'EPV')}

def franchise_growth_value(e, coc, g, roiic, basis='NOPAT', net_debt=0.0, excess_cash=0.0):
    epv_value(e, coc, basis, net_debt, excess_cash)  # Validate the common valuation basis/bridge.
    g = growth_rate(g, "epv.growth.g")
    if roiic is None:
        return None
    roiic = number(roiic, "epv.growth.roiic")
    if roiic <= 0 or not (coc > g) or not (0 <= g / roiic <= 1):
        return None
    v = number(e * (1 - g / roiic) / (coc - g), 'epv.growth.value')
    if earnings_basis(basis) == 'NOPAT':
        return {'ev': v, **common_equity(v - net_debt, 'epv.growth')}
    return {'ev': None, **common_equity(v, 'epv.growth')}

def moat_verdict(ratio):
    if ratio < 1.0:  return 'EPV<净资产 → 毁灭价值（ROIC<资本成本），规避'
    if ratio <= 1.3: return 'EPV≈净资产 → 无/弱护城河的辛苦生意'
    return 'EPV≫净资产 → 护城河的财务度量（差额=特许经营价值）'

def run_epv(c):
    object_input(c, 'epv')
    role, role_label = model_role(c, 'epv')
    basis = earnings_basis(c.get('earnings_basis', 'NOPAT'))
    if basis == 'NOPAT': firm_basis(c, 'epv')
    else: equity_basis(c, 'epv')
    e, coc = need(c, 'normalized_earnings', 'epv'), need(c, 'coc', 'epv')
    nd, xc = c.get('net_debt', 0.0), c.get('excess_cash', 0.0)
    sh = number(need(c, 'shares', 'epv'), 'epv.shares', positive=True)
    av, price = c.get('asset_value'), c.get('price')
    if av is not None: av = number(av, 'epv.asset_value', positive=True)
    if price is not None: price = number(price, 'epv.price', positive=True)
    ep = epv_value(e, coc, basis, nd, xc)
    rate_name = 'WACC' if basis == 'NOPAT' else 'CoE'
    print(f"\n=== 盈利能力价值 EPV === {role_label} | 口径 {basis} | 常态化盈利 {e} | {rate_name} {coc:.2%}")
    epv_ps = ep['equity'] / sh
    print(f"  EPV 权益价值 {ep['equity']:,.1f} | 每股 {epv_ps:,.2f}")
    print_equity_shortfall(ep, 'EPV')
    if av:
        ratio = ep['equity'] / av
        print(f"  护城河验证：EPV/净资产 = {ratio:.2f}x → {moat_verdict(ratio)}")
    if c.get('asset_series'):
        if not isinstance(c['asset_series'], list): die('epv.asset_series 必须是数组')
        print("  EPV/净资产 多年趋势：")
        for row in c['asset_series']:
            if not isinstance(row, list) or len(row) != 3:
                die("epv.asset_series 每行须为 [期间, 盈利, 资产价值]")
            yr, ee, aa = row
            ee = number(ee, 'epv.asset_series.earnings')
            aa = number(aa, 'epv.asset_series.asset_value', positive=True)
            print(f"    {yr}: {(ee/coc)/aa:.2f}x")
    growth_ps, fg = None, None
    g = c.get('growth')
    if g is not None:
        object_input(g, 'epv.growth')
        gg, roiic, mode = need(g, 'g', 'epv.growth'), g.get('roiic'), g.get('mode', 'franchise')
        gg = growth_rate(gg, 'epv.growth.g')
        if roiic is not None: roiic = number(roiic, 'epv.growth.roiic')
        fg = franchise_growth_value(e, coc, gg, roiic, basis, nd, xc) if mode == 'franchise' else None
        if fg is None:
            print("  成长价值：不可计算（需 franchise 模式、有效 ROIIC、g<coc、再投资率 g/ROIIC∈[0,1]）；保留零增长 EPV，不自动加价")
        else:
            growth_ps = fg['equity'] / sh
            warn = ' ⚠ ROIIC<coc，增长毁灭价值' if roiic < coc else ''
            print(f"  成长价值（franchise 严格式，g={gg:.1%}，ROIIC={roiic:.1%}）：每股 {growth_ps:,.2f}{warn}")
            print_equity_shortfall(fg, '成长价值')
    if av and price:
        asset_ps = av / sh
        ladder_label = '买点阶梯' if role == 'decision' else '条件估值参考'
        print(f"  {ladder_label}：底价 {asset_ps:,.2f}｜EPV {epv_ps:,.2f}" +
              (f"｜成长调整 {growth_ps:,.2f}" if growth_ps is not None else ""))
    return {"epv_ps": epv_ps, "growth_ps": growth_ps, **ep, "growth": fg, "role": role}

# ---------- EVA / 剩余收益 ----------

def run_eva(c, top):
    firm_basis(c, 'eva')
    role, role_label = model_role(c, 'eva')
    ic = number(need(c, 'invested_capital', 'eva'), 'eva.invested_capital', positive=True)
    nopat = number(need(c, 'nopat', 'eva'), 'eva.nopat')
    if nopat <= 0:
        die("eva: 非正 NOPAT 不适用此资本反推衰减模型，请使用可审计的显式资本路径补充脚本")
    wacc = number(c.get('wacc', top.get('wacc')), 'eva.wacc', positive=True)
    sh = c.get('shares', top.get('shares'))
    if sh is not None: sh = number(sh, 'eva.shares', positive=True)
    nd = debt_bridge(c.get('net_debt', top.get('net_debt', 0.0)), c.get('excess_cash', 0), 'eva')
    fade = year_count(c.get('fade_years', 10), 'eva.fade_years', minimum=1)
    growth = growth_rate(c.get('nopat_growth', 0.0), 'eva.nopat_growth')
    rr, roiic = c.get('reinvestment_rate'), c.get('roiic')
    if rr is not None:
        rr = number(rr, 'eva.reinvestment_rate', minimum=0)
        if rr > 1: die("eva.reinvestment_rate 不得大于 1")
    if roiic is not None: roiic = number(roiic, 'eva.roiic', positive=True)
    roic = nopat / ic
    spread0 = roic - wacc
    print(f"\n=== EVA / 剩余收益 === {role_label} | NOPAT {nopat} | 投入资本 {ic} | ROIC {roic:.1%} | WACC {wacc:.2%} | 超额利差 {spread0:+.1%}")
    if rr is not None and roiic is not None:
        g_implied = rr * roiic
        print(f"  自洽检验：g = 再投资率 {rr:.0%} × ROIIC {roiic:.1%} = {g_implied:.1%}"
              f"（DCF 中假设的增速若高于此值，意味着需要外部资本或假设不自洽）")
    pv_eva, ic_t = 0.0, ic
    nopat_t = number(nopat * (1 + growth), 'eva.NOPAT_1', positive=True)
    spread1 = nopat_t / ic - wacc
    rows = []
    for t in range(1, fade + 1):
        begin_ic = ic_t
        capital_charge = wacc * begin_ic
        eva_t = nopat_t - capital_charge
        next_nopat = number(nopat_t * (1 + growth), 'eva.next_nopat', positive=True)
        next_roic = wacc + spread1 * (1 - t / fade)
        if not math.isfinite(next_roic) or next_roic <= 0:
            die("eva: 目标资本回报率非正/无效，此衰减模型不适用，请提供显式资本路径补充脚本")
        ic_t = number(next_nopat / next_roic, 'eva.end_capital', positive=True)
        reinvestment = ic_t - begin_ic
        fcff = nopat_t - reinvestment
        discount = power(1 + wacc, t, 'EVA 折现因子')
        pv_eva += eva_t / discount
        rows.append({"year": t, "begin_capital": begin_ic, "nopat": nopat_t,
                     "capital_charge": capital_charge, "eva": eva_t,
                     "end_capital": ic_t, "reinvestment": reinvestment,
                     "fcff": fcff, "roic": nopat_t / begin_ic,
                     "pv_eva": eva_t / discount})
        nopat_t = next_nopat
    value = number(ic + pv_eva, 'EVA value')
    bridge = common_equity(value - nd, 'EVA')
    equity = bridge['equity']
    print(f"  剩余收益价值：投入资本 {ic:,.1f} + PV(EVA, {fade}年衰减) {pv_eva:,.1f} = EV {value:,.1f}")
    print("  年度 | 期初资本 | NOPAT | 资本费用 | EVA | 期末资本 | 再投资 | FCFF")
    for row in rows:
        print(f"  {row['year']:>4} | {row['begin_capital']:,.2f} | {row['nopat']:,.2f} | "
              f"{row['capital_charge']:,.2f} | {row['eva']:,.2f} | {row['end_capital']:,.2f} | "
              f"{row['reinvestment']:,.2f} | {row['fcff']:,.2f}")
    print(f"  终值年 {fade + 1} 起：NOPAT={nopat_t:,.2f}、资本={ic_t:,.2f} 保持不变，ROIC=WACC，EVA=0；期末终值=资本")
    if sh:
        print(f"  权益 {equity:,.1f} | 每股 {equity/sh:,.2f}")
    print_equity_shortfall(bridge, 'EVA')
    if spread0 < 0:
        print("  ⚠ 当前 EVA 为负：公司未赚回资本成本；新增投资是否毁灭价值取决于其增量回报")
    return {"per_share": equity / sh if sh else None, "ev": value, **bridge,
            "pv_eva": pv_eva, "rows": rows, "terminal_ev": ic_t,
            "terminal_nopat": nopat_t, "terminal_growth": 0.0, "terminal_eva": 0.0, "role": role}

# ---------- PVGO 分解 ----------

def run_pvgo(c, top):
    object_input(c, 'pvgo')
    basis = earnings_basis(c.get('earnings_basis', 'NET_INCOME'))
    if basis != 'NET_INCOME': die("pvgo: 每股净利润口径必须搭配权益成本 CoE；不接受企业 NOPAT")
    equity_basis(c, 'pvgo')
    e = number(need(c, 'earnings_ps', 'pvgo'), 'pvgo.earnings_ps')
    r = number(need(c, 'r', 'pvgo（须显式提供 CoE，不从 WACC 继承）'), 'pvgo.r', positive=True)
    price = number(c.get('price', top.get('price')), 'pvgo.price', positive=True)
    zg = e / r
    pvgo = price - zg
    pct = pvgo / price
    print(f"\n=== PVGO 分解 === 每股盈利 {e} ÷ CoE {r:.2%} = 零增长价值 {zg:,.2f}/股")
    print(f"  现价 {price} = 零增长 {zg:,.2f} + 增长期权 PVGO {pvgo:,.2f} → **现价的 {pct:.0%} 在为未来增长付费**")
    if pct > 0.5:
        print("  ⚠ PVGO>50%：报告必须回答“这些增长从哪来、谁买单”（对照 base-rates）")
    return {"pvgo_pct": pct, "pvgo": pvgo, "zero_growth_value": zg}

# ---------- 蒙特卡洛 ----------

def run_montecarlo(c, top):
    firm_basis(c, 'montecarlo')
    role, role_label = model_role(c, 'montecarlo')
    n = year_count(c.get('n', 2000), 'montecarlo.n', minimum=1)
    seed = c.get('seed', 42)
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        die('montecarlo.seed 必须是整数或字符串')
    rng = random.Random(seed)
    years = year_count(c.get('years', 5), 'montecarlo.years', minimum=1)
    rev0 = number(need(c, 'base_revenue', 'montecarlo'), 'montecarlo.base_revenue', positive=True)
    gm = growth_rate(need(c, 'growth_mean', 'montecarlo'), 'montecarlo.growth_mean')
    gs = number(need(c, 'growth_std', 'montecarlo'), 'montecarlo.growth_std', minimum=0)
    ml, mm, mh = [number(need(c, key, 'montecarlo'), f'montecarlo.{key}')
                  for key in ('margin_low', 'margin_mode', 'margin_high')]
    if not ml <= mm <= mh: die('montecarlo: margin_low ≤ margin_mode ≤ margin_high 必须成立')
    if gm - 3 * gs <= -1: die('montecarlo: 增长率分布的下界必须大于 -100%')
    defaults_wacc = None
    if 'wacc_low' not in c or 'wacc_high' not in c:
        defaults_wacc = number(need(top, 'wacc', 'montecarlo 顶层默认'), 'wacc', positive=True)
    wl = number(c['wacc_low'] if 'wacc_low' in c else defaults_wacc - 0.01, 'montecarlo.wacc_low', positive=True)
    wh = number(c['wacc_high'] if 'wacc_high' in c else defaults_wacc + 0.01, 'montecarlo.wacc_high', positive=True)
    g = growth_rate(c.get('terminal_g', top.get('terminal_g')), 'montecarlo.terminal_g')
    if not g < wl <= wh: die('montecarlo: terminal_g < wacc_low ≤ wacc_high 必须成立；不自动改写折现率')
    fade = year_count(c.get('fade_years', 5), 'montecarlo.fade_years')
    dilution = growth_rate(c.get('annual_dilution', 0.0), 'montecarlo.annual_dilution')
    shares = number(top.get('shares'), 'montecarlo.shares', positive=True)
    nd = debt_bridge(top.get('net_debt', 0.0), c.get('excess_cash', 0), 'montecarlo')
    price = top.get('price')
    if price is not None: price = number(price, 'montecarlo.price', positive=True)
    vals, raw_vals, raw_equities, shortfalls = [], [], [], []
    for _ in range(n):
        gr = max(min(rng.gauss(gm, gs), gm + 3 * gs), gm - 3 * gs)   # 截断正态
        mg = rng.triangular(ml, mh, mm)
        wc = rng.uniform(wl, wh)
        rev, fcfs = rev0, []
        for _t in range(years):
            rev *= (1 + gr); fcfs.append(rev * mg)
        sc = {'fcf': fcfs, 'fade_years': fade, 'fade_g_start': gr,
              'annual_dilution': dilution}
        result = dcf_value(sc, wc, g, shares, nd)
        vals.append(result['per_share'])
        raw_vals.append(result['raw_per_share'])
        raw_equities.append(result['raw_equity'])
        shortfalls.append(result['equity_shortfall'])
    vals.sort()
    q = lambda p: vals[min(int(p * n), n - 1)]
    mean = sum(vals) / n
    print(f"\n=== 蒙特卡洛（n={n}） === {role_label} | 公允价值分布（每股）")
    print(f"  P10 {q(.10):,.1f} | P25 {q(.25):,.1f} | P50 {q(.50):,.1f} | P75 {q(.75):,.1f} | P90 {q(.90):,.1f} | 均值 {mean:,.1f}")
    out = {"p10": q(.10), "p50": q(.50), "p90": q(.90), "mean": mean,
           "annual_dilution": dilution, "forecast_years": years + fade, "role": role,
           "raw_mean_per_share": sum(raw_vals) / n, "min_raw_equity": min(raw_equities),
           "equity_shortfall_count": sum(shortfall > 0 for shortfall in shortfalls),
           "mean_equity_shortfall": sum(shortfalls) / n}
    if out['equity_shortfall_count']:
        print(f"  {out['equity_shortfall_count']}/{n} 次模拟原始权益为负并归零；最低原始权益 {out['min_raw_equity']:,.2f}，"
              f"全样本平均权益缺口 {out['mean_equity_shortfall']:,.2f}；{EQUITY_FLOOR_NOTE}")
    if price:
        p_loss = sum(1 for v in vals if v < price) / n
        out["p_loss"] = p_loss
        print(f"  P(内在价值 < 现价 {price}) = {p_loss:.0%}  ← “现价买入是错误”的模型概率")
    return out

# ---------- 仓位思维 ----------

def run_position(results, cfg):
    price = cfg.get('price')
    scs = cfg.get('scenarios') or []
    if not price or not results or not scs:
        return
    price = number(price, '仓位.price', positive=True)
    pairs = []
    for sc in scs:
        if sc['name'] not in results or sc.get('role', 'decision') != 'decision':
            continue
        p = number(sc.get('prob', 0.0), '仓位.prob')
        v = number(results[sc['name']]['per_share'], '仓位.per_share')
        if v < 0:
            die('仓位: 普通股每股价值不得为负，请先处理有限责任并保留原始权益缺口')
        pairs.append((p, v))
    if not pairs: return
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p, _ in pairs) or not math.isclose(sum(p for p, _ in pairs), 1, abs_tol=1e-6):
        die('仓位: 决策情景概率必须在 [0,1] 且合计为 1')
    ev_ret = sum(p * (v / price - 1) for p, v in pairs)
    ups = [(p, v / price - 1) for p, v in pairs if v > price]
    downs = [(p, 1 - v / price) for p, v in pairs if v <= price]
    up_mag = max((v for _, v in ups), default=0.0)
    down_mag = max((v for _, v in downs), default=0.0)
    asym = up_mag / down_mag if down_mag > 0 else float('inf')
    print(f"\n=== 仓位思维 === 概率加权期望收益 EV = {ev_ret:+.0%}")
    print(f"  上行幅度（牛） {up_mag:+.0%} | 下行幅度（熊） {-down_mag:.0%} | 不对称比 {asym:.1f}"
          + ("（<1.5：赔率结构不支持重仓）" if asym < 1.5 else ""))
    p_up = sum(p for p, _ in ups); p_dn = sum(p for p, _ in downs)
    b = sum(p * v for p, v in ups) / p_up if p_up else 0.0
    a = sum(p * v for p, v in downs) / p_dn if p_dn else 0.0
    if a > 0 and b > 0:
        kelly = (p_up * b - p_dn * a) / (a * b)
        lite = max(0.0, min(kelly / 4, 0.15))
        size = "标准" if lite >= 0.08 else ("中" if lite >= 0.04 else ("小" if lite > 0 else "零"))
        print(f"  Kelly-lite（¼Kelly，上限15%）≈ {lite:.0%} → 该赔率结构支持 **{size}仓位**（量级参考，非配置建议）")

# ---------- 主流程 ----------

def run(cfg):
    object_input(cfg, 'config')
    shares = cfg.get('shares')
    if shares is not None: shares = number(shares, 'shares', positive=True)
    nd = debt_bridge(cfg.get('net_debt', 0), cfg.get('excess_cash', 0), '顶层')
    wacc, g = cfg.get('wacc'), cfg.get('terminal_g')
    if wacc is not None: wacc = number(wacc, 'wacc', positive=True)
    if g is not None: g = growth_rate(g, 'terminal_g')
    price = cfg.get("price")
    if price is not None: price = number(price, 'price', positive=True)
    scs = cfg.get('scenarios', [])
    if not isinstance(scs, list): die('scenarios 必须是数组')
    if scs or cfg.get('reverse'):
        shares = number(shares, '顶层 shares', positive=True)
        wacc = number(wacc, '顶层 wacc', positive=True)
        g = growth_rate(g, '顶层 terminal_g')
    if scs or any(cfg.get(k) for k in ('reverse', 'eva', 'montecarlo')):
        firm_basis(cfg, '顶层企业估值')

    print('=== 估值计算 === 仅运行配置中选定的方法')
    if scs:
        print(f"  FCFF | 股本 {shares} | 净债 {nd}（已扣非经营现金） | WACC {wacc:.2%} | g {g:.2%}")
        print('  每股简化口径：全部权益现值÷完整预测期末股本；稀释覆盖显式期和衰减期，终值期不继续稀释')

    weighted, probs, results = 0.0, 0.0, {}
    decision_count = 0
    if scs:
        names = set()
        for sc in scs:
            object_input(sc, 'scenario')
            name = need(sc, 'name', 'scenario')
            if not isinstance(name, str) or not name.strip() or name in names:
                die('情景 name 必须是非空且唯一的字符串')
            names.add(name)
            if sc.get('role', 'decision') not in ('decision', 'conditional'):
                die(f'情景 {name}: role 必须是 decision 或 conditional')
            p = number(sc.get('prob', 0.0), f'情景 {name}.prob', minimum=0)
            if p > 1: die(f'情景 {name}: prob 不得超过 1')
            if sc.get('role', 'decision') == 'decision':
                probs += p
                decision_count += 1
        if decision_count and abs(probs - 1) > 1e-6:
            die(f'决策情景概率和 = {probs:.6f}，必须为 1；conditional 不计入')
        print("\n=== 情景 DCF ===")
        for sc in scs:
            r = dcf_value(sc, wacc, g, shares, nd)
            role = sc.get('role', 'decision')
            r['role'] = role
            results[sc["name"]] = r
            p = sc.get('prob', 0.0)
            if role == 'decision': weighted += p * r['per_share']
            role_label = f'decision p={p:.0%}' if role == 'decision' else 'conditional 条件情景（不进入决策加权/仓位）'
            tv_warn = " ⚠TV>80%" if r["tv_share"] > 0.80 else ""
            print(f"  {sc['name']:<8} {role_label}  EV {r['ev']:>9,.0f} | 每股 {r['per_share']:>7,.1f} "
                  f"| 退出P/FCF {r['exit_pfcf']:.1f}x | TV占比 {r['tv_share']:.0%}{tv_warn} "
                  f"| 期末股本 {r['shares_end']:.2f}")
            print_equity_shortfall(r, sc['name'])
        if decision_count:
            print(f"  ── 概率加权公允价值: {weighted:,.1f}/股"
                  + (f"（较现价 {(weighted/price-1):+.0%}）" if price else ""))
        else:
            print('  仅有条件情景：不输出决策加权价值和仓位')

    sens = cfg.get("sensitivity")
    if sens is not None:
        object_input(sens, 'sensitivity')
        name = need(sens, 'scenario', 'sensitivity')
        sc = next((s for s in scs if s['name'] == name), None)
        if sc is None: die(f"sensitivity.scenario `{sens['scenario']}` 不在 scenarios 中")
        gs = numeric_path(need(sens, 'g', 'sensitivity'), 'sensitivity.g')
        ws = numeric_path(need(sens, 'wacc', 'sensitivity'), 'sensitivity.wacc')
        print(f"\n=== 敏感性（{sens['scenario']}，{sc.get('role', 'decision')}，每股） ===")
        print("            " + "".join(f"g={gg:.1%}  " for gg in gs))
        for w in ws:
            cells = "".join(f"{dcf_value(sc, w, gg, shares, nd)['per_share']:>7,.1f} " for gg in gs)
            print(f"  WACC {w:.1%} {cells}")

    rev = cfg.get("reverse")
    if rev is not None:
        firm_basis(rev, 'reverse')
        debt_bridge(nd, rev.get('excess_cash', 0), 'reverse')
        r = reverse_dcf(price, shares, nd, wacc, g, need(rev, "interim_fcf", "reverse"),
                        need(rev, "steady_margins", "reverse"), rev.get("base_revenue"))
        print(f"\n=== 反向 DCF ===  EV {r['ev']:,.0f} | 建设期FCF现值 {r['pv_interim']:,.0f}")
        print(f"  现价隐含稳态首年（第 {r['required_year']} 年）FCFF: {r['fcf_required']:,.0f}/年；当前股本口径")
        for row in r["rows"]:
            cagr = f"，隐含营收 CAGR {row['implied_cagr']:.0%}" if row["implied_cagr"] is not None else ""
            print(f"    @FCF率 {row['margin']:.0%} → 需营收 {row['revenue_required']:,.0f}{cagr}")

    if cfg.get("pvgo") is not None:        run_pvgo(cfg["pvgo"], cfg)
    if cfg.get("epv") is not None:         run_epv(cfg["epv"])
    if cfg.get("eva") is not None:         run_eva(cfg["eva"], cfg)
    if cfg.get("montecarlo") is not None:  run_montecarlo(cfg["montecarlo"], cfg)
    run_position(results, cfg)

    if 'range_low' in cfg or 'range_high' in cfg:
        lo, hi = need(cfg, 'range_low', '标定'), need(cfg, 'range_high', '标定')
        print(f"\n=== 标定 === 综合区间 [{lo}, {hi}] × 现价 {price} → **{calibrate(price, lo, hi)}**")
        print("  （动作映射与否决项见 valuation-methods.md §9，由分析师结合不确定性与财报可信度执行）")
    return results

DEMO = {
    "price": 100.0, "shares": 1.0, "net_debt": 5.0, "wacc": 0.09, "terminal_g": 0.03,
    "scenarios": [
        {"name": "bear", "prob": 0.3, "revenue": [10, 11, 12, 13, 14],
         "fcf_margin": [0.10, 0.12, 0.14, 0.15, 0.16], "fade_years": 3,
         "fade_g_start": 0.05, "annual_dilution": 0.01},
        {"name": "base", "prob": 0.5, "revenue": [10, 12, 14, 17, 20],
         "fcf_margin": [0.12, 0.15, 0.18, 0.20, 0.22], "fade_years": 5,
         "fade_g_start": 0.10, "annual_dilution": 0.01},
        {"name": "bull", "prob": 0.2, "revenue": [10, 13, 17, 22, 28],
         "fcf_margin": [0.14, 0.18, 0.22, 0.25, 0.28], "fade_years": 5,
         "fade_g_start": 0.14, "annual_dilution": 0.02}],
    "sensitivity": {"scenario": "base", "wacc": [0.08, 0.09, 0.10], "g": [0.02, 0.03, 0.04]},
    "reverse": {"interim_fcf": [1.2, 1.8, 2.5, 3.4, 4.4],
                "steady_margins": [0.20, 0.25], "base_revenue": 10},
    "range_low": 45, "range_high": 75,
    "pvgo": {"earnings_ps": 5.5, "r": 0.11, "discount_rate_basis": "COE"},
    "epv": {"earnings_basis": "NOPAT", "normalized_earnings": 6.0, "coc": 0.09,
            "net_debt": 5.0, "shares": 1.0, "asset_value": 30.0, "price": 100.0,
            "growth": {"g": 0.04, "roiic": 0.20, "mode": "franchise"},
            "asset_series": [["FY-2", 4.5, 26], ["FY-1", 5.2, 28], ["最新", 6.0, 30]]},
    "eva": {"invested_capital": 40.0, "nopat": 6.0, "fade_years": 10,
            "reinvestment_rate": 0.4, "roiic": 0.20},
    "montecarlo": {"n": 2000, "base_revenue": 10, "years": 5,
                   "annual_dilution": 0.01,
                   "growth_mean": 0.14, "growth_std": 0.06,
                   "margin_low": 0.10, "margin_mode": 0.18, "margin_high": 0.26},
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="JSON 假设文件路径")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    try:
        if a.demo:
            run(DEMO)
        elif a.config:
            with open(a.config) as f:
                run(json.load(f))
        else:
            ap.print_help(); sys.exit(1)
    except ValueError as e:
        print(str(e), file=sys.stderr); sys.exit(2)
