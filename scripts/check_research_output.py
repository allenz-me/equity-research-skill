#!/usr/bin/env python3
"""equity-research skill · 财务/估值一致性检查器

用法：
    python scripts/check_research_output.py --report report.md --assumptions valuation.json
    python scripts/check_research_output.py --financials financials.csv
    python scripts/check_research_output.py --demo

输入都是可选的；脚本会检查已提供文件中可复算的内容。仅使用 Python 标准库。
"""

import argparse
import csv
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
LABELS = ["显著低估", "低估", "合理", "高估", "显著高估"]
LABEL_ALIASES = {
    "显著低估": ["显著低估", "significantly undervalued", "deeply undervalued"],
    "低估": ["低估", "undervalued"],
    "合理": ["合理", "fairly valued", "fair value", "fairly priced"],
    "高估": ["高估", "overvalued"],
    "显著高估": ["显著高估", "significantly overvalued", "deeply overvalued"],
}
MISSING_DATA_MARKERS = ["未获取到", "not obtained", "not available", "unavailable", "not disclosed", "not found"]
JUDGMENT_MARKERS = ["我的判断", "my view", "my judgment", "my assessment", "assessment:"]
SOURCE_MARKERS = ["数据来源", "来源", "sources and timestamps", "data sources", "sources", "source:"]
INDUSTRY_RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "references", "industry-rules.json")
ZH_TEMPLATE_MARKERS = ["本章要点", "我的判断", "未获取到", "数据来源与时间戳", "必写结论句", "估值假设表"]
EN_TEMPLATE_MARKERS = ["key takeaways:", "my view:", "not obtained", "sources and timestamps", "disclaimer: not investment advice"]


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    detail: str = ""
    file: str = ""

    def line(self) -> str:
        loc = f" [{self.file}]" if self.file else ""
        detail = f"\n    {self.detail}" if self.detail else ""
        return f"[{self.severity}] {self.code}{loc}: {self.message}{detail}"


def add(issues: List[Issue], severity: str, code: str, message: str, detail: str = "", file: str = "") -> None:
    issues.append(Issue(severity, code, message, detail, file))


def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def norm_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if char.isalnum())


def parse_number(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    s = str(value).strip()
    if not s or s in {"-", "—", "N/A", "NA", "n/a", "未获取到", "not obtained", "not available"}:
        return None
    mult = 1.0
    if s.endswith("%"):
        mult = 0.01
        s = s[:-1]
    s = s.replace(",", "").replace("$", "").replace("￥", "").replace("¥", "")
    s = s.replace("倍", "").replace("x", "").replace("X", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    return float(m.group(0)) * mult


def pct(value: float) -> str:
    return f"{value:.1%}"


def close_enough(a: float, b: float, rel_tol: float = 0.015, abs_tol: float = 0.02) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b), 1.0))




def detect_labels(text: str) -> List[str]:
    """长标签优先匹配，避免“显著低估”被同时计为“低估”；英文标签返回中文 canonical。"""
    found: List[str] = []
    masked = text.lower()
    aliases = []
    for canonical, values in LABEL_ALIASES.items():
        for alias in values:
            aliases.append((canonical, alias.lower()))
    for canonical, alias in sorted(aliases, key=lambda item: len(item[1]), reverse=True):
        if alias in masked and canonical not in found:
            found.append(canonical)
            masked = masked.replace(alias, "■" * len(alias))
    return found


def has_any(text: str, patterns: Sequence[str]) -> bool:
    lower = text.lower()
    return any(pattern.lower() in lower for pattern in patterns)


