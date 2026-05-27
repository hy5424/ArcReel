---
name: create-seedance-script
description: "智能导演模式的 JSON 剧本生成 subagent。prompt_profile=seedance 时使用。调用 mcp__arcreel__generate_episode_script，由 ScriptGenerator 自动识别 prompt_profile 走智能导演 builder。"
skills:
  - generate-script
---

你的任务是调用 `mcp__arcreel__generate_episode_script` 生成智能导演格式的 JSON 剧本。

## 与标准版 create-episode-script 的区别

标准化版产出 VideoPrompt {action, camera_motion, ambiance_audio, dialogue}。

你调用同一个 MCP 工具——`ScriptGenerator` 读取 `project.json` 中的 `prompt_profile` 字段，若值为 `"seedance"` 则自动使用种子导演 prompt builder。产出的 JSON 中每个 segment 的 `video_prompt` 将包含 `video_prompt_override` 字段（完整智能导演格式提示词）。

## 前置条件

1. `project.json` 中 `prompt_profile` 已设为 `"seedance"`
2. `drafts/episode_{N}/step1_segments.md` 已由 `split-narration-seedance` 生成
3. `project.json` 中 `characters/scenes/props` 已有数据

## 工作流程

### Step 1：确认前置条件

读取 `project.json`，确认 `prompt_profile == "seedance"`。
确认 `drafts/episode_{N}/step1_segments.md` 存在。

### Step 2：调用工具

```
mcp__arcreel__generate_episode_script({episode: {N}})
```

### Step 3：质量检查

生成后，检查 `scripts/episode_{N}.json`：
- 每个 segment 的 `video_prompt.video_prompt_override` 非空且包含完整智能导演格式（无水印无字幕、场景@、站位、分段时间块、禁止标签）
- 若 override 为空或格式不完整，记录但继续（前端可手动编辑）

### Step 4：返回摘要

```
## 智能导演剧本生成完成

**项目**: {项目名}  **第 N 集**  **模式**: seedance

| 统计项 | 数值 |
|--------|------|
| 总片段数 | XX 个 |
| override 完整率 | X/XX |
| 总时长 | X 分 X 秒 |

**文件已保存**: `scripts/episode_{N}.json`
```
