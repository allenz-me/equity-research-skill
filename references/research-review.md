# 增长持续与盈利恢复：证据复核输入 v1

给出估值或投资动作时使用本规范。篇幅不改变证据标准；单纯解释概念、读取一个指标不需要建立模型。检查器验证输入关联、可比性、桥接算术和假设使用方式，**不验证来源真实性，不替代分析者的商业判断**。

## 执行及兼容性

在原估值 JSON 中增加 `research_review`，`version` 固定为 `1`。完整可运行的格式示例见 [research-review-example.json](research-review-example.json)。示例的 `synthetic://` 来源与经营数字均为虚构测试输入，不能复制为真实公司的证据。

新增的 [DCF 示例](research-review-dcf-example.json) 同时展示收入/FCFF 利润率路径与直接 FCF 经营桥；[蒙特卡洛示例](research-review-montecarlo-example.json) 展示整份分布复核。EPV 原示例继续适用。版本仍为 v1，但旧的 v1 DCF/蒙特卡洛复核记录须按下文补充实际现金流基线、经营桥或分布记录；未补齐会报 P1。未提供 `research_review` 的旧计算配置仍按原有兼容规则处理，不能据此宣称证据已通过。

```bash
python scripts/dcf.py --config valuation.json
python scripts/check_research_output.py --report report.md --assumptions valuation.json --scope focused --language zh
```

计算器继续接受未含复核块的合法旧配置。检查器单独检查旧 JSON 时给 `RESEARCH_REVIEW_NOT_PERFORMED` P2；报告与模型一起检查时升为 P1。旧模型的算术通过不能表述为新证据门槛通过。提供复核块后，任何范围的格式或证据硬门槛错误均为 P1，不能在正文中声明豁免。

`--scope direct|focused|deep` 只控制报告结构检查。给出了估值模型，短答仍须通过同样的研究复核。

### 未内置的行业估值方法

v1 的自动参数绑定与算术检查覆盖内置 DCF、EPV、EVA、蒙特卡洛。NAV、SOTP、FCFE、权益剩余收益等补充模型使用同样的增长/恢复证据纪律，但必须人工核对输入、计算及使用位置，留存输入文件、可执行代码或公式工作簿、输出结果与证据审查记录。

报告明确标注“补充模型人工复核；其算术和参数绑定未由 research_review.py 检查”，并关联上述内部复算材料。仅使用补充模型时，运行报告及财务检查，不为通过检查而虚构内置 DCF 配置；混合使用时，`decision.model_paths` 只列内置模型，报告另列完整方法清单及每项检查覆盖范围。检查器通过不表示未覆盖部分自动通过，补充模型中未获支持的有利假设同样不能进入买入依据。

## 经营基线与可比期间

`sources` 为来源数组，每条有唯一 `id`、`locator`（链接、文件及页码等可追溯定位）和 ISO 日期 `date`。所有 `source_ids` 必须指向这些来源；同一披露可以支持多项观测，不能因重复引用就声称来源独立。

`baseline` 字段：

| 字段 | 含义 |
|---|---|
| `earnings_basis` | `NOPAT` 或 `NET_INCOME`，与所用盈利模型一致 |
| `currency`, `unit` | 估值币种和金额单位，全模型一致 |
| `annual_period`, `annual_earnings`, `source_ids` | 当前年度/TTM 可持续实际盈利、对应期间及来源；不能以预测代替实际 |
| `periods` | 最近两个不重叠的实际报告期，各与上年同期比较 |
| `cash_flow` | 决策 DCF/蒙特卡洛另需实际 FCFF 经营基线，见下文；EPV/EVA 不强制此字段 |

每个 `periods` 元素有 `period`、`start`、`end`、`prior_start`、`prior_end`、`actual: true`、`comparable`、`profit`、`prior_profit`、`source_ids`。起止日期为 `YYYY-MM-DD`；比较期应约早一年、期间长度一致，允许日历和 52/53 周差异。不要把 Q1 与累计 H1、两个滚动 TTM 或预测行当作两个独立实际报告期。

