# 管理端扩展

此目录存放受信任的本地管理端扩展。每个扩展使用独立目录，目录名即扩展 ID：

```text
extensions/request-tools/
├── extension.py                  # 可选：Python 后端入口
├── helpers.py                    # 可选：后端辅助模块
├── frontend/
│   ├── index.ts                  # 可选：页面描述
│   └── RequestToolsPage.vue
└── README.md
```

- `frontend/index.ts` 默认导出 `defineExtensionPage(...)` 的结果，构建期自动发现。
- `extension.py` 导出 `Extension` 类，服务启动时自动加载，路由挂载到 `/api/extensions/<id>/`。
- 页面和后端均为可选；两者都存在时构成完整全栈扩展。

完整页面协议、后端动作、生命周期、长任务和安全边界见 [管理端扩展开发](../docs/extension-development.md)。