def load_industry_rules() -> Dict[str, Dict]:
    with open(INDUSTRY_RULES_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload["industries"]


def normalize_industry_args(values: Optional[Sequence[str]]) -> List[str]:
    industries: List[str] = []
    for value in values or []:
        for item in value.split(","):
            slug = item.strip().lower()
            if slug and slug not in industries:
                industries.append(slug)
    return industries


def detect_declared_industries(text: str, rules: Dict[str, Dict]) -> List[str]:
    matches = re.findall(r"(?:行业附录|industry append(?:ix|ices))\s*[:：]\s*([^\n]+)", text, flags=re.I)
    declared = " ".join(matches).lower()
    if not declared:
        return []
    declared_words = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", declared)
    declared_compact = declared_words.replace(" ", "")
    found: List[str] = []
    for slug, rule in rules.items():
        candidates = [slug, rule.get("name_zh", ""), rule.get("name_en", "")] + rule.get("aliases", [])
        normalized = [re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", candidate.lower()).strip() for candidate in candidates if candidate]
        if any(candidate in declared_words or candidate.replace(" ", "") in declared_compact for candidate in normalized):
            found.append(slug)
    return found


def detect_report_language(text: str) -> Optional[str]:
    if re.search(r"^# .+Equity Research Report\s*$", text, flags=re.I | re.M):
        return "en"
    if re.search(r"^# .+(个股投资研究报告|财报深度分析)\s*$", text, flags=re.M):
        return "zh"
    return None


def check_language_consistency(text: str, language: str, path: str, issues: List[Issue]) -> None:
    resolved = detect_report_language(text) if language == "auto" else language
    if not resolved:
        add(issues, "P2", "REPORT_LANGUAGE_UNDETERMINED", "无法从标题判定报告语言；请使用 --language zh 或 --language en。", file=path)
        return
    conflicting = ZH_TEMPLATE_MARKERS if resolved == "en" else EN_TEMPLATE_MARKERS
    found = [marker for marker in conflicting if marker.lower() in text.lower()]
    if found:
        add(
            issues,
            "P1",
            "REPORT_LANGUAGE_MIXED",
            "报告含与目标语言不一致的模板标记。",
            detail=f"language={resolved}; conflicting_markers={', '.join(found)}",
            file=path,
        )


def check_industry_requirements(
    text: str,
    requested: Optional[Sequence[str]],
    path: str,
    issues: List[Issue],
) -> None:
    rules = load_industry_rules()
    table_text = "\n".join(line for line in text.splitlines() if "|" in line)
    slugs = normalize_industry_args(requested)
    if "auto" in slugs:
        slugs = [slug for slug in slugs if slug != "auto"] + detect_declared_industries(text, rules)
    if not slugs:
        slugs = detect_declared_industries(text, rules)
    if not slugs:
        add(
            issues,
            "P2",
            "REPORT_NO_INDUSTRY_DECLARATION",
            "报告未声明主/次行业附录，无法执行行业特定 KPI 检查。",
            detail="在报告中写“行业附录: saas”或运行检查器时传入 --industry saas。",
            file=path,
        )
        return
    for slug in dict.fromkeys(slugs):
        rule = rules.get(slug)
        if not rule:
            add(issues, "P1", "REPORT_UNKNOWN_INDUSTRY", f"未知行业规则：{slug}。", file=path)
            continue
        for group in rule.get("required_groups", []):
            if not has_any(table_text, group.get("terms", [])):
                add(
                    issues,
                    "P1",
                    "REPORT_INDUSTRY_KPI_MISSING",
                    f"{rule['name_zh']}报告的表格缺少必备 KPI 组：{group['label']}。",
                    detail=f"accepted_terms={', '.join(group.get('terms', []))}",
                    file=path,
                )


def text_mentions_ai_capex(text: str) -> bool:
    lower = text.lower()
    return ("ai" in lower or "人工智能" in text or "cloud" in lower or "云" in text) and (
        "capex" in lower or "资本开支" in text or "数据中心" in text
    )


def calibrate(price: float, lo: float, hi: float) -> str:
    if price < lo * 0.50:
        return "显著低估"
    if price < lo * 0.85:
        return "低估"
    if price <= hi * 1.15:
        return "合理"
    if price <= hi * 1.50:
        return "高估"
    return "显著高估"


def load_csv(path: str, issues: Optional[List[Issue]] = None) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    aliases: Dict[str, str] = {}
    grouped: Dict[str, List[str]] = {}
    for header in reader.fieldnames or []:
        grouped.setdefault(norm_key(header), []).append(header)
    for key, headers in grouped.items():
        if not key or len(headers) > 1:
            if issues is not None:
                add(
                    issues,
                    "P1",
                    "FINANCIALS_AMBIGUOUS_HEADER" if key else "FINANCIALS_EMPTY_HEADER",
                    "财务 CSV 表头重复或规范化后冲突；相关列不参与计算。" if key else "财务 CSV 含空或无法识别的表头；相关列不参与计算。",
                    detail=f"headers={headers!r}",
                    file=path,
                )
            continue
        aliases[key] = headers[0]
    return rows, aliases


def pick(aliases: Dict[str, str], names: Sequence[str]) -> Optional[str]:
    for name in names:
        key = norm_key(name)
        if key in aliases:
            return aliases[key]
    return None


def row_num(row: Dict[str, str], col: Optional[str]) -> Optional[float]:
    return parse_number(row.get(col)) if col else None


def check_report(
    path: str,
    assumptions: Optional[Dict],
    issues: List[Issue],
    industries: Optional[Sequence[str]] = None,
    language: str = "auto",
    scope: str = "deep",
) -> None:
    text = load_text(path)
    lower = text.lower()

    if scope != "deep":
        if language != "auto" or detect_report_language(text):
            check_language_consistency(text, language, path, issues)
        if scope == "focused":
            if not has_any(text, SOURCE_MARKERS) and not re.search(r"https?://", text):
                add(issues, "P1", "REPORT_NO_SOURCE_SECTION", "专题分析未发现来源说明或链接。", file=path)
            if not re.search(r"20\d{2}[-年/.]\d{1,2}", text):
                add(issues, "P2", "REPORT_NO_TIMESTAMP", "专题分析未发现数据日期或时间戳。", file=path)
        check_model_report_markers(text, assumptions, path, issues)
        check_valuation_labels(text, assumptions, path, issues)
        return

    check_language_consistency(text, language, path, issues)
    check_industry_requirements(text, industries, path, issues)

    if not has_any(text, SOURCE_MARKERS):
        add(issues, "P1", "REPORT_NO_SOURCE_SECTION", "报告未发现来源清单或来源说明。", file=path)
    if not re.search(r"20\d{2}[-年/.]\d{1,2}", text):
        add(issues, "P2", "REPORT_NO_TIMESTAMP", "报告未发现明确日期或时间戳。", file=path)
    if not has_any(text, MISSING_DATA_MARKERS):
        add(issues, "P3", "REPORT_NO_MISSING_DATA_MARKER", "报告未出现缺失数据标记（如“未获取到”或“Not obtained”）；若确无缺失数据可忽略。", file=path)
    if not has_any(text, JUDGMENT_MARKERS):
        add(issues, "P2", "REPORT_NO_JUDGMENT_MARKER", "报告未显式标注判断（如“我的判断”或“My view”），事实与判断可能混在一起。", file=path)
    check_model_report_markers(text, assumptions, path, issues)
    if not has_any(text, ["预测登记", "预测与验证", "forecast register", "forecast tracking"]):
        add(issues, "P2", "REPORT_NO_FORECAST_REGISTER", "报告未发现带验证期限的预测登记。", file=path)
    if not has_any(text, ["验证日期", "验证时点", "validation date", "review date"]):
        add(issues, "P2", "REPORT_NO_FORECAST_REVIEW_DATE", "报告预测未发现明确验证日期。", file=path)

    has_decision_triad = (
        has_any(text, ["内在价值判断", "intrinsic value view", "intrinsic value judgment"])
        and has_any(text, ["未来 1–3 个月市场交易方向", "1-3 month market trading direction", "1–3 month market trading direction"])
        and has_any(text, ["投资动作", "action:", "investment action"])
    )
    if not has_decision_triad:
        add(
            issues,
            "P2",
            "REPORT_NO_DECISION_TRIAD",
            "报告未发现决策三分法：内在价值判断 / 未来 1–3 个月市场交易方向 / 投资动作。",
            file=path,
        )

    has_upside = has_any(text, ["上行证伪", "踏空", "上修证伪", "upside invalidation", "upside disconfirmation", "upside risk"])
    has_downside = has_any(text, ["下行证伪", "下修证伪", "破坏本报告乐观", "downside invalidation", "downside disconfirmation", "downside risk"])
    if has_any(text, ["证伪信号", "invalidation signal", "disconfirmation signal"]) and not (has_upside and has_downside):
        add(
            issues,
            "P2",
            "REPORT_DISCONFIRMATION_DIRECTION_AMBIGUOUS",
            "报告出现“证伪信号”，但未同时区分上行证伪与下行证伪。",
            file=path,
        )
    if has_any(text, ["观望", "wait and see", "hold off", "watch"]) and not has_upside:
        add(
            issues,
            "P2",
            "REPORT_WAIT_NO_UPSIDE_DISCONFIRMATION",
            "报告含“观望”动作，但未发现上行证伪/踏空条件。",
            file=path,
        )

    if has_any(text, ["未来 1–3 个月市场交易方向", "1-3 month market trading direction", "1–3 month market trading direction"]) and not has_any(text, ["市场最可能", "先交易", "再定价", "重定价", "market is most likely", "trade first", "repricing", "re-rating"]):
        add(
            issues,
            "P3",
            "REPORT_MARKET_PATH_UNEXPLAINED",
            "报告写了未来 1–3 个月市场交易方向，但未解释市场最可能先交易的变量。",
            file=path,
        )

    if text_mentions_ai_capex(text) and not (
        "维护性 capex" in lower
        or "维护性资本开支" in text
        or "成长性 capex" in lower
        or "成长性资本开支" in text
        or "维护性/成长" in text
    ):
        add(
            issues,
            "P2",
            "REPORT_AI_CAPEX_NOT_SPLIT",
            "报告讨论 AI/cloud/data center capex，但未拆分维护性 capex 与成长性 capex 或说明无法获取拆分。",
            file=path,
        )

    check_valuation_labels(text, assumptions, path, issues)


def check_model_report_markers(text: str, assumptions: Optional[Dict], path: str, issues: List[Issue]) -> None:
    """Only inspect valuation methods actually supplied; methods are not a quota."""
    if not assumptions:
        return
    models = [name for name in ("scenarios", "epv", "eva", "reverse", "pvgo", "montecarlo") if assumptions.get(name)]
    if models and "dcf.py" not in text.lower():
        add(issues, "P2", "REPORT_NO_DCF_SCRIPT_EVIDENCE", "报告未说明所用估值由脚本执行。", file=path)
    markers = {
        "scenarios": ("REPORT_NO_SCENARIO_VALUATION", ["情景", "scenario"]),
        "reverse": ("REPORT_NO_REVERSE_DCF", ["反向 dcf", "反向dcf", "reverse dcf"]),
        "epv": ("REPORT_NO_EPV", ["epv", "盈利能力价值", "三要素"]),
    }
    for model, (code, terms) in markers.items():
        if model in models and not has_any(text, terms):
            add(issues, "P2", code, f"报告未解释已使用的 {model} 估值结果。", file=path)


def check_valuation_labels(text: str, assumptions: Optional[Dict], path: str, issues: List[Issue]) -> None:
    if assumptions:
        price = parse_number(assumptions.get("price"))
        lo = parse_number(assumptions.get("range_low"))
        hi = parse_number(assumptions.get("range_high"))
        if price is not None and lo is not None and hi is not None and hi >= lo:
            expected = calibrate(price, lo, hi)
            found_labels = detect_labels(text)
            if expected not in found_labels:
                add(
                    issues,
                    "P1",
                    "REPORT_VALUATION_LABEL_MISMATCH",
                    f"报告结论未发现按规则应出现的估值标签：{expected}。",
                    f"price={price}, range=[{lo}, {hi}], report_labels={found_labels or 'none'}",
                    path,
                )


def check_assumptions(path: str, issues: List[Issue], require_review: bool = False) -> Dict:
    cfg = load_json(path)
    if not isinstance(cfg, dict):
        add(issues, "P1", "ASSUMPTION_CONFIG_INVALID", "估值假设必须是 JSON 对象。", file=path)
        return {}
    for name in ("reverse", "montecarlo", "eva", "epv", "pvgo"):
        if cfg.get(name) is not None and not isinstance(cfg[name], dict):
            add(issues, "P1", "ASSUMPTION_MODEL_INVALID", f"{name} 必须是对象。", file=path)
            return cfg
    if not isinstance(cfg.get("scenarios", []), list) or any(not isinstance(sc, dict) for sc in cfg.get("scenarios", [])):
        add(issues, "P1", "ASSUMPTION_MODEL_INVALID", "scenarios 必须是情景对象数组。", file=path)
        return cfg

    def numeric(block, key, context, required=False, positive=False):
        value = block.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            if required or key in block:
                add(issues, "P1", "ASSUMPTION_INVALID_NUMBER", f"{context}.{key} 必须是有限 JSON 数值，不能是字符串或布尔值。", file=path)
            return None
        if positive and value <= 0:
            add(issues, "P0", "ASSUMPTION_NON_POSITIVE", f"{context}.{key} 必须为正数。", file=path)
        return value

    def rate_basis(block, expected, context):
        actual = str(block.get("discount_rate_basis", expected)).upper()
        if actual == "COST_OF_EQUITY":
            actual = "COE"
        if actual != expected:
            add(issues, "P1", "VALUATION_RATE_BASIS_MISMATCH", f"{context} 的折现率口径应为 {expected}。", file=path)

    def cash_bridge(block, context):
        cash = numeric(block, "excess_cash", context)
        if cash is not None and cash != 0:
            add(issues, "P1", "VALUATION_CASH_DOUBLE_COUNT", f"{context}: net_debt 已扣非经营性现金，不得再加 excess_cash。", file=path)
        numeric(block, "net_debt", context)

    def firm_basis(block, context):
        rate_basis(block, "WACC", context)
        if str(block.get("cashflow_basis", "FCFF")).upper() != "FCFF":
            add(issues, "P1", "VALUATION_CASHFLOW_BASIS_MISMATCH", f"{context} 仅支持 FCFF / WACC 企业估值。", file=path)
        if str(block.get("valuation_basis", "firm")).lower() not in {"firm", "enterprise"}:
            add(issues, "P1", "VALUATION_CASHFLOW_BASIS_MISMATCH", f"{context} 必须采用企业价值口径。", file=path)
        if str(block.get("earnings_basis", "NOPAT")).upper() != "NOPAT":
            add(issues, "P1", "VALUATION_EARNINGS_BASIS_MISMATCH", f"{context} 必须采用 NOPAT 口径。", file=path)
        cash_bridge(block, context)

    def equity_basis(block, context):
        rate_basis(block, "COE", context)
        if str(block.get("cashflow_basis", "FCFE")).upper() != "FCFE" or str(block.get("valuation_basis", "equity")).lower() != "equity":
            add(issues, "P1", "VALUATION_CASHFLOW_BASIS_MISMATCH", f"{context} 必须采用股权口径。", file=path)
        cash_bridge(block, context)

    def discount_pair(rate, growth, context):
        if rate is not None and growth is not None and rate <= growth:
            add(issues, "P0", "DCF_WACC_NOT_ABOVE_G", f"{context}: 折现率必须大于永续增长率。", file=path)

    models = {name: cfg.get(name) for name in ("scenarios", "reverse", "montecarlo", "eva", "epv", "pvgo")}
    uses_dcf = bool(models["scenarios"]) or models["reverse"] is not None
    mc = models["montecarlo"] or {}
    eva = models["eva"] or {}
    uses_eva, uses_mc = models["eva"] is not None, models["montecarlo"] is not None
    uses_firm = uses_dcf or uses_mc or uses_eva
    needs_wacc = uses_dcf or (uses_eva and "wacc" not in eva) or (uses_mc and ("wacc_low" not in mc or "wacc_high" not in mc))
    needs_g = uses_dcf or (uses_mc and "terminal_g" not in mc)
    needs_shares = uses_dcf or uses_mc or (uses_eva and "shares" not in eva)
    price = numeric(cfg, "price", "valuation", required=models["reverse"] is not None, positive=True)
    shares = numeric(cfg, "shares", "valuation", required=needs_shares, positive=True)
    wacc = numeric(cfg, "wacc", "valuation", required=needs_wacc, positive=True)
    g = numeric(cfg, "terminal_g", "valuation", required=needs_g)
    if uses_firm:
        firm_basis(cfg, "valuation")
    else:
        cash_bridge(cfg, "valuation")
    if uses_dcf:
        discount_pair(wacc, g, "DCF")
    if wacc is not None and not 0.03 <= wacc <= 0.20:
        add(issues, "P2", "DCF_WACC_UNUSUAL", "WACC 超出常见区间，请确认口径。", file=path)
    if g is not None and not -0.02 <= g <= 0.05:
        add(issues, "P2", "DCF_TERMINAL_G_UNUSUAL", "永续增长率超出常见区间，请确认长期增长锚。", file=path)
    lo, hi = numeric(cfg, "range_low", "valuation"), numeric(cfg, "range_high", "valuation")
    if lo is not None and hi is not None and lo > hi:
        add(issues, "P0", "VALUATION_RANGE_INVERTED", "综合估值区间下限高于上限。", file=path)

    scenarios = models["scenarios"] or []
    prob_sum, decision_count, seen = 0.0, 0, set()
    for idx, sc in enumerate(scenarios):
        name = str(sc.get("name") or f"scenario_{idx}")
        if name in seen:
            add(issues, "P1", "DCF_DUPLICATE_SCENARIO", "情景名称重复。", name, path)
        seen.add(name)
        firm_basis(sc, name)
        role = sc.get("role", "decision")
        if role not in {"decision", "conditional"}:
            add(issues, "P1", "DCF_SCENARIO_INVALID_ROLE", "情景 role 仅支持 decision 或 conditional。", name, path)
        prob = numeric(sc, "prob", name, required=role == "decision")
        if prob is not None and not 0 <= prob <= 1:
            add(issues, "P0", "DCF_SCENARIO_PROB_OUT_OF_RANGE", "情景概率必须在 0 到 1 之间。", name, path)
        if role == "decision":
            decision_count += 1
            prob_sum += prob or 0.0
            if not str(sc.get("probability_rationale") or "").strip():
                add(issues, "P2", "DCF_PROBABILITY_RATIONALE_MISSING", "参与决策加权的情景缺少 probability_rationale。", name, path)
            if (sc.get("evidence_strength") or sc.get("evidence")) and not str(sc.get("evidence_update") or "").strip():
                add(issues, "P2", "DCF_EVIDENCE_UPDATE_MISSING", "情景标注了证据强度，但未说明新证据如何影响概率；请填写 evidence_update。", name, path)
        if "fcf" in sc:
            arrays = [("fcf", sc["fcf"])]
        else:
            arrays = [("revenue", sc.get("revenue")), ("fcf_margin", sc.get("fcf_margin"))]
            if all(isinstance(values, list) for _, values in arrays) and len(arrays[0][1]) != len(arrays[1][1]):
                add(issues, "P0", "DCF_DRIVER_LENGTH_MISMATCH", "revenue 与 fcf_margin 长度不一致。", name, path)
        for key, values in arrays:
            if not isinstance(values, list) or not values:
                add(issues, "P1", "DCF_SCENARIO_NO_FCF_OR_DRIVERS", "情景必须提供非空 fcf，或 revenue + fcf_margin。", name, path)
            else:
                for index, value in enumerate(values):
                    numeric({key: value}, key, f"{name}[{index}]", required=True)
        dilution = numeric(sc, "annual_dilution", name)
        if dilution is not None:
            if dilution <= -1:
                add(issues, "P0", "DCF_DILUTION_INVALID", "年化股本变化必须大于 -100%。", name, path)
            elif not -0.20 <= dilution <= 0.20:
                add(issues, "P2", "DCF_DILUTION_UNUSUAL", "年化股本变化超出常见区间，请确认。", name, path)
    if decision_count and abs(prob_sum - 1.0) > 0.005:
        add(issues, "P1", "DCF_PROBABILITY_SUM_NOT_ONE", "参与决策的情景概率之和不等于 1；conditional 不参与加权。", f"prob_sum={prob_sum:.4f}", path)

    epv = models["epv"]
    if epv is not None:
        numeric(epv, "normalized_earnings", "epv", required=True)
        coc = numeric(epv, "coc", "epv", required=True, positive=True)
        numeric(epv, "shares", "epv", required=True, positive=True)
        basis = str(epv.get("earnings_basis", "NOPAT")).upper()
        if basis not in {"NOPAT", "NET_INCOME", "NI"}:
            add(issues, "P1", "EPV_EARNINGS_BASIS_INVALID", "EPV earnings_basis 仅支持 NOPAT / NET_INCOME / NI。", file=path)
        if basis == "NOPAT":
            firm_basis(epv, "epv")
        else:
            equity_basis(epv, "epv")
        debt = epv.get("net_debt", 0)
        if basis in {"NET_INCOME", "NI"} and isinstance(debt, (int, float)) and not isinstance(debt, bool) and math.isfinite(debt) and debt != 0:
            add(issues, "P1", "EPV_EQUITY_DEBT_DOUBLE_COUNT", "净利润 EPV 已为权益价值，不得再次扣净债。", file=path)
        growth = epv.get("growth") or {}
        if not isinstance(growth, dict):
            add(issues, "P1", "ASSUMPTION_MODEL_INVALID", "epv.growth 必须是对象。", file=path)
            growth = {}
        gg = numeric(growth, "g", "epv.growth")
        roiic = numeric(growth, "roiic", "epv.growth")
        if gg is not None and coc is not None and gg >= coc:
            add(issues, "P1", "EPV_G_NOT_BELOW_COC", "franchise 成长公式要求 g < 资本成本。", file=path)
        if roiic is not None and coc is not None and roiic < coc:
            add(issues, "P2", "EPV_ROIIC_BELOW_COC", "ROIIC 低于资本成本，增长可能毁灭价值。", file=path)
    if uses_eva:
        firm_basis(eva, "eva")
        if str(eva.get("earnings_basis", "NOPAT")).upper() != "NOPAT":
            add(issues, "P1", "EVA_EARNINGS_BASIS_INVALID", "EVA 仅支持 NOPAT 企业价值口径。", file=path)
        numeric(eva, "invested_capital", "eva", required=True, positive=True)
        numeric(eva, "nopat", "eva", required=True, positive=True)
        numeric(eva, "wacc", "eva", positive=True)
        numeric(eva, "shares", "eva", positive=True)
    if models["reverse"] is not None:
        firm_basis(models["reverse"], "reverse")
    if uses_mc:
        firm_basis(mc, "montecarlo")
        mc_g = numeric(mc, "terminal_g", "montecarlo") if "terminal_g" in mc else g
        low = numeric(mc, "wacc_low", "montecarlo", positive=True) if "wacc_low" in mc else (wacc - 0.01 if wacc is not None else None)
        high = numeric(mc, "wacc_high", "montecarlo", positive=True) if "wacc_high" in mc else (wacc + 0.01 if wacc is not None else None)
        discount_pair(low, mc_g, "montecarlo")
        if low is not None and high is not None and low > high:
            add(issues, "P1", "MC_WACC_RANGE_INVERTED", "蒙特卡洛 WACC 区间颠倒。", file=path)
    pvgo = models["pvgo"]
    if pvgo is not None:
        numeric(pvgo, "r", "pvgo", required=True, positive=True)
        numeric(pvgo, "earnings_ps", "pvgo", required=True)
        equity_basis(pvgo, "pvgo")
        if str(pvgo.get("earnings_basis", "NET_INCOME")).upper() not in {"NI", "NET_INCOME"}:
            add(issues, "P1", "PVGO_EARNINGS_BASIS_INVALID", "PVGO 每股盈利必须采用净利润 / 权益成本口径。", file=path)

    try:
        from research_review import review_issues
    except ModuleNotFoundError:
        from scripts.research_review import review_issues
    for issue in review_issues(cfg, require_review=require_review):
        add(issues, issue["severity"], issue["code"], issue["message"], issue.get("detail", ""), path)
    return cfg


def check_ratio(
    issues: List[Issue],
    path: str,
    row_label: str,
    row: Dict[str, str],
    numerator_col: Optional[str],
    denominator_col: Optional[str],
    provided_col: Optional[str],
    code: str,
    name: str,
    tolerance: float = 0.006,
) -> None:
    if not numerator_col or not denominator_col or not provided_col:
        return
    num = row_num(row, numerator_col)
    den = row_num(row, denominator_col)
    provided = row_num(row, provided_col)
    if num is None or den in (None, 0) or provided is None:
        return
    expected = num / den
    if abs(expected - provided) > tolerance:
        add(
            issues,
            "P1",
            code,
            f"{row_label} 的{name}与原始数值不一致。",
            f"expected={pct(expected)}, provided={pct(provided)}, numerator={num}, denominator={den}",
            path,
        )


def check_growth(
    issues: List[Issue],
    path: str,
    rows: List[Dict[str, str]],
    period_col: Optional[str],
    value_col: Optional[str],
    provided_col: Optional[str],
    lag: int,
    code: str,
    name: str,
) -> None:
    if not value_col or not provided_col or len(rows) <= lag:
        return
    for idx in range(lag, len(rows)):
        cur = row_num(rows[idx], value_col)
        prev = row_num(rows[idx - lag], value_col)
        provided = row_num(rows[idx], provided_col)
        label = rows[idx].get(period_col, f"row {idx + 2}") if period_col else f"row {idx + 2}"
        if cur is None or prev in (None, 0) or provided is None:
            continue
        expected = cur / prev - 1
        if abs(expected - provided) > 0.015:
            add(
                issues,
                "P1",
                code,
                f"{label} 的{name}与序列计算不一致。",
                f"expected={pct(expected)}, provided={pct(provided)}, current={cur}, prior={prev}",
                path,
            )




def growth_rate(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return cur / prev - 1


def check_forensics(rows, aliases, path, issues) -> None:
    """财报质量核查检查：应计质量 / 现金转化 / DSO与递延背离 / Beneish M-Score。
    列名要求见 references/forensic-accounting.md 第 7 节；缺列自动跳过对应项。"""
    ni = pick(aliases, ["net_income", "netincome", "净利润"])
    cfo = pick(aliases, ["cfo", "operating_cash_flow", "cash_from_operations", "经营现金流"])
    ta = pick(aliases, ["total_assets", "totalassets", "总资产"])
    rev = pick(aliases, ["revenue", "sales", "收入", "营收"])
    rec = pick(aliases, ["receivables", "accounts_receivable", "应收账款", "应收"])
    dfr = pick(aliases, ["deferred_revenue", "contract_liabilities", "递延收入", "合同负债"])
    gp = pick(aliases, ["gross_profit", "grossprofit", "毛利"])
    ppe = pick(aliases, ["ppe", "net_ppe", "固定资产"])
    ca = pick(aliases, ["current_assets", "流动资产"])
    dep = pick(aliases, ["depreciation", "折旧"])
    sga = pick(aliases, ["sga", "sg_a", "销售管理费用"])
    tl = pick(aliases, ["total_liabilities", "totalliabilities", "总负债"])

    # -- 应计比率与现金转化（逐行 + 趋势） --
    conv_series = []
    for idx, row in enumerate(rows):
        label = row.get(pick(aliases, ["period", "fiscal_period", "date", "财期", "期间"]) or "", f"row {idx + 2}")
        ni_v, cfo_v, ta_v = row_num(row, ni), row_num(row, cfo), row_num(row, ta)
        if ni_v is not None and cfo_v is not None and ta_v not in (None, 0):
            accr = (ni_v - cfo_v) / ta_v
            if accr > 0.10:
                add(issues, "P1", "FORENSIC_HIGH_ACCRUALS", f"{label} 总应计比率 {accr:.1%} > 10%，盈利质量红旗。",
                    f"net_income={ni_v}, cfo={cfo_v}, total_assets={ta_v}", path)
            elif accr > 0.05:
                add(issues, "P2", "FORENSIC_ELEVATED_ACCRUALS", f"{label} 总应计比率 {accr:.1%} 偏高（5–10%）。", "", path)
        if ni_v not in (None, 0) and cfo_v is not None and ni_v > 0:
            conv_series.append((label, cfo_v / ni_v))
    if len(conv_series) >= 3:
        last = conv_series[-1][1]
        declining = all(conv_series[i][1] >= conv_series[i + 1][1] for i in range(len(conv_series) - 3, len(conv_series) - 1))
        if last < 0.8 and declining:
            add(issues, "P2", "FORENSIC_CASH_CONVERSION_DECLINING",
                f"现金转化率降至 {last:.0%}（<80% 且连续下滑），利润与现金背离。",
                ", ".join(f"{l}={v:.0%}" for l, v in conv_series[-3:]), path)

    # -- DSO / 递延收入 与收入增速背离（末两行） --
    if len(rows) >= 2 and rev:
        r_g = growth_rate(row_num(rows[-1], rev), row_num(rows[-2], rev))
        if rec:
            rec_g = growth_rate(row_num(rows[-1], rec), row_num(rows[-2], rec))
            if r_g is not None and rec_g is not None and rec_g - r_g > 0.15:
                add(issues, "P2", "FORENSIC_DSO_DIVERGENCE",
                    f"应收增速 {rec_g:.0%} 超收入增速 {r_g:.0%} 逾 15pp，警惕塞货/放宽信用/提前确认。", "", path)
        if dfr:
            d_g = growth_rate(row_num(rows[-1], dfr), row_num(rows[-2], dfr))
            if r_g is not None and d_g is not None and r_g > 0 and d_g < 0:
                add(issues, "P2", "FORENSIC_DEFERRED_DIVERGENCE",
                    f"收入增长 {r_g:.0%} 而递延收入下降 {d_g:.0%}，订阅型公司此为透支未来信号。", "", path)

    # -- Beneish M-Score（末两行，需全列） --
    needed = [rev, rec, gp, ppe, ca, dep, sga, tl, ta, ni, cfo]
    if len(rows) >= 2 and all(needed):
        t, p = rows[-1], rows[-2]
        try:
            def v(row, col):
                x = row_num(row, col)
                if x is None:
                    raise ValueError(col)
                return x
            dsri = (v(t, rec) / v(t, rev)) / (v(p, rec) / v(p, rev))
            gmi = (v(p, gp) / v(p, rev)) / (v(t, gp) / v(t, rev))
            aqi_t = 1 - (v(t, ca) + v(t, ppe)) / v(t, ta)
            aqi_p = 1 - (v(p, ca) + v(p, ppe)) / v(p, ta)
            aqi = aqi_t / aqi_p if aqi_p else 1.0
            sgi = v(t, rev) / v(p, rev)
            depi = (v(p, dep) / (v(p, dep) + v(p, ppe))) / (v(t, dep) / (v(t, dep) + v(t, ppe)))
            sgai = (v(t, sga) / v(t, rev)) / (v(p, sga) / v(p, rev))
            tata = (v(t, ni) - v(t, cfo)) / v(t, ta)
            lvgi = (v(t, tl) / v(t, ta)) / (v(p, tl) / v(p, ta))
            m = (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
                 + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
            detail = (f"M={m:.2f} | DSRI={dsri:.2f} GMI={gmi:.2f} AQI={aqi:.2f} SGI={sgi:.2f} "
                      f"DEPI={depi:.2f} SGAI={sgai:.2f} TATA={tata:.3f} LVGI={lvgi:.2f}")
            if m > -1.78:
                add(issues, "P1", "FORENSIC_MSCORE_FLAG",
                    f"Beneish M-Score = {m:.2f} > -1.78，落入盈余操纵可疑区，逐项手工核查。", detail, path)
            else:
                add(issues, "P3", "FORENSIC_MSCORE_INFO", f"Beneish M-Score = {m:.2f}（阈值 -1.78，未越限）。", detail, path)
        except (ValueError, ZeroDivisionError):
            add(issues, "P3", "FORENSIC_MSCORE_SKIPPED", "M-Score 所需列存在但含缺失/零值，跳过计算。", "", path)


def check_financials(path: str, issues: List[Issue]) -> None:
    rows, aliases = load_csv(path, issues)
    if not rows:
        add(issues, "P1", "FINANCIALS_EMPTY", "财务 CSV 没有数据行。", file=path)
        return

    period = pick(aliases, ["period", "fiscal_period", "date", "财期", "期间"])
    revenue = pick(aliases, ["revenue", "sales", "收入", "营收"])
    gross_profit = pick(aliases, ["gross_profit", "grossprofit", "毛利"])
    operating_income = pick(aliases, ["operating_income", "operatingincome", "ebit", "营业利润", "经营利润"])
    net_income = pick(aliases, ["net_income", "netincome", "净利润"])
    cfo = pick(aliases, ["cfo", "operating_cash_flow", "cash_from_operations", "经营现金流"])
    capex = pick(aliases, ["capex", "capital_expenditure", "capitalexpenditure", "资本开支"])
    fcf = pick(aliases, ["fcf", "free_cash_flow", "freecashflow", "自由现金流"])
    shares = pick(aliases, ["shares", "diluted_shares", "share_count", "股本", "稀释股数"])
    eps = pick(aliases, ["eps", "diluted_eps", "每股收益"])
    cash_begin = pick(aliases, ["cash_begin", "beginning_cash", "期初现金"])
    cash_end = pick(aliases, ["cash_end", "ending_cash", "期末现金"])
    cfi = pick(aliases, ["cfi", "investing_cash_flow", "投资现金流"])
    cff = pick(aliases, ["cff", "financing_cash_flow", "融资现金流"])
    gross_margin = pick(aliases, ["gross_margin", "grossmargin", "毛利率"])
    operating_margin = pick(aliases, ["operating_margin", "operatingmargin", "营业利润率", "经营利润率"])
    net_margin = pick(aliases, ["net_margin", "netmargin", "净利率"])
    fcf_margin = pick(aliases, ["fcf_margin", "fcfmargin", "自由现金流率"])
    yoy_revenue = pick(aliases, ["revenue_yoy", "yoy_revenue", "营收同比"])
    qoq_revenue = pick(aliases, ["revenue_qoq", "qoq_revenue", "营收环比"])

    if not period:
        add(issues, "P2", "FINANCIALS_NO_PERIOD_COLUMN", "财务 CSV 未发现期间列。", file=path)
    if not revenue:
        add(issues, "P1", "FINANCIALS_NO_REVENUE_COLUMN", "财务 CSV 未发现收入列，很多比率无法复核。", file=path)

    for idx, row in enumerate(rows):
        label = row.get(period, f"row {idx + 2}") if period else f"row {idx + 2}"
        check_ratio(issues, path, label, row, gross_profit, revenue, gross_margin, "GROSS_MARGIN_MISMATCH", "毛利率")
        check_ratio(issues, path, label, row, operating_income, revenue, operating_margin, "OPERATING_MARGIN_MISMATCH", "经营利润率")
        check_ratio(issues, path, label, row, net_income, revenue, net_margin, "NET_MARGIN_MISMATCH", "净利率")
        check_ratio(issues, path, label, row, fcf, revenue, fcf_margin, "FCF_MARGIN_MISMATCH", "自由现金流率")

        cfo_v = row_num(row, cfo)
        capex_v = row_num(row, capex)
        fcf_v = row_num(row, fcf)
        if cfo_v is not None and capex_v is not None and fcf_v is not None:
            expected = cfo_v - abs(capex_v) if capex_v >= 0 else cfo_v + capex_v
            if not close_enough(expected, fcf_v):
                add(issues, "P1", "FCF_RECONCILIATION_MISMATCH", f"{label} 的 FCF 与 CFO/Capex 不一致。", f"expected={expected}, provided={fcf_v}, cfo={cfo_v}, capex={capex_v}", path)

        net_income_v = row_num(row, net_income)
        shares_v = row_num(row, shares)
        eps_v = row_num(row, eps)
        if net_income_v is not None and shares_v not in (None, 0) and eps_v is not None:
            expected = net_income_v / shares_v
            if not close_enough(expected, eps_v, rel_tol=0.025, abs_tol=0.03):
                add(issues, "P2", "EPS_RECONCILIATION_MISMATCH", f"{label} 的 EPS 与净利润/股本不一致，请确认单位。", f"expected={expected}, provided={eps_v}, net_income={net_income_v}, shares={shares_v}", path)

        if cash_begin and cash_end and cfo and cfi and cff:
            cb = row_num(row, cash_begin)
            ce = row_num(row, cash_end)
            cfo_v = row_num(row, cfo)
            cfi_v = row_num(row, cfi)
            cff_v = row_num(row, cff)
            if None not in (cb, ce, cfo_v, cfi_v, cff_v):
                expected = cb + cfo_v + cfi_v + cff_v
                if not close_enough(expected, ce):
                    add(issues, "P1", "CASH_FLOW_ROLL_FORWARD_MISMATCH", f"{label} 的现金流量表勾稽不一致。", f"expected_ending_cash={expected}, provided={ce}", path)

    check_growth(issues, path, rows, period, revenue, yoy_revenue, 4, "REVENUE_YOY_MISMATCH", "营收同比")
    check_growth(issues, path, rows, period, revenue, qoq_revenue, 1, "REVENUE_QOQ_MISMATCH", "营收环比")

    check_forensics(rows, aliases, path, issues)


def sort_issues(issues: Iterable[Issue]) -> List[Issue]:
    return sorted(issues, key=lambda x: (SEVERITY_RANK.get(x.severity, 9), x.code, x.file))


def print_report(issues: List[Issue], as_json: bool = False) -> None:
    ordered = sort_issues(issues)
    if as_json:
        print(json.dumps([issue.__dict__ for issue in ordered], ensure_ascii=False, indent=2))
        return
    if not ordered:
        print("财务/估值一致性检查通过：未发现可复算异常。")
        return
    counts = {}
    for issue in ordered:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    summary = " ".join(f"{sev}={counts.get(sev, 0)}" for sev in ["P0", "P1", "P2", "P3"])
    print(f"财务/估值一致性检查发现 {len(ordered)} 项：{summary}\n")
    for issue in ordered:
        print(issue.line())


def run(args) -> int:
    issues: List[Issue] = []
    assumptions = None
    if args.assumptions:
        assumptions = check_assumptions(args.assumptions, issues, require_review=bool(args.report))
    if args.report:
        check_report(args.report, assumptions, issues, args.industry, args.language, getattr(args, "scope", "deep"))
    if args.financials:
        check_financials(args.financials, issues)
    if not any([args.report, args.assumptions, args.financials]):
        add(issues, "P1", "NO_INPUT", "请至少提供 --report、--assumptions 或 --financials 之一。")
    print_report(issues, args.json)
    fail_levels = {"P0", "P1"} if not args.strict else {"P0", "P1", "P2"}
    return 1 if any(issue.severity in fail_levels for issue in issues) else 0


def write_demo_files(tmp: str) -> Tuple[str, str, str]:
    report = os.path.join(tmp, "demo_report.md")
    assumptions = os.path.join(tmp, "demo_assumptions.json")
    financials = os.path.join(tmp, "demo_financials.csv")
    with open(report, "w", encoding="utf-8") as f:
        f.write(
            "# Demo（DEMO）个股投资研究报告\n\n"
            "截至 2026-10-05，来源 Demo（仅合成示例）。我的判断：公司合理。\n\n"
            "行业附录: saas。\n\n"
            "| NRR | RPO | Rule of 40 |\n|---:|---:|---:|\n| 110% | 100 | 35% |\n\n"
            "> 内在价值判断：合理｜未来 1–3 个月市场交易方向：中性｜投资动作：观望。\n"
            "> 市场最可能先交易收入增速和 FCF 修复；上行证伪：增长显著加速；下行证伪：FCF 继续恶化。\n\n"
            "估值由 scripts/dcf.py 运行，以 EPV / 盈利能力价值为主，恢复假设见证据复核。\n\n"
            "预测登记：收入增长 10%–15%，验证日期 2026-10-31。\n\n"
            "数据来源与时间戳：Demo 2026-07-31。未获取到：无。\n"
        )
    example_path = os.path.join(os.path.dirname(__file__), "..", "references", "research-review-example.json")
    with open(assumptions, "w", encoding="utf-8") as f:
        json.dump(load_json(example_path), f, ensure_ascii=False, indent=2)
    with open(financials, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["period", "revenue", "gross_profit", "gross_margin", "cfo", "capex", "fcf", "fcf_margin"])
        writer.writerow(["Q1", "100", "60", "60%", "30", "10", "20", "20%"])
        writer.writerow(["Q2", "110", "66", "60%", "33", "11", "22", "20%"])
    return report, assumptions, financials


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", help="Markdown 报告路径")
    ap.add_argument("--assumptions", help="估值假设 JSON 路径，建议使用 dcf.py 的 config")
    ap.add_argument("--financials", help="历史/预测财务 CSV 路径")
    ap.add_argument("--industry", action="append", help="行业规则 slug，可重复或逗号分隔；传 auto 读取报告中的行业附录声明")
    ap.add_argument("--language", choices=["auto", "zh", "en"], default="auto", help="报告语言；auto 从标准标题判定")
    ap.add_argument("--scope", choices=["direct", "focused", "deep"], default="deep", help="报告范围；只影响结构检查，不跳过已用估值及证据门槛")
    ap.add_argument("--list-industries", action="store_true", help="列出可用行业 slug 后退出")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出检查结果")
    ap.add_argument("--strict", action="store_true", help="P2 也返回非零退出码")
    ap.add_argument("--demo", action="store_true", help="运行内置示例")
    args = ap.parse_args()
    if args.list_industries:
        for slug, rule in load_industry_rules().items():
            print(f"{slug}\t{rule['name_zh']}\t{rule['appendix']}")
        return
    if args.demo:
        with tempfile.TemporaryDirectory() as tmp:
            report, assumptions, financials = write_demo_files(tmp)
            args.report, args.assumptions, args.financials = report, assumptions, financials
            sys.exit(run(args))
    sys.exit(run(args))


if __name__ == "__main__":
    main()
