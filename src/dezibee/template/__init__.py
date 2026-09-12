"""DeziBee 原型设计工作台模板（三栏骨架 + 画布 + PRD + 双向关联）。

产物（store.create 时复制进每个需求的 demo/）：
 - index.html：Prototype Workspace 入口（三栏骨架 + 画布 + PRD 容器）。
   页面/卡片/PRD 数据在 index.html 顶部 `WORKBENCH_DATA` 常量中，由 AI 直接编辑。
 - css/workbench.css：三栏骨架与画布样式。
 - js/workbench.js：画布引擎（平移/缩放/卡片/连线/双向定位）。
 - GUIDE.md：AI 使用说明（SKILL 式，创建页面/卡片/PRD/关联的标准做法）。

设计约束（需求文档）：纯静态、相对路径、无构建步骤、无外部依赖，可直接部署到
OSS / GitHub Pages；框架只承载，页面/卡片/PRD 内容全部由 AI 按需求生成。
"""
