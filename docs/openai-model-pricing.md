# OpenAI 模型目录与费用快照

核对日期：2026-11-09。数据文件为 `src/config/openai_model_catalog.json`，单价单位为美元 / Token。价格在服务启动时加载，更新后重启 Python 服务生效。架构未变化。

## 内置目录

文本内置目录包含 GPT-6 Astra、GPT-6.1 Sol、GPT-6 Luna、GPT-5.6 Terra 和 GPT-5.6 Luna。GPT-5.6 Sol 的明确 ID 仍有价格记录，可用于自定义目录和后续请求统计。

图片内置目录包含 GPT Image 2.6 Sunburst、2.6 Flare、2.5 Sunburst、2.5 Flare 和 Image 2。默认图片模型仍为 `gpt-image-2.5-sunburst`。官方 API 模型存在不代表所有 Codex OAuth 账号均有访问权限；上游授权结果决定实际可用性。

已有 `models.json`、`image_models.json` 和 `image_settings.json` 保留用户设置。图片目录中的“恢复内置”会补齐新的内置 ID，同时保留自定义模型与有效的默认值。用户已保存的 GPT-5.6 Sol 可在目录中手动删除。

## 价格核对

- 官方 [API 价格表](https://developers.openai.com/api/docs/pricing/) 是标准价、Fast 价、缓存读取及长上下文价格的来源。
- GPT-6 Astra、GPT-6.1 Sol / Terra、GPT-6 Sol / Luna、GPT-5.6 Sol / Terra / Luna、GPT-5.5 / Pro、GPT-5.4 / Pro 和 GPT-5.3 Codex 的既有标准与 Fast 价格与本次官方快照一致。
- 表内覆盖 GPT-6 Cyber、GPT-6 Rosalind、GPT-6 Astra Law、Rosalind Research / Discovery，以及 GPT-5.4 Mini / Nano、GPT-5.2 / Pro、GPT-5.1、GPT-5 / Mini / Nano / Pro、GPT-4.1 / Mini、GPT-4o / Mini 的公开价格；Fast 只记录官方公布的模型。
- GPT-5.5 Cyber 的输入、缓存读取和输出单价仍为每百万 Token 12.50、1.25、75 美元；官方没有公布缓存写入价，出现缓存写入 Token 时费用为未知。
- `gpt-5.6` 是官方确认的 [GPT-5.6 Sol 别名](https://developers.openai.com/api/docs/models/gpt-5.6-sol)。当前促销价为每百万输入 4 美元、输出 20 美元，官方标注截至 2026-11-30。后续价格以新的官方快照为准。
- `gpt-daybreak-blue-latest`、`gpt-daybreak-red-latest` 缺少可核实的 API 美元单价，费用为未知。Codex 信用点费率不直接作为这两个模型 ID 的 API 单价。
- GPT Image 2.6 Sunburst / Flare 与 2.5、Image 2 单价一致：每百万文本输入 5 美元、缓存文本输入 1.25 美元、图片输入 8 美元、缓存图片输入 2 美元、图片输出 30 美元。来源：[图像生成文档](https://developers.openai.com/api/docs/guides/image-generation)。
- Image 1.5 既有价格与官方表一致；已停用的 Image 1 记录保留为历史价格，不属于内置目录。

## 估算边界

此表用于 API 美元等价费用估算，与 [Codex 订阅额度及信用点计费](https://developers.openai.com/codex/pricing) 是不同口径。历史请求保存的费用不随价格快照变化重新计算。

文本模型支持 Standard 和 Fast / Priority；其他服务档位返回未知。长上下文按输入超过 272,000 Token 判断，官方未公布长上下文价格的专用模型不推算。模型按完整 ID 或日期快照后缀匹配，不按相似名称套用价格。

缓存写入字段对应公开的 30 分钟写入单价；当前 usage 归一化未区分 12 小时写入，12 小时写入费用不属于准确估算范围。地区处理附加费、工具调用费和订阅信用点不包含在本表中。

图片计费要求完整的文本 / 图片 Token 明细；混合模态缓存无法拆分时返回未知。GPT Image 2 / 2.5 的缓存优惠仅适用于 Responses 图像工具，直接 Images API 不适用；响应未提供的缓存命中不能推测为已知优惠。

维护时同步更新 `pricing.snapshot_date`，逐项核对输入、输出、缓存与长上下文单价，并运行 `uv run python -m unittest tests.test_openai_model_pricing tests.test_codex_oauth_service tests.test_codex_auth_usage`。
