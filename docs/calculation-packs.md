# 参数化税额计算规则包

OpenTaxCheck 的参数化计算规则包用于客户内部《税务健康检查报告》，不是自动申报器，也不替代专业复核。

## 使用方式

1. 准备请求文件，例如 `request.json`。计税基础、税率和扣除额必须作为结构化事实提供。
2. 每个事实必须标记为客户确认，并带有证据引用，例如申报表、合同台账或官方政策文件。
3. 运行：

```text
opentaxcheck tax-health-check --request request.json --output-dir output --pack examples/calculation-packs/vat-cn-2023-2026.parameterized.json
```

`--pack` 可以重复传入多个规则包。程序会拒绝不存在、损坏、越界、symlink 或 Windows reparse point 规则包。

## 公式边界

- `generic.base_minus_deductions_times_rate.v1`：计税基础减扣除额后乘税率，适用于明确提供扣除额的参数化场景。
- `generic.base_times_rate.v1`：计税基础乘税率，不接受扣除额绑定。

示例规则包不会内置未经逐项核验的法定税率，也不会自动套用地方优惠政策。缺少客户确认或证据时，报告保留“待补充资料”或“待确认当地政策”，不会生成税额。

## 交付边界

机器快照保留稳定英文公式 ID，便于不同宿主 Agent（Codex、Hermes、WorkBuddy、扣子、Qoder、Claude Code、OpenClaw）复用；客户 Markdown、Word 和 PDF 报告使用中文公式说明。所有输出仍受免责声明约束，不构成涉税鉴证、专业税务意见或纳税申报结论。
