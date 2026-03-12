# LLM_Proxy 4+1 Architecture View

本文档描述当前代码实现对应的 4+1 架构视图，作为后续重构、评审和需求变更时的基线。

维护约束：
- 涉及模块职责、依赖关系、主请求链路、运行时状态、配置加载/重载机制、Hook 机制、部署拓扑的修改时，需要同步更新本文件。
- 如果代码实现与本图不一致，以代码为准，但提交中应补齐文档更新。

## 0. 架构摘要

系统当前是一个单进程、分层式单体应用，按职责分成两条主轴：
- 控制平面：后台管理、认证、用户、Provider 配置、系统设置。
- 数据平面：OpenAI 兼容代理、模型路由、上游请求转发、响应适配、日志落库。

关键代码位置：
- 启动与装配：[main.py](/d:/001Code/008llm/003LLM_Proxy/main.py)
- 组合根：[application.py](/d:/001Code/008llm/003LLM_Proxy/src/application/application.py)
- 配置与 Provider 运行时：[config_manager.py](/d:/001Code/008llm/003LLM_Proxy/src/config/config_manager.py) [provider_manager.py](/d:/001Code/008llm/003LLM_Proxy/src/config/provider_manager.py)
- 代理主链路：[proxy_controller.py](/d:/001Code/008llm/003LLM_Proxy/src/presentation/proxy_controller.py) [proxy_service.py](/d:/001Code/008llm/003LLM_Proxy/src/services/proxy_service.py)

## 1. Logical View

```mermaid
flowchart LR
    Browser[浏览器管理端]
    ApiClient[OpenAI 兼容 API Client]

    subgraph App["LLM_Proxy 应用"]
        subgraph Presentation["Presentation"]
            WebCtl[WebController]
            AuthCtl[AuthenticationController]
            UserCtl[UserController]
            ProviderCtl[ProviderController]
            ProxyCtl[ProxyController]
        end

        subgraph Services["Services"]
            AuthSvc[AuthenticationService]
            UserSvc[UserService]
            ProviderSvc[ProviderService]
            DiscoverySvc[ModelDiscoveryService]
            SettingsSvc[SettingsService]
            ProxySvc[ProxyService]
            LogSvc[LogService]
        end

        subgraph ConfigRuntime["Config / Provider Runtime"]
            ConfigMgr[ConfigManager]
            ProviderCfg[provider_config]
            ProviderMgr[ProviderManager]
            LLMProvider[LLMProvider]
        end

        subgraph Persistence["Persistence"]
            UserRepo[UserRepository]
            LogRepo[LogRepository]
            SQLite[(SQLite)]
        end

        subgraph Integration["External Integration"]
            Hooks[Hook Modules]
            Adapter[response_adapter]
            Probe[stream_probe]
            Upstream[Upstream LLM Providers]
        end
    end

    Browser --> WebCtl
    Browser --> AuthCtl
    Browser --> UserCtl
    Browser --> ProviderCtl
    ApiClient --> ProxyCtl

    AuthCtl --> AuthSvc
    UserCtl --> UserSvc
    ProviderCtl --> ProviderSvc
    ProviderCtl --> DiscoverySvc
    ProviderCtl --> SettingsSvc
    ProxyCtl --> ProxySvc
    ProxyCtl --> UserSvc
    ProxyCtl --> ProviderMgr
    ProxyCtl --> LogSvc
    WebCtl --> ConfigMgr

    AuthSvc --> ConfigMgr
    ProviderSvc --> ConfigMgr
    ProviderSvc --> ProviderCfg
    DiscoverySvc --> Upstream
    SettingsSvc --> ConfigMgr
    ProxySvc --> LLMProvider
    ProxySvc --> Adapter
    ProxySvc --> Probe
    ProviderMgr --> ProviderCfg
    ProviderMgr --> LLMProvider
    ProviderMgr --> Hooks
    Adapter --> Hooks

    UserSvc --> UserRepo
    LogSvc --> LogRepo
    UserRepo --> SQLite
    LogRepo --> SQLite
    ConfigMgr --> ConfigFile[(config.yaml)]
```

逻辑划分：
- `Presentation` 负责 HTTP 路由、鉴权入口、页面渲染和请求/响应封装。
- `Services` 负责用例编排，不直接持有全局可变配置。
- `Config / Provider Runtime` 负责 YAML 配置快照、Provider 规范化、模型到 Provider 的运行时映射。
- `Persistence` 负责 SQLite 读写。
- `External Integration` 负责上游协议适配、流式探测、Hook 扩展和上游 Provider 集成。

## 2. Development View

