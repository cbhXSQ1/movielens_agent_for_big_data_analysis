# 前端（MovieLens 大数据分析助手）

Vue 3 + Vite + TypeScript 单页应用，组件库 [Naive UI](https://www.naiveui.com/)，状态管理 Pinia，测试 Vitest + Vue Test Utils。

> 本目录处于「前端重写·基建」阶段：仅工程骨架与占位页，业务界面由后续子智能体按 `docs/frontend/前端重写计划.md` 实现。

## 环境要求

- Node.js ≥ 20（开发使用 Node v25.9.0）
- npm ≥ 10

## 安装

```bash
cd frontend
npm install
```

## 开发

```bash
npm run dev        # 启动 Vite 开发服务器（默认 http://localhost:5173）
```

## 构建

```bash
npm run build      # vue-tsc 类型检查 + vite build，产物在 dist/
npm run preview    # 本地预览 dist/ 产物
```

构建产物为静态文件，`base: './'`，可直接用任意静态服务器托管 `dist/`。

## 测试

```bash
npm run test       # vitest run
```

## 接口基址配置

默认接口基址为 `http://localhost:8765`（后端 `python3 -m agent.http_api --port 8765`，已开 CORS）。

可用环境变量 `VITE_API_BASE` 覆盖：

```bash
# 开发时
VITE_API_BASE=http://192.168.1.10:8765 npm run dev

# 或在 frontend/.env.local 中
VITE_API_BASE=http://192.168.1.10:8765
```

代码中统一从常量模块取用：

```ts
import { API_BASE, apiUrl, sseUrl } from '@/api/config'
```

## 目录结构

```
frontend/
  index.html            # 入口 HTML
  vite.config.ts        # Vite 配置（base:'./'、@→src 别名、Vitest 配置）
  tsconfig.json         # 应用 TS 配置
  tsconfig.node.json    # 构建工具 TS 配置
  src/
    main.ts             # 应用入口
    App.vue             # 根组件（n-config-provider）
    env.d.ts            # 环境类型声明
    api/                # 数据层（config.ts 为接口基址常量）
    stores/             # Pinia stores
    views/              # 页面级视图
    components/         # 通用组件
    theme/              # Naive UI 主题配置
    __tests__/          # 测试
```

## 路径别名

`@` 指向 `src`（同时配置于 `vite.config.ts` 与 `tsconfig.json`）。
