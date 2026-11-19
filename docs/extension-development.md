# 管理端扩展开发

管理端扩展由页面模块和可选的后端 Python 模块组成。扩展属于受信任的本地代码，与主程序运行在同一进程和同一文件系统权限下。每个扩展使用根目录下的独立包：

```text
extensions/<id>/
├── extension.py                  # 可选：Python 后端入口
├── helpers.py                    # 可选：后端辅助模块
├── frontend/
│   ├── index.ts                  # 可选：页面描述
│   └── RequestToolsPage.vue
└── README.md
```

`<id>` 使用小写字母开头，可包含小写字母、数字、下划线和连字符，最长 64 个字符。目录名即扩展 ID。`frontend/index.ts` 和 `extension.py` 均为可选；两者都存在时构成完整全栈扩展。

## 1. 页面扩展

页面模块位于扩展包的 `frontend/` 目录：

```text
extensions/request-tools/
├── frontend/
│   ├── index.ts
│   └── RequestToolsPage.vue
└── extension.py
```

`frontend/index.ts` 默认导出一个扩展页面定义：

```ts
import { defineExtensionPage } from '@/app/page-contract'

export default defineExtensionPage({
  id: 'request-tools',
  title: '请求工具',
  navigation: {
    label: '请求工具',
    icon: 'terminal',
    order: 650,
  },
  component: () => import('./RequestToolsPage.vue'),
})
```

页面声明支持以下字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 扩展 ID，必须与目录名一致 |
| `title` | 浏览器标题和扩展页面标题 |
| `navigation` | 侧边栏入口；省略时页面不显示在侧边栏 |
| `navigation.label` | 侧边栏文字 |
| `navigation.icon` | 共享 `UiIcon` 的图标名 |
| `navigation.order` | 排序值，同值时按扩展 ID 排序 |
| `visible(context)` | 可选；按 `/api/ui/bootstrap` 上下文控制入口显示 |
| `component` | 返回 `{ default: Vue 组件 }` 的异步加载函数 |

宿主自动生成路由：

```text
/extensions/<id>
```

扩展页面使用现有 `AppShell`、登录校验、主题、`@/components/ui` 和 `@/api/client`。页面草稿、分页和请求取消由扩展组件管理；跨组件状态可以定义独立 Pinia Store。

`component` 可以是：

```ts
component: () => import('./RequestToolsPage.vue')
```

动态导入结果本身包含 `default`，可以直接作为组件加载器。页面切换时宿主按扩展 ID 重建组件实例；侧栏收起和窗口宽度变化保留当前页面实例。

新增或修改页面模块后执行：

```text
npm --prefix frontend run build
```

构建完成后刷新浏览器即可使用新页面，不需要重启 Python 服务。开发模式由 Vite 自动发现页面目录。

页面加载失败时，扩展宿主显示错误与重试按钮，`AppShell` 和其他页面继续工作。重复 ID、重复路径、扩展路径与核心路径冲突会在启动阶段报错。

## 2. Python 后端扩展

需要执行 Python 动作时，在扩展包根目录放置 `extension.py`：

```text
extensions/request-tools/
├── extension.py
├── helpers.py
└── frontend/
    └── index.ts
```

扩展入口作为独立 Python 包加载，同包辅助模块使用相对导入：

```python
from .helpers import normalize_value
```

模块导出 `Extension` 类：

```python
class Extension:
    """请求工具后端动作。"""

    def __init__(self, context):
        self._context = context

    def register_routes(self, blueprint):
        @blueprint.post("/actions/refresh")
        def refresh():
            return {"ok": True, "extension": self._context.extension_id}

    def start(self):
        """启动后台任务；该方法可选。"""

    def close(self):
        """停止后台任务并释放资源；该方法可选。"""
```

宿主自动挂载 Blueprint：

```text
/api/extensions/<id>/
```

上例动作地址为：

```text
POST /api/extensions/request-tools/actions/refresh
```

Blueprint 的所有请求继承管理端登录校验。认证关闭时，接口不要求登录会话，与现有管理端控制器一致。扩展路由统一位于控制平面 `/api/extensions/<id>/` 命名空间，不进入 `/v1/*` 数据平面。

### 扩展上下文

`Extension` 构造函数接收 `AdminExtensionContext`：

| 字段 | 说明 |
| --- | --- |
| `extension_id` | 当前扩展 ID |
| `logger` | 应用 logger |
| `config_manager` | 当前配置管理器 |
| `root_path` | 项目根目录 |
| `extension_path` | 当前后端扩展目录 |
| `database` | 共享 SQLite 连接工厂 |
| `services` | 只读业务服务映射 |

