# 个人开发上下文

本文件记录 AGENTS.md 不覆盖的个人开发信息：fork 关系、分支策略、自研功能、部署备忘。

## Fork 关系

| 远程 | 地址 | 说明 |
|------|------|------|
| origin | `git@github.com:hy5424/ArcReel.git` | 我的 fork |
| upstream | `https://github.com/ArcReel/ArcReel.git` | 主仓库 |

## 分支

- `main` — 跟踪 upstream/main，**不直接在上面开发**
- `custom` — 当前工作分支，领先 main 72 commits

### 同步上游

```bash
git fetch upstream
git checkout main && git merge upstream/main
git checkout custom && git rebase main
# 如有冲突，解决后 git rebase --continue
```

## custom 分支自研功能

### 音色系统（Timbre）— 主力功能
- 数据层：`assets` 表加 `audio_file`/`gender`/`age_range` 字段，alembic 迁移
- 后端：`server/routers/timbres.py` 完整 CRUD API，上传分两步（JSON 创建 + 文件上传）
- 前端：`AssetSidebar` 音色导航入口、`AssetFormModal` 音色上传、角色卡片音色下拉选择器
- Dreamina 集成：`@音色名` 解析查 `project.json`，`--audio` 参数传递
- 编辑器增强：`@音色名` → teal 高亮、mention lookup 注册、非贪婪正则匹配+去重

### 种子导演模式（Seed Director）
- `prompt_profile` 可选切换，剧本生成器按 profile 路由不同 subagent
- 分集阶段：按叙事节奏（非字数）拆分，入口/出口钩子 + 完整性 + 密度四重检查
- 提示词语法高亮：站位/禁止标签/时间块/元信息着色
- 每条提示词强制 `【禁止标签】` ，防止 AI 反向执行

### Dreamina（即梦）集成
- 注册全部可用模型：4 视频 + 8 图片后端
- OAuth 数据目录挂载、文件下载重命名为 ArcReel 期望路径
- 参考生视频支持 `multimodal2video` + `[图N]→图片N` 转换

### 部署与基础设施
- Docker：uv 二进制 + frozen sync 加速构建，容器加公共 DNS
- uv 下载走 `gh.ddlc.top` 镜像

### 格式规范
- `@引用` 后强制加空格，防止与后续文字粘连
- prompt 中禁 ✅❌ 图标，改用文字正误
- 台词必须带情绪描述

## 开发命令

```bash
cd /home/lcy/ArcReel

# 后端
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241

# 测试/lint
uv run python -m pytest
uv run ruff check . && uv run ruff format .
uv run basedpyright

# 前端（先 cd frontend）
pnpm lint && pnpm check
```

## 注意事项

- 不要在 `custom` 分支做 `git rebase` 以外的历史改写（已推送到 origin）
- 上游 AGENTS.md 可能会被 upstream 更新覆盖，本文件不会冲突
- `.env` 从 `.env.example` 复制，WebUI `/settings` 管理 API Key