默认 `decline_threshold: 0.20`。连续两个可比同比降幅达到该阈值触发恢复审查；单期转亏或亏损扩大也触发。零/负基数不计算普通增长百分比，须填写 `comparability_note`；不可比期间也需该说明，产生趋势待审状态，不能据此宣称没有衰退。阈值变化须提供 `threshold_rationale`。

`structural_signals` 为结构性恶化记录数组，各有 `description`、`source_ids`；客户流失、份额下降、定价权恶化等可独立触发，不因调高百分比阈值而失效。

## 主张、参数绑定与证据

`claims` 每条复核一个具体数值参数：

| 字段 | 要求 |
|---|---|
| `id`, `kind` | 唯一 id；`growth_persistence` 或 `earnings_recovery` |
| `parameter` | 标准 JSON Pointer，如 `/scenarios/0/revenue`、`/epv/normalized_earnings` |
| `assumed_value`, `conservative_value` | 同口径数字或等长数字数组；前者必须与实际模型完全勾稽 |
| `use` | `decision` 或 `conditional`，必须匹配模型的 `role` |
| `status` | 分析者判断的 `supported`、`unsupported` 或 `unknown` |
| `rationale`, `counterevidence` | 支持该判断的理由与反证；未找到反证时交代查证范围 |
| `review_date`, `falsifier` | 下次复核日期及可检验的失效条件 |
| `benchmark` | 有来源的保守比较基线、参照组及适用限制 |
| `evidence` | 业务、现金流、竞争三类具体观测及各自来源 |

`benchmark` 必须包含 `description`、`limitations`、`source_ids`。增长主张另填 `industry`、`scale`、`business_model`、`stage`、`order_visibility`；匹配不足写清楚，不拼造理想样本。无可核验分布时 `percentile: null`；填写 0–100 数值时，还需 `distribution` 内的 `dataset`、正整数 `sample_size`、`period`、`metric` 和 `source_ids`。无需为了通过检查而计算分位。

`supported` 主张的 `evidence.business`、`evidence.cash_flow`、`evidence.competition` 均为非空数组；每条包含 `observation` 和 `source_ids`。只写“强”“订单”“份额”等词不满足该数据合同；分析者仍须判断具体观测是否足以支持目标数值。

比保守值更有利的假设，只有 `supported` 才能进入决策模型。盈利模型另以实际年度盈利作为恢复基线：不能把从 60 恢复到 100 的目标重命名为“保守值 100”绕过门槛。EPV 常态化盈利及 EVA 起始盈利必须使用 `earnings_recovery` 类型。用于决策的 DCF 情景必须复核收入/现金流路径及全部现金流利润率路径，即使只有一期或预测期内平坦、下降。收入用 `growth_persistence`，利润率用 `earnings_recovery`；相对实际基线改善或预测期内回升均需要支持，不能令 `conservative_value=assumed_value` 来免除。正增长衰减期起点及正永续增速也须获支持；EPV 的成长率及 ROIIC、EVA 的正盈利增长率需对应记录。蒙特卡洛按下文整体审查分布，单独审查增长均值和利润率众数不再足够。

## DCF 实际现金流基线与直接 FCF 桥接

`baseline.cash_flow` 具有 `actual: true`、`revenue`、`nopat`、`da`、`capex`、`change_nwc`、`fcff`、`source_ids`。这些是与 `annual_period`、币种和金额单位一致的实际年度/TTM 数字。`da` 是折旧摊销，`capex` 是全部资本开支（含增长投资），`change_nwc` 是经营营运资本增加额，释放时可为负数。检查：

`fcff = nopat + da - capex - change_nwc`

当 `baseline.earnings_basis=NOPAT` 时，`cash_flow.nopat` 必须等于 `annual_earnings`。若盈利基线是净利润，须另取有来源的 NOPAT 构建现金桥，不能直接把净利润当作 NOPAT。折旧摊销和资本开支用非负数。实际收入必须为正，以便计算实际 FCFF 利润率；零收入等不适用情形采用留存复算材料的补充模型，不能填一个虚构收入以通过检查。

