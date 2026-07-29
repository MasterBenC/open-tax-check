# 税额计算规则包

OpenTaxCheck 的计算规则包用于客户内部《税务健康检查报告》，不是自动申报器，也不替代客户对事实和资料完整性的确认。

## 当前正式覆盖

- 全国 20 个税费项目均已内置 2023–2026 年适用性筛查包。
- 印花税已内置 2023–2026 年法定税目税率表，并按 2023-08-28 证券交易印花税税率调整拆成两个不可变期间包。客户确认存在应税事项后，系统继续询问法定税目和本条计税依据。
- 税目只能由客户按合同、产权转移书据、营业账簿或证券交易事实选择；AI 不得自动分类。未知税目不会使用默认税率。
- 跨税率变更期间且所选税目税率发生变化时，必须拆分期间和计税依据后分别计算。
- 契税适用税率由省级规定，当前仍须作为当地政策参数确认，程序不得从 3%–5% 法定幅度中自行选择。
- 其他税种目前仍以筛查包或参数化模板为主，不能宣称已经具备完整法定税额计算。

默认命令会自动加载内置筛查包和正式印花税计算包：

```text
opentaxcheck tax-health-check --request request.json --output-dir output
```

所有可计算事实必须标记为客户确认，并绑定合同、书据、账簿、申报表或官方政策等证据引用。一个计算记录只代表一个已确认税目或汇总行，不代表整个申报期间的全部应纳税额。

## 外部参数化规则包

自有规则包可以通过可重复的 `--pack PATH` 显式传入：

```text
opentaxcheck tax-health-check --request request.json --output-dir output --pack examples/calculation-packs/vat-cn-2023-2026.parameterized.json
```

程序会拒绝不存在、损坏、越界、symlink 或 Windows reparse point 规则包。`--include-parameterized-packs` 可一次启用全部 20 个税费项目的参数化模板；这些模板只绑定计算事实和官方来源，不提供统一法定税率，也不会覆盖内置正式规则。

## 公式与模式边界

- schema `1.2.0`：税率作为客户确认并绑定证据的事实传入。
- schema `1.3.0`：规则包可以提供经核验的法定税率表，由客户确认的精确 selector 选择税率。
- `generic.base_minus_deductions_times_rate.v1`：计税基础减扣除额后乘税率。
- `generic.base_times_rate.v1`：计税基础乘税率，不接受扣除额绑定。

缺少客户确认、证据、当地政策或期间拆分时，报告保留“待补充资料”“待确认当地政策”或“暂时无法判断”，不会猜测并生成税额。

## 交付边界

机器快照保留稳定英文公式 ID 和税目 selector，供 Codex、Hermes、WorkBuddy、扣子、Qoder、Claude Code、OpenClaw 等宿主审计复用；客户 Markdown、Word 和 PDF 报告显示中文税目和百分比税率。所有输出仍受免责声明约束，不构成涉税鉴证、专业税务意见或纳税申报结论。
