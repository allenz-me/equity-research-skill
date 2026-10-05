# 个股投研报告 Skill

[![Stars](https://img.shields.io/github/stars/rollingSirius/equity-research-skill?style=flat)](https://github.com/rollingSirius/equity-research-skill/stargazers)
[![Last commit](https://img.shields.io/github/last-commit/rollingSirius/equity-research-skill)](https://github.com/rollingSirius/equity-research-skill/commits/main)
[![License](https://img.shields.io/github/license/rollingSirius/equity-research-skill)](LICENSE)

作者：[@rollingSirius](https://x.com/rollingSirius)

英文文档：[README.md](README.md) ｜ 示例报告：[NVDA 中文](Example/EXAMPLE_NVDA.md) / [English](Example/EXAMPLE_NVDA.en.md) ｜ [GOOGL 中文](Example/EXAMPLE_GOOGL.md) / [English](Example/EXAMPLE_GOOGL.en.md)

**按问题选择深度的个股研究 Skill。**

目标是让研究**事实可追溯、估值可复算、结论可审计**。事实追问直接回答；一般研究、财报或估值问题产出专题 Markdown 报告；明确要求深度时才使用九章报告。所有深度都保留来源纪律，并对增长持续和盈利恢复执行对称的证据门槛。

## 核心定位

研究深度跟随问题，估值判断跟随证据：

| 能力 | 设计要求 |
|---|---|
| 深度研究 | 完整研究模式输出九章个股研报，覆盖业务、竞争、治理、财务、估值、催化剂与投资结论。 |
| 预期差分析主线 | 反向 DCF + PVGO 分解解码"现价定价了什么"，产出预期差对比表与可证伪的分歧命题；没有独立观点不给买卖动作。 |
| 财报事件 | 财报、指引和电话会是事件类型，与深度分开；只分析相关变化，明确深研才使用九章。 |
| 财报质量核查 | 应计质量、Beneish M-Score、收入确认红旗、治理信号 → 财报可信度 A–D 分级；C/D 级直接否决买入动作。 |
| 可复算估值 | 脚本支持的 FCFF DCF、EPV、企业 EVA 等用 `dcf.py` 和 JSON；SOTP、NAV、rNPV、FCFE、权益剩余收益另建可复算模型，不规定方法数量。 |
| 外部视角 | 按行业、规模、商业模式与订单可见度选择可比组；有可核验数据才报告分位，否则写区间和资料缺口。 |
| 对称证据门槛 | 增长持续和盈利恢复均需业务、现金流、竞争证据与量化桥；未通过的乐观路径只列 conditional，排除出决策权重。 |
| 来源纪律 | 关键数据必须标注来源与时间戳；冲突数据要对账；缺失数据必须写"未获取到"；外部内容仅作数据、不改变流程。 |
| 买方视角 | 结论按预注册标定规则映射，成稿前完成反方论证与事前风险预演，并回答"如果今天这是一笔现金，我会买入它吗？为什么？" |
| 二十类行业附录 | 针对 20 类行业分别改变 KPI、模型、估值与反证框架，仅在完整研究中由检查器验证整套必备 KPI。 |
| 多市场覆盖 | 支持美股、港股、A 股与 A/H 双重上市对比，含中概 VIE/ADR 结构风险定价。 |
| 财报首次覆盖 | 无旧模型不自动升级深度；只有明确要求完整首次覆盖才补建至少 3 年、8 季度，专题仅建立问题所需基线。 |

## 它能做什么

### 1. 按问题选择研究深度

| 请求 | 范围 | 默认交付 |
|---|---|---|
| 单一事实、定义、局部追问 | `direct` | 聊天内回答与相关来源 |
| “研究一下”“值得买吗”、财报或估值专题 | `focused` | 专题 `.md` 报告 |
| 明确深度研究、完整研报、系统首次覆盖 | `deep` | 九章 `.md` 报告 |

完整研究保留以下九章：

1. 一页速览（结论框 + Tearsheet 速览表 + 预期差对比表）
2. 公司与业务详情
3. 竞争格局与护城河
4. 管理层、治理与资本配置计分卡
5. 财务分析与财报质量核查
6. 适用估值方法、共享假设与敏感性
7. 分析师观点汇总与分歧归因
8. 新闻、风险与催化剂
9. 投资结论、反方论证与仓位参考

### 2. 财报事件与研究深度

适合公司刚发布季报、年报、业绩指引或电话会纪要后，判断"这份财报到底改变了什么"。财报模式会区分两种情况：

| 覆盖状态 | skill 的处理方式 |
|---|---|
| 已有历史报告或模型 | 做持续覆盖更新，重点分析财报相对旧论点、旧预测、旧估值的变化。 |
| 没有历史报告或模型 | 说明旧基线缺失，只补本次范围所需资料；完整首次覆盖仅用于明确要求深研的任务。 |

只有明确要求完整财报深研时输出九章：

1. 结论与快照
2. 预期差与质量
3. 收入、分部与 KPI
4. 利润率、费用与盈利质量
5. 现金流、资产负债表与资本配置
6. 指引、电话会与管理层信号
7. 竞争、行业与市场反应
8. 模型、估值与公允价值变动桥
9. 投资论点更新与行动清单

完整财报研究和涉及整体盈利可信度的判断执行相关最小核查集（应计比率、现金转化、DSO/递延背离、Non-GAAP 调整项经常性）。

### 3. 估值与结论校准

根据问题和商业模式选择适用方法，不设最少数量。DCF、EPV、EVA 可能重复同一盈利假设，反向 DCF 是价格诊断，不是独立买入证据。实际使用的假设、共享驱动、计算与结论映射均留档：

- 反向 DCF + PVGO 分解：当前股价隐含了什么收入增速、利润率或资本回报；现价中有多少比例在为未来增长付费。
- 三情景 DCF：乐观、中性、悲观情景及概率加权公允价值，附概率极端化稳健性检验。
- EPV：以当前可持续盈利为基础，通过证据门槛才允许恢复；资产价值与有依据的成长价值单列，不保证形成递增买点阶梯。
- 企业 EVA：按期初投入资本计费，简化模型衰减终点为 NOPAT 与资本持平的零超额回报稳态；权益剩余收益另建补充模型。
- 相对估值：合理倍数（warranted multiple）纪律，用增长/回报/风险推算应有倍数，不直接抄同业中位数。
- SOTP：适合多业务、多资产或分部差异极大的公司。
- 蒙特卡洛（可选）：公允价值 P10–P90 分布与 P(内在价值 < 现价)。

结论标签（低估/合理/高估 + 动作）按预注册标定规则映射（±15% 缓冲带），叠加动作矩阵与否决项；需要仓位参考时只采用 decision 情景；conditional 不能支撑买入或进入仓位权重。

### 4. 财报质量核查

将利润用于估值或买入判断前，先核查其可信度：

- 应计质量（Sloan）：总应计比率、现金转化率趋势。
- Beneish M-Score 八变量模型（检查器自动计算）。
- 收入确认红旗：DSO 背离、递延收入背离、渠道压货信号。
- 费用资本化与利润平滑、治理与审计信号。
- 产出财报可信度等级 A–D：C 级动作最高"观望"，D 级一律"规避"——禁止用"估值便宜"对冲可信度问题。

### 5. 行业专用深度附录

主报告不是给所有公司套同一个模板。skill 会先识别公司所处价值链，再按需加载对应附录：

| 行业 | 专用研究重点 |
|---|---|
| SaaS | ARR、NRR、RPO/cRPO、获客效率、Rule of 40、SBC 与反向 DCF。 |
| 半导体 | 产品/终端、units 与 ASP、库存周期、良率、产能、路线图、出口限制与跨周期估值。 |
| 硬件/消费电子/AI 服务器 | units、ASP、BOM、渠道库存、客户/供应商集中与服务 attach。 |
| 银行 | NIM、存款 beta、资产质量、拨备、CET1、流动性与 P/TBV-ROTCE。 |
| 保险 | 承保利润、准备金、combined ratio、VNB/CSM、偿付能力、投资组合与 P/EV。 |
| 医药 | 临床证据、成功概率、患者漏斗、专利/独占期、现金 runway 与逐资产 rNPV。 |
| 医疗服务/器械/CRO-CDMO | 患者/手术量、利用率、报销、装机耗材、订单转化与客户集中。 |
| 消费 | 量价 mix、同店、客流、渠道 sell-through、库存、品牌份额与单位经济。 |
| 能源 | 产量、储量、递减、成本、差价/套保、维持 capex、商品价格敏感性与 NAV。 |
| 公用事业 | rate base、allowed/earned ROE、监管案件、资本项目、融资稀释与股息覆盖。 |
| 互联网/平台 | 用户×时长×变现率、GMV/take rate、单位经济、分部 SOTP、监管风险与 SBC 后 FCF。 |
| 游戏/媒体/内容 IP | 用户与付费漏斗、内容 ROI、开发资本化、生命周期收入与 IP SOTP。 |
| 支付/金融科技 | TPV、净 take rate、激励返点、损失率 vintage、资金成本与渗透天花板。 |
| 资本市场基础设施 | AUM/净流入、交易量、费率、市场数据、净资本与利率敏感性。 |
| 地产/REIT | FFO/AFFO、同店 NOI、出租率与租金差、cap rate、债务到期墙与 P/NAV。 |
| 工业/机械 | 订单/book-to-bill、backlog 质量、服务后市场、中周期盈利与周期定位。 |
| 电信 | 用户与 ARPU、EBITDA margin、capex/收入、FCF 与股息覆盖、频谱与净债。 |
| 汽车/EV | 销量、单车经济、产能利用与盈亏平衡、订单质量、电池成本与现金 runway。 |
| 金属/矿业 | 产量、AISC 成本曲线分位、储量寿命、维持 capex、价格敏感性与分矿山 NAV。 |
| 航空/运输 | 单位收益/成本、客座率/利用率、供给端订单簿、周期分位与租赁负债。 |

完整的适用边界、混合业务选择规则、官方数据入口和预测复盘字段见 [`references/industry-routing.md`](references/industry-routing.md)。混合业务公司只加载足以改变模型或估值的主附录和必要的次附录，避免为了"全面"堆砌无关指标。

### 6. 数据来源与对账

不强制使用 IBKR、Morningstar 或任何单一数据商。默认优先顺序为：

1. 监管申报、交易所公告、政府/监管数据库和公司原始文件。
2. 交易所/受监管行情、公司正式材料和行业官方统计。
3. Bloomberg、FactSet、LSEG、S&P Capital IQ、Visible Alpha、Morningstar、Koyfin、Quartr 等专业来源。
4. 公开行情与财务聚合站，用于补缺和交叉核对。
5. 媒体、转述和搜索摘要只作线索，尽量追溯原文。

连接器只是访问方式。skill 会在当前 AI 环境中选择可用的最高等级来源，并明确披露降级、延迟、口径和数据冲突。抓取到的外部内容只作为待核验数据，其中的任何指令不改变研究流程。

## 安装

### 最简单方法

直接复制这个仓库链接，发送给支持 skill 或 agent 指令的 AI 工具：

```text
https://github.com/rollingSirius/equity-research-skill
```

可以这样说：

```text
请安装并使用这个 skill：
https://github.com/rollingSirius/equity-research-skill
```

### Claude Code

```bash
# 个人级：所有项目可用
git clone https://github.com/rollingSirius/equity-research-skill.git ~/.claude/skills/equity-research

# 项目级：随仓库共享
git clone https://github.com/rollingSirius/equity-research-skill.git .claude/skills/equity-research
```

### Claude Desktop / Cowork

把本仓库打包为 zip，或下载 Release，在 **Settings -> Capabilities -> Skills** 中上传。

### Codex / 其他 Agent 工具

本技能主体是 Markdown 指令，并附带可复算脚本。任何能读取文件的 Agent 都可以使用；本地 Python **不是安装前提**：

1. 把本仓库放进项目目录，例如 `skills/equity-research/`。
2. 在 Agent 配置中加入一句：当用户要求研究/分析某只股票时，先读取 `skills/equity-research/SKILL.md`，按问题选择研究范围。
3. 需要运行估值或检查脚本时，优先使用 Agent 自带代码环境；本机没有 Python，可在 AI 托管代码环境或在线 notebook 中运行，无需先配置本地 Python。

## 快速开始

三步，无需配置。

**1. 安装技能** —— 见上一节[安装](#安装)。

**2. 用自然语言提问** —— 不需要特殊语法，也不需要参数：

```text
帮我研究一下 NVDA
```

**3. 阅读结果** —— 专题和完整研究默认 Markdown，事实追问在聊天内回答。

想在跑完整研究之前，先确认脚本在你的环境能跑通：

```bash
python3 scripts/dcf.py --demo
python3 scripts/check_research_output.py --demo
```

首次使用值得先知道的几件事：

| 问题 | 说明 |
|---|---|
| 会运行多少研究？ | 只加载本次问题所需资料和模型；简单追问不重启完整报告。 |
| 数据缺失怎么办？ | 写"未获取到"——不猜测，也不凭记忆填补。 |
| 能换输出格式吗？ | 在请求里说明即可：`.pdf`、`.docx` 或 `.xlsx`（估值 workbook）。 |
| 必须装本地 Python 吗？ | 不必。脚本可以在 Agent 自带的代码环境里运行。 |

## 使用

自然语言即可触发：

```text
帮我研究一下 NVDA
分析下 Marvell 值不值得买
深度分析一下 AAPL 最新财报
根据 MSFT 财报更新估值和投资结论
腾讯最新业绩怎么看？按财报模式做深度分析
帮我比较宁德时代 A 股和港股定价差异
按 SaaS 行业附录深度分析 Salesforce / CRM
按半导体行业附录分析台积电的周期位置和估值
按银行行业附录复盘招商银行最新财报
```

也可以显式指定技能：

| 工具 | 调用示例 |
|---|---|
| Claude Code | `/equity-research 分析 NVDA`，或"用 equity-research 技能研究 TSLA"。 |
| Claude Desktop / Cowork | "用 equity-research 技能帮我看看 AAPL 值不值得买"。 |
| Codex CLI | "先读 skills/equity-research/SKILL.md，再按它分析 NVDA"。 |
| 其他 Agent | "先读取 skills/equity-research/SKILL.md 并严格按其流程执行，然后研究 <股票>"。 |

未指明输出格式时，专题和完整报告默认以 **Markdown（.md）** 交付，直接回答留在聊天；可在请求中指明 `.pdf`、`.docx` 或 `.xlsx`（估值 workbook）。报告语言默认跟随用户请求语言或当前常用沟通语言，也可显式指定“用中文输出”或“write the report in English”。

## 适合什么场景

| 场景 | 是否适合 | 说明 |
|---|---|---|
| 首次研究一家公司 | 适合 | 默认专题，明确要求系统深研时才建立完整底稿。 |
| 财报发布后复盘 | 适合 | 用财报模式拆解预期差、质量、指引、电话会和估值变化。 |
| 投资备忘录 | 适合 | 适合形成可审计、可复盘的研究结论。 |
| 长线跟踪 | 适合 | 可以基于旧报告持续更新论点、预测和公允价值。 |
| 事实或分析追问 | 适合 | 直接核对相关证据，不启动完整报告或无依据预测股价。 |
| 高频交易信号 | 不适合 | 它不是量化交易或盘中交易系统。 |

## 输出物

**直接回答留在聊天；专题和完整研究默认交付 Markdown 报告。** 用户可以指定 PDF、DOCX 或 XLSX，语言默认跟随请求，也可明确指定。

专题报告包含结论、相关依据、适用估值、假设、反证与来源。完整研究增加九章结构、Tearsheet、预期差表、完整财报可信度核查、预测登记和反方论证。估值表只展示实际适用方法并披露共享假设，多个模型结果相近本身不提高置信度。

估值假设 JSON、`scripts/dcf.py` 原始输出、检查器结果、财务 CSV 等为**内部工作文件**：保留在工作目录供复算与追溯，不作为交付物；用户索要时才提供。

## 文件结构

```text
equity-research-skill/
├── SKILL.md                        # 技能主文件：触发条件 + 纪律 + 六步工作流程
├── references/
│   ├── report-template.md          # 九章报告模板与表格骨架
│   ├── earnings-mode.md            # 财报事件路由、专题分析与完整研究九章模板
│   ├── expectations-investing.md   # 预期差分析主线：反向 DCF、PVGO、预期差表、独立观点检验
│   ├── forensic-accounting.md      # 财报质量核查：应计、M-Score、红旗与可信度分级
│   ├── research-review.md          # 结构化证据、增长/恢复门槛、决策与条件情景
│   ├── base-rates.md               # 历史基准率：用外部视角约束预测假设
│   ├── cost-of-capital.md          # 资本成本：WACC 构建与折现率纪律
│   ├── valuation-methods.md        # 估值方法：DCF、反向 DCF、情景加权、EPV、EVA、SOTP 与结论标定
│   ├── output-format.md            # 输出格式：直接聊天，专题/完整研究默认 Markdown
│   ├── data-sources.md             # 取数手册：来源分级、工具降级、行情、申报、行业与对账
│   ├── industry-routing.md         # 20 类行业路由、官方数据入口与预测复盘协议
│   ├── industry-rules.json         # 检查器使用的行业 slug 与必备 KPI 规则
│   └── markets-cn-hk.md            # A股/港股/A+H 差异手册，含中概 VIE/ADR 结构风险
├── industries/                     # 20 类行业附录（见上表）
├── scripts/
│   ├── dcf.py                      # 估值计算器：DCF、反向 DCF、敏感性、概率加权、EPV、EVA、PVGO、蒙特卡洛、仓位
│   ├── research_review.py          # 结构化证据复核与模型参数绑定
│   └── check_research_output.py    # 财务/估值、语言与行业 KPI 检查器 + 财报质量核查
└── Example/
    ├── EXAMPLE_NVDA.md             # 英伟达示例产出，不参与技能执行
    ├── EXAMPLE_NVDA.en.md          # NVIDIA 英文示例产出，不参与技能执行
    ├── EXAMPLE_GOOGL.md            # Alphabet 示例产出（v2 完整模式），不参与技能执行
    └── EXAMPLE_GOOGL.en.md         # Alphabet 英文示例产出，不参与技能执行
```

[`Example/EXAMPLE_NVDA.md`](Example/EXAMPLE_NVDA.md) / [`Example/EXAMPLE_NVDA.en.md`](Example/EXAMPLE_NVDA.en.md) 与 [`Example/EXAMPLE_GOOGL.md`](Example/EXAMPLE_GOOGL.md) / [`Example/EXAMPLE_GOOGL.en.md`](Example/EXAMPLE_GOOGL.en.md)属于旧版方法的历史示例，保留原始事实与估值，未按当前规则重新计算，仅供了解当时的呈现方式，不会被 `SKILL.md` 自动加载。示例中使用的数据源反映当次运行环境，不代表安装或执行必须具备同一连接器。

## 依赖

| 依赖 | 必需性 | 说明 |
|---|---|---|
| 联网搜索 / 网页抓取 | 建议 | 获取最新行情、监管申报、行业数据、分析师评级和新闻；离线使用时必须由用户提供材料。 |
| 可执行 Python 环境 | 运行脚本时需要 | 可用 Agent 自带环境、AI 在线环境、在线 notebook 或本机 Python；不要求本地预装。脚本仅使用标准库。 |
| PDF 生成能力 | 可选 | 用户要求 PDF 时使用相应技能或 md→PDF 工具链，并检查渲染。 |
| IBKR / 其他行情连接器 | 可选 | 有则作为行情路径之一；没有时使用交易所、专业数据 API 或公开行情源。 |
| Morningstar / 专业数据连接器 | 可选 | 用于外部估值锚、护城河、一致预期和标准化数据；均非必需。 |
| docx / xlsx 技能 | 可选 | 仅当用户指明输出 Word 版报告或 Excel 估值 workbook。 |

## 设计取向

深度跟随问题，来源、假设、计算与证伪标准保持一致。高增长不自动压价，历史高盈利不自动恢复；企业现金流使用 WACC，权益现金流使用权益成本，真实概率情景可以与系统性风险溢价并存。检查器核查结构、引用与算术，不能代替对证据真实性和投资论点的判断。

## 免责声明

本技能产出的内容仅为研究参考，**不构成投资建议**。作者与本技能均非持牌投资顾问，投资决策及其后果由使用者自行承担。

## 许可证

[MIT](LICENSE)