对于 `revenue`/`fcf_margin` 情景，检查器把整个预测路径连接到实际收入和 `fcff/revenue`。实际 10% 到预测 `[40%,40%]`、`[40%]` 或 `[40%,30%]` 都是恢复；只有收入证据不能代替利润率证据。NOPAT 利润率与 FCFF 利润率分别比较，不相互替代。

直接输入 `scenarios[i].fcf` 时，在同一情景提供 `operating_bridge`，包含与显式 `fcf` 等长的 `revenue`、`nopat_margin`、`da`、`capex`、`change_nwc` 数组及 `source_ids`。逐期检查：

`fcf[t] = revenue[t] × nopat_margin[t] + da[t] - capex[t] - change_nwc[t]`

该情景须绑定 `/scenarios/i/fcf`、`/scenarios/i/operating_bridge/revenue` 和 `/scenarios/i/operating_bridge/nopat_margin` 三项主张。FCF 高于实际、隐含 FCFF 利润率改善、收入增长或 NOPAT 利润率恢复，分别需要相应支持；把 FCF 路径称为“保守”不能跳过桥接。衰减期沿用计算器的显式期终点及衰减规则，并单独复核正增长起点和终值增长。

## 蒙特卡洛完整分布复核

决策蒙特卡洛提供 `research_review.montecarlo_distribution`。该记录代替只覆盖增长均值/利润率众数的检查，字段如下：

| 字段 | 要求 |
|---|---|
| `assumed_spec`, `conservative_spec` | 实际模型与有来源保守参照的完整有效规格；包括收入基期、增长均值/标准差、利润率三角分布下限/众数/上限、WACC 区间、终值增长、预测/衰减年数、稀释、股数和净债务，以及固定分布/采样规则标识 |
| `assumed_summary`, `conservative_summary` | 程序计算的增长、利润率与 WACC 分布均值、P10、P90、上下界；必须与各自完整规格勾稽 |
| `status`, `use`, `review_date` | `supported|unsupported|unknown`、`decision`、ISO 复核日期 |
| `rationale`, `counterevidence`, `falsifier` | 分布选取理由、反证和可检验失效条件 |
| `expectation_rationale`, `tail_rationale` | 均值及尾部概率/幅度的证据依据，不把上行情景说成一定发生 |
| `benchmark` | 保守参照的 `description`、`limitations`、`source_ids`；增长参照匹配及经验分位的要求与普通主张相同，见下文 |
| `evidence` | 获支持时仍须业务、现金流和竞争三类观测及各自来源 |

`montecarlo_spec` 与计算器共用参数解析，展开顶层 WACC、终值增长等默认值。实际 `base_revenue` 必须等于实际现金流基线收入。当前增长抽样是正态尾部裁剪至 ±3σ；利润率是三角分布；WACC 是均匀分布。三者独立抽样，每条模拟的增长与利润率在显式期保持不变。这些规则也进入复核快照。`n` 与 `seed` 控制模拟精度/复现，不属于经济分布规格。

实际增长分布上界为正，或终值增长为正时，`benchmark` 仍须填写 `industry`、`scale`、`business_model`、`stage`、`order_visibility`，说明参照的适用性和匹配限制。增长分布无正上尾且终值增长非正时，不强制这些增长参照字段，也不要求为了零增长模型拼造外部样本。任何模型如填写非空 `benchmark.percentile`，仍须有含 `dataset`、正整数 `sample_size`、`period`、`metric`、`source_ids` 的可追溯 `distribution`；没有经验分布则用 `null`。这项经验排名要求与程序根据假定分布计算 `assumed_summary` 的 P10/P90 是两回事。

利润率三角分布期望是 `(low + mode + high)/3`，不能用众数代替。改变端点、增长标准差、折现区间、期限或任何已绑定输入，都需更新并重新审查整份记录。相对保守分布的有利变化、相对实际 FCFF 利润率的上行尾部或正增长尾部须有支持；即使两份规格完全相同，也不能免除实际基线检查。这里要求支持尾部出现的概率和幅度，不要求尾部必然实现。输入分布统计量不等于估值分布或实际持有期收益分布；后两者仍需另外解释。

