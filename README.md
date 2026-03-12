# LLM_Proxy

一个基于 Flask + gevent 的 OpenAI 兼容代理服务，重点不只是“填 API Key 转发”，而是通过 Hook 机制把不同上游协议、鉴权方式和请求格式收敛成统一的 OpenAI 风格接口。

## 项目亮点

- OpenAI 兼容入口
  - 提供 `POST /v1/chat/completions` 和 `GET /v1/models`
- 多 Provider / 多模型路由
  - 通过 `provider/model` 形式精确路由到不同上游
- Hook 扩展机制
  - 可在请求头、请求体、响应体三个阶段做定制化改写
- 非标准上游适配
  - 适合处理 Claude / Anthropic 一类需要请求格式转换的场景
  - 也适合处理经过授权的会话型上游集成，例如额外 Cookie、session token、自定义 header、特定 body 字段
- 管理后台
  - 支持登录、用户管理、Provider 管理、模型探测、白名单控制
- 请求统计
  - 自动记录调用明细和每日聚合 Token 统计

## 这个项目解决什么问题

大多数模型服务只需要配置 `api` 和 `api_key` 就能接入，但真实集成里经常会遇到这几类问题：

- 上游接口不是 OpenAI 格式，需要字段转换
- 上游要求额外请求头、Cookie、session token 或特殊参数
- 不同厂商响应结构不同，需要统一成 OpenAI 风格
- 某些模型接入只在特定客户端或内部工具里可用，需要做受控的协议适配

LLM_Proxy 的核心价值就在这里：  
把“代理转发”升级成“可编排、可适配、可管理的统一入口”。

## 合规说明

Hook 适配能力的设计目标是支持合法授权前提下的协议兼容和私有集成。

- 仅应在你拥有访问权限的前提下使用额外的 Cookie、token、header 或会话凭据
- 应遵守上游服务的产品条款、访问策略和安全要求
- README 和示例不会提供抓包、绕过限制或规避授权的操作步骤

## 核心能力概览

### 1. 标准 Provider 接入

对标准 OpenAI 风格上游，通常只需要配置：

- `name`
- `api`
- `api_key`
- `model_list`

这种情况下不需要写 Hook。

### 2. Hook 方式做协议适配

对非标准上游，可以通过 Hook 处理：

- `header_hook`
  - 注入额外 header、Cookie、鉴权字段
- `input_body_hook`
  - 把 OpenAI 风格请求体转换成上游需要的格式
- `output_body_hook`
  - 把上游响应转换回统一的 OpenAI 风格

这也是项目当前最有区分度的能力。

### 3. 管理与运维能力

- 后台登录
- 用户白名单
- Provider 配置增删改查
- Provider 模型列表探测
- 请求日志与聚合统计

## 架构文档

完整的 Mermaid 版 4+1 架构视图见：

- [docs/architecture-4plus1.md](/d:/001Code/008llm/003LLM_Proxy/docs/architecture-4plus1.md)

其中包含：

- 逻辑视图
- 开发视图
- 进程视图
- 物理视图
- 关键场景时序图

## 快速开始

### 1. 安装依赖

```bash
pip install flask gevent requests pyyaml urllib3
```

### 2. 准备配置文件

复制并编辑 `config.yaml`，至少确认这些字段：

- `server.host`
- `server.port`
- `chat.whitelist_enabled`
- `providers[].name`
- `providers[].api`
- `providers[].model_list`

可选字段：

- `providers[].api_key`
- `providers[].proxy`
- `providers[].timeout_seconds`
- `providers[].max_retries`
- `providers[].verify_ssl`
- `providers[].hook`
- `admin.username`
- `admin.password`
- `database.path`
- `logging.path`
- `logging.level`

参考样例见：

- [config.sample.yaml](/d:/001Code/008llm/003LLM_Proxy/config.sample.yaml)

### 3. 启动服务

```bash
python main.py
```

或指定配置文件：

```bash
python main.py --config path/to/config.yaml
```

### 4. 调用接口

