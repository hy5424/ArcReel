"""参考生视频模式 Prompt 构建器。

设计原则与 prompt_builders_script.py 一致：
- 不重复 schema 已声明的枚举（type 等）；让 response_schema 直接约束。
- 多选枚举字段不在 prompt 里写"如何选"判据；让模型按画面内容自行决定。
- 字段说明给指导和 example，不堆"必须 / 禁止"清单。
- 跨 backend 时长 / references 上限通过参数显式注入，不在文本里硬编码秒数。
"""

from __future__ import annotations


def _format_asset_names(assets: dict | None) -> str:
    if not assets:
        return "（无）"
    return "\n".join(
        f"- {name}: {meta.get('description', '') if isinstance(meta, dict) else ''}" for name, meta in assets.items()
    )


def build_reference_video_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    units_md: str,
    supported_durations: list[int],
    max_refs: int,
    episode: int,
    max_duration: int | None = None,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
) -> str:
    """构建参考生视频模式的 LLM Prompt。

    Args:
        project_overview: 项目概述（synopsis, genre, theme, world_setting）。
        style / style_description: 视觉风格标签与描述。
        characters / scenes / props: 三类已注册资产字典（用于候选列表）。
        units_md: `step1_reference_units.md` 内容（subagent 输出）。
        supported_durations: 当前视频模型支持的单镜头时长列表（秒）。
        max_refs: 当前视频模型支持的最大参考图数。
        max_duration: 当前视频模型的单次生成时长上限（秒）。传入时 prompt 会显式
            引导 LLM 让 unit 总时长贴近该值，避免默认挑最短值；为 None 时不插入该段。
    """
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    durations_desc = "/".join(str(d) for d in supported_durations) + "s"
    max_duration_line = (
        f"\n   - unit 内所有 Shot `duration` 之和宜贴近 {max_duration} 秒（当前模型上限），"
        f"除非内容明显不需要这么长；不要默认挑最短值，也不得超过 {max_duration}。"
        if max_duration is not None
        else ""
    )

    return f"""# 角色与任务

你是一位资深的短视频分镜编剧，本任务是为「参考生视频」模式产出 JSON 剧本。
你的任务：基于下方 step1_units 表，按 schema 产出 ReferenceVideoScript。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段**。

# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
画面比例：{aspect_ratio}
</style>

<characters>
{_format_asset_names(characters)}
</characters>

<scenes>
{_format_asset_names(scenes)}
</scenes>

<props>
{_format_asset_names(props)}
</props>

<step1_units>
{units_md}
</step1_units>

<episode_constraints>
当前正在生成第 {episode} 集。本集所有 unit_id 必须严格使用 `E{episode}U{{两位序号}}` 格式（如 E{episode}U01、E{episode}U02），不得使用其他集号前缀。
若 step1_units 表里出现非 `E{episode}` 前缀（如 E1U..），视为脏数据，请按当前集号 `E{episode}` 重写。
</episode_constraints>

# 字段写作指引

对每个 video_unit，按下列要求填写字段：

a. **unit_id**：保留 step1 中的 `E{episode}U{{序号}}`（当前为第 {episode} 集），不要改格式。

b. **shots**：1-4 个 Shot。
    - `duration`：整数秒，取值必须在当前模型支持列表中：{durations_desc}。{max_duration_line}
    - `text`：镜头描述，聚焦此刻可见画面（语言遵循上方"输出语言"约束）。仅用 `@[名称]` 引用角色 / 场景 / 道具——**不要**写外貌、服装、场景细节（这些由参考图提供视觉一致性）。
        - 好例：「@[角色A] 立于 @[场景A] 前，左手紧握 @[道具A]，目光投向远处」。
        - 反例：「身穿某色服装的角色A 站在某色场景A 前，手里紧握着某色道具A」（外貌 / 服装 / 颜色应由参考图承担）。
        - 动词应描述物理可观察动作（伸手 / 转身 / 摩挲 / 投向 / 收紧），避免「陷入 / 回忆 / 意识到 / 决定」等内心动词。
    - 单 unit 内所有 Shot `duration` 之和即该 unit `duration_seconds`。

c. **references**：`{{type, name}}` 列表，顺序决定 `[图N]` 编号。
    - `name` 必须来自候选：
        - character: {", ".join(character_names) or "（无）"}
        - scene: {", ".join(scene_names) or "（无）"}
        - prop: {", ".join(prop_names) or "（无）"}
    - 每个 shot `text` 中出现的 `@[名称]` 都要在 references 注册一次。
    - **references 数量不超过 {max_refs}**（模型上限）；超出时把次要角色合并到背景描述。

d. **duration_seconds**：所有 shot `duration` 之和；不要手动覆盖。

# 顶层字段

- `title` 必填。
- `episode` / `content_mode` / `generation_mode` / `novel` / `duration_seconds` 由 caller 注入或派生，不需 LLM 填。

# 复核

- 每 unit 最多 4 个 shot；shot 时长之和贴近 step1 预估。
- `@[名称]` 只能引用 characters / scenes / props 三表中已注册的名字。
- 不要在 shot `text` 中描写外貌、服装、场景细节。
- 不要发明新资产。

请按 step1_units 顺序逐 unit 产出。
"""