```mermaid
flowchart TB
    Main[main.py]

    subgraph Src["src/"]
        Application["application/"]
        Presentation["presentation/"]
        Services["services/"]
        Repositories["repositories/"]
        Config["config/"]
        External["external/"]
        Hooks["hooks/"]
        Utils["utils/"]
    end

    Main --> Application
    Main --> Utils

    Application --> Presentation
    Application --> Services
    Application --> Repositories
    Application --> Config
    Application --> Utils

    Presentation --> Application
    Presentation --> Services
    Presentation --> Config

    Services --> Application
    Services --> Repositories
    Services --> Config
    Services --> External
    Services --> Utils

    Config --> Application
    Config --> External
    Config --> Utils

    Repositories --> Utils

    External --> Hooks
```

开发视图解读：
- `application` 是组合根，不承载具体业务规则。
- `presentation -> services` 是主要调用方向。
- `services -> repositories/config/external` 是业务侧依赖方向。
- `config` 既不是传统 repository，也不是 service，更像运行时配置与 Provider 注册子系统。

## 3. Process View

```mermaid
flowchart TB
    subgraph Process["单个 Python 进程"]
        WSGI[gevent WSGIServer]
        FlaskApp[Flask App]

        subgraph RuntimeState["进程内运行时状态"]
            SessionState[AuthenticationService\n内存 Session]
            UserCache[UserService\nIP -> User 缓存]
            ConfigCache[ConfigManager\n配置快照缓存]
            ProviderRegistry[ProviderManager\nmodel -> provider 映射\nhook cache]
            HttpLocal[ProxyService\nthread-local requests.Session]
        end
    end

    subgraph Local["本地资源"]
        ConfigFile[(config.yaml)]
        DB[(data/requests.db)]
        HooksDir[(hooks/*.py)]
        LogFiles[(logs/*.log)]
    end

    subgraph Remote["外部网络资源"]
        Upstream[Upstream LLM Providers]
    end

    WSGI --> FlaskApp
    FlaskApp --> SessionState
    FlaskApp --> UserCache
    FlaskApp --> ConfigCache
    FlaskApp --> ProviderRegistry
    FlaskApp --> HttpLocal

    ConfigCache <--> ConfigFile
    UserCache <--> DB
    ProviderRegistry <--> HooksDir
    FlaskApp --> LogFiles
    HttpLocal <--> Upstream
```

进程视图要点：
- 当前是单进程单实例内存态设计。
- `AuthenticationService` 的 Session 存在进程内存中，重启后失效。
- `UserService` 有 IP 维度缓存。
- `ProviderManager` 持有运行时模型路由表和 Hook 缓存。
- `ProxyService` 维护 thread-local `requests.Session`。
- 如果未来引入多实例部署，这些内存态要重新设计。

## 4. Physical View

```mermaid
flowchart LR
    subgraph Clients["客户端"]
        Browser[浏览器管理端]
        SDK[SDK / Curl / OpenAI Client]
    end

    subgraph Host["部署主机"]
        subgraph Service["LLM_Proxy 服务进程"]
            Entry[main.py]
            Server[gevent WSGIServer]
            App[Flask + Controllers + Services]
        end

        ConfigFile[(config.yaml)]
        DB[(data/requests.db)]
        HooksDir[(hooks/*.py)]
        Logs[(logs/*.log)]
    end

    subgraph Providers["外部 Provider"]
        P1[Provider A]
        P2[Provider B]
        PN[Provider N]
    end

    Browser <--HTTP/HTTPS--> Server
    SDK <--HTTP/HTTPS--> Server

    App --> ConfigFile
    App --> DB
    App --> HooksDir
    App --> Logs

    App <--HTTPS--> P1
    App <--HTTPS--> P2
    App <--HTTPS--> PN
```

物理视图要点：
- 部署结构目前非常简单，单机即可运行。
- 本地状态包括配置文件、SQLite、日志文件、Hook 文件。
- 外部依赖主要是上游模型 Provider 的 HTTP API。

## 5. Scenario View

### 5.1 管理员登录

```mermaid
sequenceDiagram
    actor Admin as 管理员浏览器
    participant AuthCtl as AuthenticationController
    participant AuthSvc as AuthenticationService
    participant ConfigMgr as ConfigManager

    Admin->>AuthCtl: POST /api/login
    AuthCtl->>AuthSvc: authenticate(username, password)
    AuthSvc->>ConfigMgr: get_admin_config()
    ConfigMgr-->>AuthSvc: admin credentials
    AuthSvc-->>AuthCtl: authenticated / rejected

    alt 认证成功
        AuthCtl->>AuthSvc: create_session(username)
        AuthSvc-->>AuthCtl: session token
        AuthCtl-->>Admin: Set-Cookie + 200
    else 认证失败
        AuthCtl-->>Admin: 401
    end
```