获取模型列表：

```bash
curl http://127.0.0.1:8080/v1/models
```

发起聊天请求：

```bash
curl http://127.0.0.1:8080/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"volc/glm-4-7-251222\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"stream\":false}"
```

## 配置示例

### 标准 OpenAI 风格 Provider

```yaml
providers:
  - name: openai
    api: https://api.openai.com/v1/chat/completions
    api_key: ${OPENAI_API_KEY}
    model_list:
      - gpt-4.1
      - gpt-4o-mini
```

### 带 Hook 的 Provider

```yaml
providers:
  - name: anthropic
    api: https://example.com/v1/chat/completions
    api_key:
    model_list:
      - claude-sonnet
    hook: anthropic_compat.py
```

这类 Hook 常见用途：

- 把 OpenAI 风格 `messages` 转成上游要求的结构
- 注入额外鉴权 header / Cookie
- 修正响应字段，统一 `choices`、`usage`、`model`

## Hook API

Hook 模块需要导出一个名为 `Hook` 的类，通常继承：

```python
from src.hooks import BaseHook, HookContext
```

可选实现的方法：

- `header_hook(ctx, headers) -> headers`
- `input_body_hook(ctx, body) -> body`
- `output_body_hook(ctx, body) -> body`

示例：

```python
from typing import Any

from src.hooks import BaseHook, HookContext


class Hook(BaseHook):
    """Anthropic / 私有网关适配示例骨架。"""

    def header_hook(self, ctx: HookContext, headers: dict[str, str]) -> dict[str, str]:
        headers["X-Client"] = "llm-proxy"
        return headers

    def input_body_hook(self, ctx: HookContext, body: dict[str, Any]) -> dict[str, Any]:
        return body

    def output_body_hook(self, ctx: HookContext, body: Any) -> Any:
        return body
```

现成示例见：

- [hooks/example_hook.py](/d:/001Code/008llm/003LLM_Proxy/hooks/example_hook.py)

## Hook 适配的典型场景

### Claude / Anthropic 格式转换

某些上游并不直接接受 OpenAI 风格的：

- `messages`
- `tools`
- `stream` / `usage`

这时可以在 Hook 中做双向转换：

- 请求进入代理时，从 OpenAI 格式改写成上游格式
- 响应返回客户端前，再还原成统一的 OpenAI 风格

### 会话型或私有上游集成

某些经过授权的私有集成可能要求：

- 额外 Cookie
- session token
- 自定义 header
- 特定 body 标记

这时可以通过 `header_hook` 和 `input_body_hook` 注入这些字段，实现受控反代接入。

### 响应清洗或增强

有些上游：

- 不返回标准 `usage`
- 返回字段名不一致
- 流式块格式不完全兼容

可以在 `output_body_hook` 中统一处理。

## 后台能力

后台当前提供：

- 登录认证
- 用户管理
- 白名单开关
- Provider 管理
- 模型探测
- 请求日志
- 聚合统计

主要页面：

- `/`
- `/users`
- `/providers`

## 数据存储

默认使用 SQLite：

- 数据库文件：`data/requests.db`
- 配置文件：`config.yaml`
- 日志目录：`logs/`
- Hook 目录：`hooks/`

主要表：

- `users`
- `request_logs`
- `daily_request_stats`

## 当前架构判断

这个项目当前最值得保留的设计不是“又一个 OpenAI 代理”，而是：

**把 Provider 转发、协议适配、会话注入和后台管理放进了同一个可控边界里。**

也就是说：

- 标准 Provider 可以零适配接入
- 非标准 Provider 可以最小成本做 Hook 兼容
- 业务方看到的仍然是统一的 OpenAI 接口

## 后续建议

如果后续继续演进，优先建议：

- 增加更多官方示例 Hook
  - 例如 Claude 兼容骨架
  - 例如 session header 注入骨架
- 把 Provider 配置 schema 进一步显式化
- 增加自动化测试覆盖 Hook 输入输出转换
- 增加 README 中的真实接入案例文档
