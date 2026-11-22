# 税种口径与规则包（tax packs）

本文件承接各税种的计算口径、确认要求与未覆盖情形。**解释具体税种口径、税基、税率或减免前必须先读本文件**；本文件不改变引擎计算，只说明引擎已覆盖什么、还缺什么、什么必须由复核人确认。

## 1. 硬规则

- 口径、税基、税率、减免、地方参数一律以规则包 `sources` 中的官方文号和检查期间为准；随时可在 `src/open_tax_check/tax_packs/*.json` 查到当前发布的取值与来源。
- 地方参数缺失时写“待确认当地政策”，不得沿用外省口径、行业均值或往年惯例。
- `screening` 包只回答“是否可能适用”，不产生税额；`calculation` 包才产生 `calculated`，且 `calculated` 仍需专业复核。
- `prototype` 成熟度的包（当前仅 `vat-cn-2023-2026.prototype.json`）不得用于任何客户结论或报告数字。
- 期间落在包 `effective_from`~`effective_to` 之外时不得外推，改为提示期间未覆盖。

## 2. 内置税种与规则包

21 个税种、45 个包，`jurisdiction=CN`，`rule_version` 统一 `2026.1`，`fact_catalog_version` 为 `zh-CN.v1`。每个税种都有 `screening` 包；下列“计算包”另行说明公式族。

| 税种（`tax_id`） | 计算包 | 公式族 | 地方参数键 |
| --- | --- | --- | --- |
| 增值税 `vat` | `vat-cn-2023-2025`、`vat-cn-2026` | `base_times_rate_minus_credits` | 无 |
| 消费税 `consumption_tax` | `consumption-tax-cn-2023-2026` | `base_minus_deductions_times_rate` | 无 |
| 企业所得税 `enterprise_income_tax` | `enterprise-income-tax-cn-2023-2026` | `base_times_rate` | 无 |
| 个人所得税 `individual_income_tax` | `individual-income-tax-cn-2023-2026` | `base_times_rate_minus_quick_deduction` | 无 |
| 土地增值税 `land_appreciation_tax` | `land-appreciation-tax-cn-2023-2026` | `base_times_rate_minus_confirmed_deduction` | 无 |
| 房产税 `house_property_tax` | `house-property-tax-cn-2023-2026` | `base_times_rate` | `policy.house_property_tax_local_rule` |
| 城镇土地使用税 `urban_land_use_tax` | `urban-land-use-tax-cn-2023-2026` | `quantity_times_unit_amount` | `urban_land_use_tax.applicable_unit_amount`、`policy.urban_land_use_tax_local_rule` |
| 契税 `deed_tax` | `deed-tax-cn-2023-2026` | `base_times_rate` | `deed_tax.applicable_rate`、`policy.deed_tax_local_rule` |
| 印花税 `stamp_tax` | `stamp-tax-cn-2023-08-27`（至 2023-08-27）、`stamp-tax-cn-2023-08-28-2026` | `base_times_rate` | 无 |
| 资源税 `resource_tax` | `resource-tax-cn-2023-2026` | `base_times_applicable_parameter` | `resource_tax.applicable_parameter`、`policy.resource_tax_local_rule` |
| 车辆购置税 `vehicle_purchase_tax` | `vehicle-purchase-tax-cn-2023-2026` | `base_times_rate` | 无 |
| 车船税 `vehicle_vessel_tax` | `vehicle-vessel-tax-cn-2023-2026` | `quantity_times_unit_amount` | `vehicle_vessel_tax.applicable_unit_amount`、`policy.vehicle_vessel_tax_local_rule` |
| 船舶吨税 `vessel_tonnage_tax` | `vessel-tonnage-tax-cn-2023-2026` | `quantity_times_unit_amount` | `vessel_tonnage_tax.applicable_unit_amount` |
| 城市维护建设税 `urban_maintenance_construction_tax` | `urban-maintenance-construction-tax-cn-2023-2026` | `base_times_rate` | 无 |
| 教育费附加 `education_surcharge` | `education-surcharge-cn-2023-2026` | `base_times_rate` | 无 |
| 地方教育附加 `local_education_surcharge` | `local-education-surcharge-cn-2023-2026` | `base_times_rate` | `policy.local_education_surcharge_rule` |
| 耕地占用税 `cultivated_land_occupation_tax` | `cultivated-land-occupation-tax-cn-2023-2026` | `quantity_times_unit_amount` | `cultivated_land_occupation_tax.applicable_unit_amount`、`policy.cultivated_land_occupation_tax_local_rule` |
| 环境保护税 `environmental_protection_tax` | `environmental-protection-tax-cn-2023-2026` | `quantity_times_unit_amount` | `environmental_protection_tax.applicable_unit_amount`、`policy.environmental_protection_tax_local_rule` |
| 烟叶税 `tobacco_tax` | `tobacco-tax-cn-2023-2026` | `base_times_rate` | 无 |
| 关税 `customs_duty` | `customs-duty-cn-2023-2024-11`（至 2024-11-30）、`customs-duty-cn-2024-12-2026` | `base_times_applicable_parameter` | 无 |
| 增值税期间另见 | `vat-cn-2023-2026.prototype`（原型，禁用） | —— | —— |

`generic.applicability.screening.v1` 是所有 `screening` 包的评估器；`calculation` 包的评估器是 `generic.calculation.<公式族>.v1`。

## 3. 确认要求（`requirements`）

每个包的 `requirements` 列出必须由客户或复核人给出的 `fact_key`，带 `answer_type`、`evidence_types`、`priority`、`blocking`、`critical`。执行顺序固定：

1. `blocking=true` 的事实未确认时，不得进入 `calculation`，只能在报告里写“待补充资料”。
2. `critical=true` 且证据类型为 `customer_confirmation` 时，必须由客户明确确认，宿主代客户声明不等于文件证据。
3. 事实由客户确认、证据齐全、期间匹配后，才允许 `calculation` 产出 `calculated`；`calculated` 不等于最终申报调整额。

## 4. 已知未覆盖与限制

- 企业所得税业务招待费：只做 60%/0.5% 孰低的可扣除额试算，收入基数完整性须与年度申报表和视同销售收入勾稽后才确认。
- 个人股东借款：只产生 `candidate` 与 20% 情景测算，不形成最终税负，需协议、用途、年末与期后归还证据。
- 语义类候选（费用摘要与科目错配、工资/劳务摘要异常）：永远只是候选，不构成税种适用或结论。
- 地方性政策、税收优惠资格（高新、小微等）与跨区域预缴：只有被显式确认的事实进入计算，其余保留为待确认项。

## 5. 与规则目录的分工

各税种的规则编号、公式文字与阈值、法规链接见 [rule-catalog.md](rule-catalog.md)；字段与金额口径见 [canonical-schema.md](canonical-schema.md)。本文件只负责“哪个税种由哪个包覆盖、缺什么、谁来确认”。