`services` 当前包含：

- `authentication`
- `model_catalog`
- `users`
- `api_keys`
- `providers`
- `settings`
- `logs`
- `provider_manager`

扩展使用自身数据表时应在表名中带扩展 ID，并由扩展管理建表和迁移。扩展接口返回 JSON，错误响应的标准格式为：

```json
{"error": "Extension action failed"}
```

扩展路由抛出未处理异常时，宿主记录英文错误日志并返回上述 `500` 响应，其他扩展和主程序继续运行。

### 生命周期

| 阶段 | 行为 |
| --- | --- |
| 服务启动 | 扫描 `extensions/*/extension.py`，按独立包导入并实例化 `Extension` |
| 路由装配 | 调用可选 `register_routes(blueprint)`，随后挂载 `/api/extensions/<id>/` |
| 后台启动 | 调用可选 `start()` |
| 服务关闭 | 按加载逆序调用可选 `close()` |

Python 文件在服务启动时导入。修改后端扩展后需要重启服务。Blueprint 在主 Flask 应用上注册一次，`record` 和 `record_once` 回调的 `state.app` 指向主应用。注册失败时，宿主恢复路由、视图、Blueprint、请求回调、错误处理器、模板注册项及 CLI 命令；失败扩展的路由返回 404。单个扩展导入、实例化或注册失败时，该扩展不加载，其扩展包和辅助模块从 `sys.modules` 清理，错误写入应用日志，其他扩展继续装配。

注册回调仅用于 Flask 注册配置。应用配置和业务状态修改、文件写入、数据库事务及其他外部副作用不属于宿主的回滚范围；后台任务在 `start()` 中启动，扩展已分配资源在 `close()` 中释放。

### 长任务

请求内只执行短动作。耗时任务由扩展保存任务状态并立即返回任务 ID，通过独立状态接口查询：

```python
from gevent import spawn


class Extension:
    def __init__(self, context):
        self._context = context
        self._jobs = {}

    def register_routes(self, blueprint):
        @blueprint.post("/actions/start")
        def start():
            job_id = "refresh-1"
            self._jobs[job_id] = {"status": "running"}

            def run():
                # 在扩展内执行耗时动作。
                self._jobs[job_id]["status"] = "done"

            spawn(run)
            return {"job_id": job_id}

        @blueprint.get("/jobs/<job_id>")
        def status(job_id):
            return self._jobs.get(job_id, {"status": "missing"})
```

扩展后台 greenlet 在 `close()` 中停止。共享单进程内的内存状态不适合多实例部署。

## 3. 页面调用后端动作

扩展页面通过现有 API 客户端调用动作：

```ts
import { api } from '@/api/client'

export async function refreshTools() {
  return api<{ ok: boolean }>('/api/extensions/request-tools/actions/refresh', {
    method: 'POST',
    body: {},
  })
}
```

API 客户端只接受同源 `/api/*` 路径，并集中处理 Session 失效、错误消息和取消信号。

## 4. 安全边界

- 后端扩展属于受信任代码，拥有配置、数据库、文件系统和网络访问权限。
- 管理页面不提供上传并执行 Python 代码的入口；扩展文件由管理员直接放入服务器目录。
- 动作接口使用固定服务端处理函数。客户端只提交动作参数，不提交脚本路径、任意命令或可执行代码。
- 外部程序参数使用服务端固定值或显式白名单校验，不接受未经校验的客户端命令字符串。
- 扩展代码影响整个管理服务。需要不可信代码隔离时，应使用独立进程或容器，不适用当前进程内扩展模型。
- 扩展页面和接口属于管理控制平面，不修改 `/v1/*` 代理链路。

## 5. 构建与检查

页面扩展和后端扩展均位于同一个 `extensions/<id>/` 包内。页面扩展参与前端类型检查、Lint 和生产构建。样式检查覆盖 `frontend/` 顶层及子目录中的 Vue、CSS 文件，忽略 `dist/` 和 `node_modules/`：

```text
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

应用测试覆盖扩展发现、页面宿主、路由、动作认证和生命周期：

```text
npm --prefix frontend run test:unit
uv run python -m unittest discover -s tests
```

扩展自有测试随扩展包维护，使用项目已有的 Vitest 或 unittest 工具执行。

首轮安装或依赖变化后执行：

```text
npm --prefix frontend ci
uv sync
```

页面扩展发现路径和宿主机理见 [前端开发指南](frontend-development.md)，模块职责见 [4+1 架构](architecture-4plus1.md)。
