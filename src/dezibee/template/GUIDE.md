# DeziBee 原型工作台使用说明（SKILL）

> 本文件给 DeziBee(AI) 阅读。你负责在 `WORKBENCH_DATA` 中创建页面、原型卡片、
> 交互关系和 PRD；框架（index.html / css / js）负责承载与联动，**不要修改框架代码**。

## 一、你在哪里工作

需求目录结构：

```
demo/
  index.html      ← 工作台入口（预览/部署的根）
  css/workbench.css
  js/workbench.js
  GUIDE.md        ← 本文件
prd/              ← 可选：更完整的独立 PRD 文档
uploads/          ← 用户上传的参考材料（只读）
```

**修改的唯一入口**：`demo/index.html` 里的 `const WORKBENCH_DATA = {...}`。
用 read_file / write_file / write_file_chunk 直接编辑它（超过 3000 字用分块写入）。

## 二、数据模型（唯一 ID 是核心）

```js
WORKBENCH_DATA = {
  project: { title: "项目名" },

  pages: [                       // 页面（导航栏树）
    {
      id: "page-<唯一短id>",      // 必须全局唯一
      name: "页面名",
      group: "主流程",           // 多级分组用 "一级/二级"；空串=顶级
      cards: [ ... ]            // 该页面画布上的原型卡片
    }
  ],

  links: [                       // 卡片间交互关系（画布连线）
    {
      id: "lnk-<唯一短id>",
      from: "card-xxx",          // 来源卡片 id
      to: "card-yyy",            // 目标卡片 id（可跨页面）
      trigger: "点击「提交」",     // 触发行为
      condition: "表单校验通过",   // 条件（可省略）
      note: "提交成功后跳转"       // 说明（可省略）
    }
  ],

  prd: {
    sections: [                  // PRD 章节（右侧长文档；顺序即文档顺序）
      {
        id: "sec-<唯一短id>",     // 全局唯一
        title: "章节标题",
        level: 2,                // 2/3/4 → h2/h3/h4，目录按 level 缩进
        html: "<p>…</p>"         // 章节内容（HTML 字符串）
      }
    ]
  }
}
```

## 三、原型卡片（核心）

卡片 = 画布上的一个独立节点，字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| id | ✓ | 全局唯一，如 `card-login-default` |
| name | ✓ | 显示名 |
| type | 建议 | 自由定义：`page` / `dialog` / `state` / `flow` / `component`… |
| x, y | ✓ | 画布坐标（自由布局，注意留出间距 80+） |
| w, h | ✓ | 尺寸由你按内容决定（页面 360×640 起，弹窗可 400×300） |
| status | 建议 | `draft` / `confirmed` / `deprecated`（可自由扩展） |
| prdId | ✓ | **关联的 PRD 章节 id（双向联动靠它，不要靠名称）** |
| html | ✓ | **真实 HTML/CSS/JS 原型。禁止用图片模拟页面** |

`html` 写法要求：
- 只写 body 内片段（框架已包 iframe 文档）；样式可内联 `<style>`，脚本可用；
  不要写 `<html>/<body>` 外壳。
- 交互要真实可用：按钮能点、表单能填、弹窗能开（iframe 沙箱允许 scripts/forms/modals）。
- 页面级原型建议自带 `font-family:system-ui` 等基础样式。

**什么值得成为独立卡片**（核心原则）：一个需要开发明确理解的独立视觉/交互状态。
例如：登录页默认态、登录页错误态、支付弹窗、下单成功页。

## 四、PRD 章节

- 每个**卡片**必须有对应章节（`card.prdId` → `section.id`），在章节里表达：
  页面设计 / 结构 / 功能 / 元素 / 交互 / 业务规则 / 状态 / 异常 / 页面跳转 / 数据与 API
  —— 章节名与取舍由你按业务决定，模板不写死。
- 无卡片的页面级说明也可以加纯章节（不需要 prdId 对应）。
- 章节 `html` 中引用某张卡片时，用原型引用标签（点击 → 画布聚焦该卡片）：

```html
参见 <span class="prd-ref" data-card-ref="card-login-default">登录页原型</span>
```

- 也可以用 `<a href="#sec-章节id">` 做章节内跳转（框架给每个章节容器加了
  `id="sec-<章节id>"` 前缀，注意 `#sec-` 前缀要带上）。

## 五、双向关联（必须遵守）

- 卡片 → PRD：浏览器里**单击选中卡片，再次点击** → PRD 自动滚动到对应章节并高亮。
- PRD → 卡片：点击章节里的 `data-card-ref` 引用 → 画布切到所属页面并聚焦高亮卡片。
- 关联全靠 `id`（`card.prdId` / `data-card-ref`）。**禁止**依赖名称匹配。

## 六、画布操作（给用户看的提示）

- 拖空白处平移；滚轮缩放；双击空白=适应全部内容
- `0` 回原点；`Ctrl+1` 适应内容；`Esc` 取消选中
- 卡片头部可拖动移动位置；工具栏「◎ 聚焦」聚焦选中卡片

## 七、页面管理（AI 对话驱动）

用户新增/删除/重命名/排序页面时，直接修改 `pages` 数组：
- 新增：`pages.push({...})` 的数据形式（即编辑 index.html 中的数组文本）
- 删除：从数组移除；**同时**清理其卡片的 links 与对应 PRD 章节，避免死链
- 重命名：改 `name`（id 不变，关联不断）
- 排序：调整数组顺序（导航树按数组顺序渲染；同组内按顺序、组间按组名字典序）

## 八、检查清单（每轮修改后自查）

1. 所有 `pages[].id` / `cards[].id` / `sections[].id` / `links[].id` 全局唯一
2. 每张卡片有 `prdId`，且能在 `prd.sections` 里找到同名 `id`
3. `links` 的 from/to 都指向存在的卡片；删除卡片时同步清理
4. `html` 里没有绝对路径引用（部署到 OSS 后仍可用）；引用图片放 `demo/assets/` 并用相对路径
5. 修改后建议用户点「预览」在浏览器验收