### 5.2 Provider 配置变更并自动重载

```mermaid
sequenceDiagram
    actor Admin as 管理员浏览器
    participant ProviderCtl as ProviderController
    participant ProviderSvc as ProviderService
    participant ConfigMgr as ConfigManager
    participant App as Application
    participant ProviderMgr as ProviderManager
    participant ProviderCfg as provider_config
    participant Hooks as hooks/*.py

    Admin->>ProviderCtl: PUT /api/providers/{name}
    ProviderCtl->>ProviderSvc: update_provider(payload)
    ProviderSvc->>ConfigMgr: get_raw_config()
    ConfigMgr-->>ProviderSvc: config snapshot
    ProviderSvc->>ProviderCfg: normalize_provider_payload()
    ProviderSvc->>ProviderCfg: validate_provider_definitions()
    ProviderSvc->>ConfigMgr: write_raw_config(updated config)
    ProviderSvc->>App: reload_providers()
    App->>ConfigMgr: reload()
    App->>ConfigMgr: get_raw_config()
    App->>ProviderMgr: load_providers(providers_config)
    ProviderMgr->>ProviderCfg: normalize_runtime_provider_config()
    ProviderMgr->>Hooks: load hook modules if configured
    ProviderMgr-->>App: provider registry refreshed
    ProviderSvc-->>ProviderCtl: updated provider
    ProviderCtl-->>Admin: 200 OK
```

### 5.3 后台探测上游模型列表

```mermaid
sequenceDiagram
    actor Admin as 管理员浏览器
    participant ProviderCtl as ProviderController
    participant DiscoverySvc as ModelDiscoveryService
    participant Upstream as Upstream Provider

    Admin->>ProviderCtl: GET /api/providers/fetch-models?api=...
    ProviderCtl->>DiscoverySvc: fetch_models_preview(...)
    DiscoverySvc->>Upstream: GET /v1/models or /models
    Upstream-->>DiscoverySvc: JSON models payload
    DiscoverySvc-->>ProviderCtl: fetched_models
    ProviderCtl-->>Admin: 200 JSON
```

### 5.4 代理一次 `/v1/chat/completions`

```mermaid
sequenceDiagram
    actor Client as API Client
    participant ProxyCtl as ProxyController
    participant ConfigMgr as ConfigManager
    participant UserSvc as UserService
    participant ProviderMgr as ProviderManager
    participant ProxySvc as ProxyService
    participant Hook as Hook Module
    participant Upstream as Upstream Provider
    participant Adapter as response_adapter
    participant LogSvc as LogService
    participant LogRepo as LogRepository

    Client->>ProxyCtl: POST /v1/chat/completions
    ProxyCtl->>ConfigMgr: is_chat_whitelist_enabled()

    alt 白名单开启
        ProxyCtl->>UserSvc: get_user_by_ip(ip, require_whitelist_access=true)
        UserSvc-->>ProxyCtl: user / None
    end

    ProxyCtl->>ProviderMgr: find_provider_by_model(model)
    ProviderMgr-->>ProxyCtl: LLMProvider
    ProxyCtl->>ProxySvc: proxy_request(provider, body, headers, on_complete)
    ProxySvc->>Hook: header_hook / input_body_hook
    ProxySvc->>Upstream: POST provider.api
    Upstream-->>ProxySvc: response or SSE stream
    ProxySvc->>Adapter: build_proxy_response(...)
    Adapter->>Hook: output_body_hook
    Adapter-->>Client: Flask Response
    Adapter->>LogSvc: on_complete(meta)
    LogSvc->>LogRepo: insert(...)
```

## 6. 当前架构判断

当前架构优点：
- 模块划分清楚，学习成本低。
- 单进程单文件配置的运维复杂度很低。
- 控制平面和数据平面在职责上已经开始分离。
- Hook 扩展点足够轻量，适合小规模快速演进。

当前架构边界：
- 运行时状态仍集中在单进程内存中，不适合多实例横向扩展。
- `presentation` 层仍承担部分接口协议细节和错误映射责任。
- `config` 子系统目前同时承担配置快照、配置规范化、Provider 注册三类职责。
- Hook 是运行时动态加载，扩展灵活，但也引入了热更新和可观测性上的复杂性。

## 7. 后续演进建议

如果后面继续演进，优先级建议是：
- 把 `provider_config` 进一步收敛成显式 schema / factory。
- 为 `ProviderManager` 增加更清晰的只读运行时接口。
- 评估是否把 Session、Provider Registry、User IP 缓存从单进程内存态解耦。
- 补一份 ADR，说明为什么当前仍选择单体 + 单进程 + SQLite。