def build_seedance_reference_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    units_md: str,
    supported_durations: list[int],
    max_refs: int,
    episode: int,
    max_duration: int | None = None,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    timbres: dict | None = None,
) -> str:
    """种子导演模式的参考生视频 prompt 构建器。

    与标准版不同：每个 shot 的 text 字段采用种子导演格式——
    含画风前缀、场景@、站位、分段时间块、禁止标签。
    """
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    # 角色-音色绑定提示
    timbre_bindings = []
    for name, info in characters.items():
        tid = (info.get("timbre_id") or info.get("voice_style")) if isinstance(info, dict) else None
        if tid and (timbres or {}).get(tid):
            timbre_bindings.append(f"{name} → {tid}")
    bindings_block = ""
    if timbre_bindings:
        bindings_block = "\n角色音色绑定：\n" + "\n".join(f"- {b}" for b in timbre_bindings) + "\n（以上角色已有预设音色，【音色】行请使用绑定的音色名）\n"

    durations_desc = "/".join(str(d) for d in supported_durations) + "s"
    max_duration_line = (
        f"\n   - unit 内所有 Shot `duration` 之和宜贴近 {max_duration} 秒（当前模型上限），"
        f"除非内容明显不需要这么长；不要默认挑最短值，也不得超过 {max_duration}。"
        if max_duration is not None
        else ""
    )

    return f"""# 身份

{bindings_block}你是电影导演。本任务是为「参考生视频 + 种子导演」模式产出 JSON 剧本。

将每个 shot 的 text 字段写成完整的即梦 Seedance 2.0 导演格式提示词。

**输出语言**：{target_language}。JSON 键名保持英文。

# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
画面比例：{aspect_ratio}
</style>

<characters>
{_format_asset_names(characters)}
</characters>

<scenes>
{_format_asset_names(scenes)}
</scenes>

<props>
{_format_asset_names(props)}
</props>

<step1_units>
{units_md}
</step1_units>

<episode_constraints>
当前第 {episode} 集。所有 unit_id 必须为 `E{episode}U{{序号}}` 格式。
</episode_constraints>

# 字段指引

## 基础字段（与标准版相同）

- **unit_id**：保留 E{episode}U{{序号}}
- **shots**：每个 unit 固定 1 个 shot；种子模式下单个 shot 已包含完整的分段时间块+站位+禁止标签，多 shot 不必要。shot duration 取 {durations_desc}{max_duration_line}
- **references**：type + name，name 只能从候选取：
    - character: {", ".join(character_names) or "（无）"}
    - scene: {", ".join(scene_names) or "（无）"}
    - prop: {", ".join(prop_names) or "（无）"}
- references 总数 ≤ {max_refs}

## shot.text ——种子导演格式

```
无水印无字幕，[画风前缀]，无水印无字幕。场景@场景名。
【站位】：(@角色名 空间位置，多角色逗号分隔)
{{0-X秒 | 镜头：[景别][运镜]。[画面描述，纯视觉内容。@角色名（情绪）："台词"]}}
【禁止标签】：禁止[事项1]。禁止[事项2]。结尾保持静止不漂移。
```

## 核心规则

**格式**：每 shot 必填【站位】+【禁止标签】（负面款以"禁止"开头，结尾含防漂移）。时间块秒数和=shot.duration。场景@仅切换时写。**台词必带情绪描述**——@角色名（焦急万分/声如洪钟/手指攥紧衣角）："台词"。所有 @引用后加空格。references 按 @引用顺序。

**时长**：全片 50-150s（最佳~75s）。单 shot 4-15s 优先 15s。多角色问答间隔≥0.5s。

**语速**：S~2 / A~2.5 / B~3 / C~4 / D~5 字/秒，字数÷秒数≤档位，单 shot 台词≤120字，2s内≤12字。

**画面**：只写肉眼可见，禁隐喻/心理/抽象（不说"像一块石头"，写具体的颜色/材质/光线）。禁跨镜引用。构图用自然语言，禁精确坐标。

**技法**：景别≥5种（远+特写各≥1）、运镜≥4种。最快最慢差>5s。至少1处留白。关键台词焦点在听者。禁两人并排对镜头。

**AI约束**：复杂动作拆三段+缩景别。情绪双通道。暗光必补光。不依赖画中文字。首镜锚定（中景+建空间）。

**叙事**：第一 shot 黄金前5秒（信息差/视觉冲击/违反预期，不通过不进下一 shot）。末 shot 结尾钩子五选一（悬停/闯入/揭示/凝视/余韵，每集轮换）。

请按 step1_units 顺序逐 unit 产出。
"""
