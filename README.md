# LLM_Proxy

基于 Flask 的 OpenAI 兼容代理服务，聚焦模型转发、访问控制与请求统计。

## 功能特性
- OpenAI 兼容接口：支持 `POST /v1/chat/completions` 和 `GET /v1/models`
- 多 Provider / 多模型路由：按 `model` 自动选择上游 provider
- Hook 扩展：单一 `hook` 配置，模块中通过 `Hook(BaseHook)` 按需实现阶段方法
- 访问白名单控制：可按客户端 IP 控制是否允许调用代理接口
- 管理后台与认证：支持登录、用户管理、统计查询页面与 API
- 流式与非流式统一处理：支持 SSE 转发并提取 usage 信息

## 使用方式

### 1. 安装依赖

```bash
pip install flask gevent requests pyyaml urllib3
```

### 2. 配置服务

编辑 `config.yaml`，至少确认以下字段：

- `server.host` / `server.port`
- `chat.whitelist_enabled`
- `providers[].name`
- `providers[].api`
- `providers[].api_key`
- `providers[].model_list`

可选字段：

- `admin.username` / `admin.password`
- `providers[].hook`
- `logging.path` / `logging.level`
- `database.path`

### 3. 启动服务

```bash
python main.py
```

或指定配置文件：

```bash
python main.py --config path/to/config.yaml
```

### 4. 调用代理接口

获取模型列表：

```bash
curl http://127.0.0.1:22026/v1/models
```

发起聊天请求：

```bash
curl http://127.0.0.1:22026/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "your-model-name",
    "messages": [{"role": "user", "content": "hello"}],
    "stream": false
  }'
```

## Hook API（类接口）

Provider 配置使用单一 `hook` 字段。

```yaml
providers:
  - name: your-provider
    api: https://example.com/v1/chat/completions
    api_key: your-key
    model_list:
      - your-model
    hook: example_hook.py
```

Hook 模块必须导出一个名为 `Hook` 的类，通常继承 `BaseHook`：

```python
from src.hooks import BaseHook, HookContext

class Hook(BaseHook):
    def header_hook(self, ctx: HookContext, headers: dict[str, str]) -> dict[str, str]:
        return headers
```

可选实现的方法：
- `header_hook(ctx, headers) -> headers`
- `input_body_hook(ctx, body) -> body`
- `output_body_hook(ctx, body) -> body`