可用以下代码生成数值快照。先依据来源准备 `conservative_model` 的参数及适用解释，再生成它的有效规格；不要为了通过检查而自动将其复制成预测值。**重生成数值只更新绑定，不代表新假设已经获得证据支持。**

```python
from dcf import montecarlo_spec                  # PYTHONPATH=scripts
from research_review import montecarlo_summary

record = cfg['research_review']['montecarlo_distribution']
record['assumed_spec'] = montecarlo_spec(cfg['montecarlo'], cfg)
record['conservative_spec'] = montecarlo_spec(conservative_model, cfg)
record['assumed_summary'] = montecarlo_summary(record['assumed_spec'])
record['conservative_summary'] = montecarlo_summary(record['conservative_spec'])
# 再人工复核 status、证据、均值/尾部依据、反证和日期；不要自动设为 supported。
```

三个示例均可分别以 `python scripts/dcf.py --config references/<示例文件名>` 复算，并以 `python scripts/check_research_output.py --assumptions references/<示例文件名> --scope focused --language zh` 检查。它们都是虚构格式样本，不能代替公司资料或真实分布证据。

在仓库根目录也支持包入口 `python -m scripts.check_research_output --assumptions references/<示例文件名> --json`，无需额外设置 `PYTHONPATH`。

## 盈利桥接与下行情景

所有 `epv` 块均须 `earnings_bridge`：

- `earnings_basis`、`current_earnings` 与 baseline 一致；`current_revenue × current_margin = current_earnings`。
- `target_revenue × target_margin + Σ adjustments.amount = epv.normalized_earnings`。利润率使用 NOPAT 或净利润对应口径，不把 EBIT 利润率直接当税后利润率。
- 收入贡献为 `(target_revenue-current_revenue)×current_margin`；利润率贡献为 `target_revenue×(target_margin-current_margin)`。报告据此解释从当前盈利到目标盈利的差额。
- `adjustments` 每条有 `kind: one_off|recurring`、`amount`、`description`、`source_ids`。不得直接把 growth capex 加回 NOPAT；折旧、维持性投资及可分配现金的转换须单独展示，不能重复扣加。
- 桥接整体提供 `source_ids`。恢复、不可比趋势或高于实际盈利的目标，还须 `maintenance_capex` 和 `cash_conversion`，各有 `value`、`explanation`、`source_ids`。这两项解释现金口径，不自动进入上述盈利加总。

触发恢复或不可比趋势审查时，`downside` 必须有 `description` 和指向可复算情景的 `model_path`。按计算器展开显式期与衰减期，完整路径至少两期且期末 FCF 低于起点；不能只修改情景名称，或在显式期下滑后通过衰减期偷偷恢复。

## 决策、条件情景及概率

`decision.action` 为 `none|buy|accumulate|hold|wait|reduce|avoid`；纯估值分析使用 `none`。`decision.model_paths` 列出支撑所给估值及动作的模型，如 `/epv` 或 `/scenarios/0`。全部模型均为 conditional 的纯条件测算，可使用空列表并将 action 设为 none/wait。不能引用反向 DCF/PVGO 作为独立估值支持。

没有决策模型时，不填写顶层 `range_low/range_high` 综合决策区间。条件价值留在各模型结果中，不能自动转换为“低估”等决策标签。

估值块/情景的 `role` 默认为 `decision`。`conditional` 表示仅用于条件分析：计算结果可以展示，但不得进入支撑买入判断的基准、加权估值或仓位计算。所有 decision 用途的 EPV/EVA/蒙特卡洛模块和非零决策概率情景都必须在复核模型列表内；决策情景 `prob` 之和为 1，条件情景的概率不参与该和。

决策情景用 `probability_rationale` 解释概率；使用证据强度标签时以 `evidence_update` 解释新信息如何改变或没有改变概率。不设置“强证据至少 25%”的自动映射。

报告中的综合区间和动作必须对应已复核模型，附方法依赖关系及关键假设敏感性。检查器不能从自然语言证明区间合成或证据因果关系，分析者必须复核这一步；不得把“脚本无 P1”写成“投资结论已被机器验证”。
