# Frontend(Next.js 15 + Tailwind)

教学版极简 UI,演示如何对接后端 LangGraph pipeline。

## 启动

```bash
pnpm install   # 或 npm install
pnpm dev
# 浏览器访问 http://localhost:3000
```

后端必须先在 8001 端口跑起来:

```bash
cd ../backend
uvicorn co_scientist.api.main:app --reload --port 8001
```

## 扩展方向(留给你)

- 用 D3.js 画研究分叉树
- 用 Cytoscape.js 渲染知识图谱(读取后端的 graphml)
- 用 react-syntax-highlighter 显示生成的代码
- 流式渲染 Reviewer 对话气泡(改后端 WS 推送 token-by-token)
