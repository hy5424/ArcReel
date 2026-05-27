"""剧本生成 Prompt 构建器（drama / narration 两种 content_mode）。

设计原则：
- 不重复 schema 已声明的枚举（shot_type / camera_motion 等）；让 response_schema 直接约束。
- 多选枚举字段不在 prompt 里写"如何选"判据，避免把人的镜头审美灌给 LLM；
  让模型按画面内容自行决定。
- 不写无法被 LLM 自检的字数硬限制（"≤200 字"）；用示例隐性表达节奏。
- 字段说明给 1-2 个正例（必要时配一个反例），不堆"必须 / 禁止"清单。
- 节奏建议由 lib.prompt_rules.episode_pacing 注入，跨 subagent 与 builder 共享。
"""

from lib.prompt_rules import is_v2_enabled
from lib.prompt_rules.episode_pacing import render_pacing_section


def _format_names(items: dict) -> str:
    if not items:
        return "（暂无）"
    return "\n".join(f"- {name}" for name in items.keys())


def _format_duration_constraint(supported_durations: list[int], default_duration: int | None) -> str:
    """生成时长约束描述。连续整数集 ≥5 用区间表达，否则枚举。"""
    if not supported_durations:
        raise ValueError("supported_durations 不能为空：调用方必须提供 model 的合法时长列表")

    sorted_d = sorted(set(supported_durations))
    is_continuous = len(sorted_d) >= 5 and all(sorted_d[i] == sorted_d[i - 1] + 1 for i in range(1, len(sorted_d)))
    if is_continuous:
        body = f"{sorted_d[0]} 到 {sorted_d[-1]} 秒间整数任选"
    else:
        durations_str = ", ".join(str(d) for d in sorted_d)
        body = f"从 [{durations_str}] 秒中选择"

    if default_duration is not None:
        if default_duration not in sorted_d:
            raise ValueError(
                f"default_duration={default_duration} 不在 supported_durations={sorted_d} 内，"
                "调用方必须保证默认值合法（否则 prompt 会自相矛盾）"
            )
        return f"时长：{body}，默认 {default_duration} 秒"
    return f"时长：{body}，按内容节奏自行决定"


def _format_aspect_ratio_desc(aspect_ratio: str) -> str:
    if aspect_ratio == "9:16":
        return "竖屏构图"
    if aspect_ratio == "16:9":
        return "横屏构图"
    return f"{aspect_ratio} 构图"


# ---------------------------------------------------------------------------
# 字段写作指导（drama / narration 共用）
# ---------------------------------------------------------------------------

# image_prompt.scene 写作指导：原则 + 正反例。LLM 对示例的泛化优于对清单的执行。
# 好例用方括号小标注隐性传达"主体 / 环境 / 光线 / 氛围"四层覆盖。
_SCENE_WRITING_GUIDE = """用一段连贯的描述说明当前画面中真实可见的元素：角色姿态、面部可观察的状态、环境细节、可见的氛围信号（光线、雾、雨等）。聚焦"此刻这一帧"，不要混入过去/未来事件、抽象情绪词或镜头之外的元素。画面元素（材质、装束、道具质感、环境年代特征）须贴合上方 `<style>` 块定义的风格基调，避免与风格相冲的元素混入（例如赛博朋克风下不出现榻榻米，国风水墨下不出现霓虹屏）。
   好例：「[主体] 林清坐在窗边木桌前，左手撑着下巴，目光落在桌上一封拆开的信纸上。[环境] 桌面摊着信封与一只褪色的怀表。[光线] 半边脸笼在右侧落地窗逆光的蓝灰色阴影里。[氛围] 雨丝拍在木格窗棂，玻璃凝着细小水珠。」
   反例（跑偏）：「林清陷入了多年前那个绝望的雨夜，画面基调：忧郁。光影设定：冷调。」
   反例（过短）：「林清坐在窗边发呆。」——缺少环境元素、光线方向、氛围细节，至少应覆盖主体 / 环境 / 光线 / 氛围中三层。
   反例里这类词族也要避免：陷入 / 回忆 / 思绪 / 意识到 / 画外音 / BGM / 精致 / 震撼。"""

# video_prompt.action 写作指导：动态优先 + 正反例。
# 好例用方括号小标注隐性传达"主体动作 / 物件互动 / 环境动态"三层。
_ACTION_WRITING_GUIDE = """用一段描述说明该时长内主体的连贯动作（肢体动作、手势、表情过渡），可包含必要的环境互动（衣摆、尘埃、推门带起的气流等）。让画面"活"起来，但不要堆叠不可能在单镜头内完成的动作或蒙太奇切换。动词应描述物理可观察动作（伸手 / 转身 / 摩挲 / 投向 / 收紧），避免内心动词。动作幅度应与该 segment 的 duration 匹配：5 秒级镜头通常完成一个连贯动作 + 一个细节互动；8 秒级可承载一次动作过渡（如「抬头—对视—开口」），不要把三组以上独立动作塞进同一 action。
   好例：「[主体动作] 林清缓缓抬起头，眼角微微收紧。[物件互动] 手指无意识地摩挲信纸边缘。[环境动态] 窗外雨势渐大，桌面投下的雨痕影子在缓慢移动。」
   反例：「林清像蝴蝶般飞舞，思绪在过去与现在之间快速切换。」
   反例里这类词族也要避免：思绪飞舞 / 回忆翻涌 / 突然意识到 / 决心 / 仿佛 / 像蝴蝶般。"""

_LIGHTING_WRITING_GUIDE = (
    "描述具体的光源、方向、色温（如「左侧窗户透入的暖黄色晨光（约 3500K）」「头顶单点冷白色的吊灯」）。"
    "可附加摄影质感术语（如「浅景深」「逆光剪影」「丁达尔光柱」「轮廓光勾边」「35mm 胶片颗粒感」），"
    "让画面具备可观察的镜头语言而非抽象修辞；避免「光影神秘」「氛围唯美」这类抽象词。"
)
_AMBIANCE_WRITING_GUIDE = "描述可观察的环境效果（如「薄雾弥漫」「尘埃在光柱里翻飞」），避免抽象情绪词。"
_AMBIANCE_AUDIO_WRITING_GUIDE = (
    "只描写画内音（diegetic sound）：环境声、脚步、物体声响。不要写 BGM、配乐、画外音、旁白。"
)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    segments_md: str,
    supported_durations: list[int],
    episode: int,
    default_duration: int | None = None,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
) -> str:
    """构建说书模式的剧本生成 prompt。"""
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())
    pacing_block = (render_pacing_section("narration") + "\n\n") if is_v2_enabled() else ""

    return f"""# 角色与任务

你是一位资深的短视频分镜编剧，专精把小说片段改写为可直接驱动 AI 图像 / 视频生成的结构化分镜剧本。
你的任务：基于下方"小说片段拆分表"，逐条产出符合 schema 的 JSON 剧本。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

{pacing_block}# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
画面比例：{aspect_ratio}（{_format_aspect_ratio_desc(aspect_ratio)}）
</style>

<characters>
{_format_names(characters)}
</characters>

<scenes>
{_format_names(scenes)}
</scenes>

<props>
{_format_names(props)}
</props>

<segments>
{segments_md}
</segments>

segments 表每行是一个待生成的片段，包含：片段 ID（E{episode}S{{序号}}，当前为第 {episode} 集）、小说原文、{_format_duration_constraint(supported_durations, default_duration)}、是否含对话、是否为 segment_break。

<episode_constraints>
当前正在生成第 {episode} 集。本集所有 segment_id 必须严格使用 `E{episode}S{{两位序号}}` 格式（如 E{episode}S01、E{episode}S02），不得使用其他集号前缀。
若 segments 表里出现非 `E{episode}` 前缀（如 E1S..），视为脏数据，请按当前集号 `E{episode}` 重写。
</episode_constraints>

# 字段写作指引

对每个片段，按下列章节填写字段。

## 基础字段

- **novel_text**：原样复制小说原文，不修改、不删改标点。
- **characters_in_segment** / **scenes** / **props**：仅列出此片段画面或对话中实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
  - 候选 props：[{", ".join(prop_names) or "（无）"}]
  - 不要发明候选之外的名称。
- **segment_break** / **duration_seconds**：与 segments 表保持一致。

## 图片提示词（image_prompt）——切换到「摄影师」视角

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

## 视频提示词（video_prompt）——切换到「动作设计师」视角

- **video_prompt.action**：{_ACTION_WRITING_GUIDE}
- **video_prompt.camera_motion**：每个片段只选一种，按画面内容自行选择。
- **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}
- **video_prompt.dialogue**：仅当小说原文带引号对话时填写；speaker 必须出现在 characters_in_segment。
- **video_prompt.video_prompt_override**：（可选）填入完整种子提示词。非空时后端直接使用此字段生成视频，不再拼接 action/camera_motion 等字段。适合需要精细控制镜头语言的场景。按以下格式书写：

```
无水印无字幕，[画风描述，如 3D国漫CG 或 写实电影感]，无水印无字幕。场景@场景名。
【站位】：(@角色名 空间位置，多角色分号分隔)
{{0-X秒 | 镜头：[景别][运镜]。[画面描述：主体动作、环境互动、光影细节。@角色名（情绪）："台词"]}}
{{X-Y秒 | 镜头：[景别][运镜]。[画面描述]。无台词。}}
【禁止标签】：禁止[本镜禁止的事项]。结尾保持静止不漂移。（注意：每个禁止项必须以"禁止"开头，如 禁止画面中出现字幕、禁止角色变形。无论负面描述还是正面描述，禁止标签里每条都强制加"禁止"前缀，否则 AI 会把"扭曲变形"当成指令执行。）
```

格式规则：
- 用 `@角色名` 引用角色（禁止重复描述服装/外貌），用 `场景@场景名` 引用场景
- 每镜必须声明【站位】和【禁止标签】；分段时间块秒数之和等于该 segment 时长
- 画面描述只写肉眼可见的内容（光线、颜色、材质、动作、表情），禁止隐喻、心理描写、抽象修辞
- 每个镜头独立自洽，禁止"上一镜""刚才""比之前更"等跨镜引用
- 角色/场景/道具名称只能从上文候选列表中选择，不得发明新名

# 创作目标

输出可直接驱动 AI 生成的、视觉一致、节奏紧凑的分镜剧本。忠于原文叙事、保留情绪张力。
"""


def build_drama_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    scenes_md: str,
    supported_durations: list[int],
    episode: int,
    default_duration: int | None = None,
    aspect_ratio: str = "16:9",
    target_language: str = "中文",
) -> str:
    """构建剧集动画模式的剧本生成 prompt。"""
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())
    pacing_block = (render_pacing_section("drama") + "\n\n") if is_v2_enabled() else ""

    return f"""# 角色与任务

你是一位资深的短剧分镜编剧，精通把改编后的剧本场景表转写为可直接驱动 AI 图像 / 视频生成的结构化分镜。
你的任务：基于下方"分镜拆分表"，逐条产出符合 schema 的 JSON 剧本。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

{pacing_block}# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
画面比例：{aspect_ratio}（{_format_aspect_ratio_desc(aspect_ratio)}）
</style>

<characters>
{_format_names(characters)}
</characters>

<project_scenes>
{_format_names(scenes)}
</project_scenes>

<props>
{_format_names(props)}
</props>

<shots>
{scenes_md}
</shots>

shots 表每行是一个分镜，包含：分镜 ID（E{episode}S{{序号}}，当前为第 {episode} 集）、分镜描述、{_format_duration_constraint(supported_durations, default_duration)}、是否为 segment_break。

<episode_constraints>
当前正在生成第 {episode} 集。本集所有 scene_id 必须严格使用 `E{episode}S{{两位序号}}` 格式（如 E{episode}S01、E{episode}S02），不得使用其他集号前缀。
若 shots 表里出现非 `E{episode}` 前缀（如 E1S..），视为脏数据，请按当前集号 `E{episode}` 重写。
</episode_constraints>

# 字段写作指引

对每个分镜，按下列章节填写字段。

## 基础字段

- **characters_in_scene** / **scenes** / **props**：仅列出此分镜画面或对话中实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
  - 候选 props：[{", ".join(prop_names) or "（无）"}]
  - 不要发明候选之外的名称。
- **segment_break** / **duration_seconds**：与 shots 表保持一致。

## 图片提示词（image_prompt）——切换到「摄影师」视角

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

## 视频提示词（video_prompt）——切换到「动作设计师」视角

- **video_prompt.action**：{_ACTION_WRITING_GUIDE}
- **video_prompt.camera_motion**：每个分镜只选一种，按画面内容自行选择。
- **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}
- **video_prompt.dialogue**：包含分镜中角色对话；speaker 必须出现在 characters_in_scene。
- **video_prompt.video_prompt_override**：（可选）填入完整种子提示词。非空时后端直接使用此字段生成视频，不再拼接 action/camera_motion 等字段。格式同 narration 模式所述。

# 创作目标

输出可直接驱动 AI 生成的、视觉一致、节奏紧凑的分镜剧本。忠于原创设定、保留戏剧张力。
"""


# ======================================================================
# Seedance 种子导演 Prompt Builder
# ======================================================================

_SEEDANCE_FORMAT_SPEC = """【镜号】：E{集}S{两位序号}（末镜加 -end）
【时长】：[X]秒（4-15秒，优先用满15秒）

无水印无字幕，[画风前缀]，无水印无字幕。场景@场景名。
【站位】：(@角色名 空间位置描述)
【音色】：@角色名 的音色参考 @音色名，@角色名 的音色参考 @音色名
{0-X秒 | 镜头：[景别][运镜]。[画面描述。@角色名（情绪描述）："台词"]}
【禁止标签】：禁止[事项1]。禁止[事项2]。结尾保持静止不漂移。

格式铁律：
- 【站位】每镜必填，单角色也写
- 【音色】有台词时必填——列出每个说话角色的音色参考（@角色名 的音色参考 @音色名）。音色名从候选 timbres 列表选取。每镜最多 3 个音色（即梦 --audio 上限 3）。
- 【禁止标签】每镜必填。每条负面行为以"禁止"开头；结尾静止不漂移无需"禁止"。结尾镜必含防漂移。
- 分段时间块秒数和 = 镜号时长，每镜 ≥1 块。场景@ 仅切换时写。
- @角色名（情绪描述）："台词"——情绪描述必填，不拘形式：标签（焦急万分/声如洪钟）、感官比喻（声音尖锐如指甲划过石板）、动作暗示（手指攥紧衣角）均可。画外音 @角色名(O.S.)。所有 @引用后必须加空格。"""


def build_seedance_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    segments_md: str,
    supported_durations: list[int],
    episode: int,
    default_duration: int | None = None,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    timbres: dict | None = None,
) -> str:
    """构建种子导演模式（seedance）的说书 prompt。

    与标准版不同：视频提示词统一输出到 video_prompt_override 字段，
    采用完整的即梦 Seedance 2.0 导演格式——含站位、分段时间块、禁止标签、
    镜头关系、黄金前5秒钩子、结尾钩子。
    """
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    # 角色-音色绑定提示
    timbre_bindings = []
    for name, info in characters.items():
        tid = info.get("timbre_id") if isinstance(info, dict) else None
        if tid:
            timbre_bindings.append(f"{name} → {tid}")
    bindings_block = ""
    if timbre_bindings:
        bindings_block = "\n角色音色绑定：\n" + "\n".join(f"- {b}" for b in timbre_bindings) + "\n（以上角色已有预设音色，【音色】行请使用绑定的音色名）\n"

    return f"""# 身份

你是电影导演。你的工作：拿到剧本后脑海中看到这场戏，用镜头语言——角度、运动、光线、焦点、节奏——精确控制观众的情绪。

**核心信条**：两个镜头之间的关系比单个镜头自身的精美更重要。

将每个片段转化为完整的即梦 Seedance 2.0 视频生成提示词，写入 video_prompt_override 字段。

{bindings_block}

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
画面比例：{aspect_ratio}（{"竖屏构图" if aspect_ratio == "9:16" else "横屏构图"}）
</style>

<characters>
{_format_names(characters)}
</characters>

<scenes>
{_format_names(scenes)}
</scenes>

<props>
{_format_names(props)}
</props>

<timbres>
{_format_names(timbres or {})}
</timbres>

<segments>
{segments_md}
</segments>

segments 表每行一个待生成片段：ID（E{episode}S{{序号}}）、小说原文、{_format_duration_constraint(supported_durations, default_duration)}、是否含对话、是否为 segment_break。

<episode_constraints>
当前第 {episode} 集。所有 segment_id 必须为 `E{episode}S{{序号}}` 格式。
</episode_constraints>

# 基础字段

- **novel_text**：原样保留原文。
- **characters_in_segment / scenes / props**：仅列此片段实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
  - 候选 props：[{", ".join(prop_names) or "（无）"}]
  - 禁止发明候选之外的名称。
- **segment_break / duration_seconds**：与 segments 表一致。

# 图片提示词（image_prompt）

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：从枚举中按画面内容选择。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

# 视频提示词（video_prompt）——保底字段

先照常填写 video_prompt.action / camera_motion / ambiance_audio / dialogue 作为保底。
然后填写 video_prompt.video_prompt_override，按下方种子导演格式输出。

# video_prompt_override ——种子导演格式

## 输出格式

{_SEEDANCE_FORMAT_SPEC}

## 时长

- 全片 50-150 秒（最佳 ~75s）。单镜 4-15s，优先用满 15s，场景切换/情绪断点处才拆分。
- 多角色对话：每两次问答间 ≥0.5s 反应微动作（眼神/手势/喉结）。

## 语速

| 档位 | 字/秒 | 留白 | 适用 |
|------|-------|------|------|
| S | ~2 | +50% | 临终嘱托、绝望呓语 |
| A | ~2.5 | +20% | 伤感独白、重要坦白 |
| B | ~3 | +10% | 常规对话 |
| C | ~4 | +5% | 急切交流 |
| D | ~5 | 0% | 争吵咆哮 |

单镜台词≤120字，2秒内≤12字，字数÷秒数≤档位。

## 导演规则（逐镜内部完成，不输出）

**每镜决策**：
1. 叙事任务（五选一）：推进剧情 / 揭示性格 / 强化情绪 / 制造悬念 / 主题隐喻
2. 本镜与上镜关系：但是 / 所以 / 然而 / 与此同时
3. 遮脸测试：关台词+遮面部，还能理解50%吗？（不能→重写）

**第一镜·黄金前5秒**（不通过不写第二镜）：
至少一项：信息差（未知答案的问题）/ 视觉冲击（色彩/动静强烈对比）/ 违反预期。打法：冲击式（猛烈运动+推镜）| 倒置式（特写→急拉远）| 阈限式（悬停+极缓推）。

**末镜·结尾钩子**（五选一，每集不重复）：
悬停定格 / 新元素闯入 / 揭示关键信息 / 角色凝视画外 / 空镜余韵(2-3s)。

**画面铁律**：
- 只写肉眼可见：光线/颜色/材质/动作。禁用"观众感受到""预示着""象征着""像一块石头""光影神秘"等隐喻/抽象词。写"左侧暖黄晨光(3500K)"而不是"光影唯美"。
- @角色名 引用角色（禁止重复描述服装/发型），场景@场景名 引用场景。所有 @引用后加空格。
- 每镜独立自洽：严禁跨镜引用（"上一镜""刚才""判若两人""那个方向"）。
- 构图用自然语言（"居中偏上""前景/中景/后景"），禁止精确坐标。

**技法约束**：
- 景别≥5种（远+特写各≥1）、运镜≥4种。相邻镜≥2维度变化。最快最慢差>5s。至少1处留白。
- 关键台词焦点在听者。对话用空间纵深，禁两人并排。每5镜内≥1次景别跳3级。

**AI现实约束**：
- 复杂动作拆"起势→关键一击→收势"，关键动作缩景别。不同时描述≥3个独立运动物。
- 情绪≥2通道表达（面部+手/姿态/光影）。暗光必补光。不依赖画中文字。首镜锚定（中景+建空间）。

## 自检（逐镜内部完成）

- [ ] 纯视觉、无隐喻、无跨镜、@引用正确加空格
- [ ] 【站位】+【禁止标签】已填，负面款带"禁止"，结尾含防漂移
- [ ] 分段时间块秒数和=时长，台词字数≤档位
- [ ] 多角色问答间隔≥0.5s，复杂动作已拆三段

全片检查：总时长50-150s | 景别≥5种+运镜≥4种 | ≥1处留白 | 前5秒钩子+结尾钩子

# 创作目标

忠于原文，用导演语言转化为可直接驱动 AI 视频生成的分镜剧本。
"""


def build_seedance_drama_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    scenes_md: str,
    supported_durations: list[int],
    episode: int,
    default_duration: int | None = None,
    aspect_ratio: str = "16:9",
    target_language: str = "中文",
) -> str:
    """种子导演模式的 drama prompt 构建器。"""
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    return f"""# 身份

你是电影导演。与 build_seedance_narration_prompt 同等导演身份，但是针对剧集动画（drama）模式。

将每个场景转化为完整的即梦 Seedance 2.0 视频生成提示词，写入 video_prompt_override 字段。

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
{_format_names(characters)}
</characters>

<scenes>
{_format_names(scenes)}
</scenes>

<props>
{_format_names(props)}
</props>

<shots>
{scenes_md}
</shots>

shots 表每行一个分镜：ID（E{episode}S{{序号}}）、描述、{_format_duration_constraint(supported_durations, default_duration)}、是否为 segment_break。

<episode_constraints>
当前第 {episode} 集。所有 scene_id 必须为 `E{episode}S{{序号}}` 格式。
</episode_constraints>

# 基础字段

- **characters_in_scene / scenes / props**：仅列此分镜实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
  - 候选 props：[{", ".join(prop_names) or "（无）"}]
  - 禁止发明候选之外的名称。
- **segment_break / duration_seconds**：与 shots 表一致。

# 图片提示词（image_prompt）

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：按画面内容选择。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

# 视频提示词（video_prompt）

先填 video_prompt.action / camera_motion / ambiance_audio / dialogue 作为保底。
然后填 video_prompt.video_prompt_override，按种子导演格式输出。

## 输出格式

{_SEEDANCE_FORMAT_SPEC}

## 核心规则

- 全片 50-150s（最佳 ~75s），单镜 4-15s 优先 15s
- 语速 S~2 / A~2.5 / B~3 / C~4 / D~5 字/秒，字数÷秒数≤档位
- 只写肉眼可见，禁隐喻/心理/抽象。@引用后加空格，禁跨镜引用
- 【站位】+【禁止标签】每镜必填，负面款以"禁止"开头，结尾含防漂移
- 第一镜黄金前5秒（信息差/视觉冲击/违反预期），末镜结尾钩子五选一
- 景别≥5种+运镜≥4种，至少1处留白，关台词+遮面能理解50%+
- 复杂动作拆三段，情绪双通道，暗光必补光

# 创作目标

忠于原创设定，用导演语言转化为分镜剧本。
"""


def build_normalize_prompt(
    novel_text: str,
    project_overview: dict,
    style: str,
    characters: dict,
    scenes: dict,
    props: dict,
    default_duration: int | None,
    supported_durations: list[int],
    episode: int,
) -> str:
    """Step-1 normalization prompt: novel text → markdown scene table.

    Consumed by ``normalize_drama_script`` MCP tool. Sibling of
    ``build_drama_prompt`` (step 2 of the drama pipeline).
    """
    char_list = _format_names(characters)
    scene_list = _format_names(scenes)
    prop_list = _format_names(props)

    # 规范化 + 校验：空集合或 default 不在集合内都会产出自相矛盾的提示词，
    # 让生成阶段失败比让 LLM 见到"只能取 — 中的值"更便于诊断（PR #528 review）。
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        raise ValueError(f"default_duration={default_duration} 不在 supported_durations={normalized_durations} 内")

    durations_str = ", ".join(str(d) for d in normalized_durations)
    max_dur = normalized_durations[-1]

    if default_duration is not None:
        duration_rules = (
            f"- 时长：只能取 {durations_str} 中的值（该视频模型支持的秒数集合）\n"
            f"- 每场景默认 {default_duration} 秒；打斗、大场面、情绪铺陈等画面可取更长值至上限 {max_dur} 秒，"
            "不要默认挑最短值"
        )
    else:
        duration_rules = (
            f"- 时长：只能取 {durations_str} 中的值（该视频模型支持的秒数集合）\n"
            f"- 按画面内容复杂度匹配合适时长（最长 {max_dur} 秒），不强制默认值"
        )

    return f"""你的任务是将小说原文改编为结构化的分镜场景表（Markdown 格式），用于后续 AI 视频生成。

## 项目信息

<overview>
{project_overview.get("synopsis", "")}

题材类型：{project_overview.get("genre", "")}
核心主题：{project_overview.get("theme", "")}
世界观设定：{project_overview.get("world_setting", "")}
</overview>

<style>
{style}
</style>

<characters>
{char_list}
</characters>

<scenes>
{scene_list}
</scenes>

<props>
{prop_list}
</props>

## 小说原文

<novel>
{novel_text}
</novel>

## 输出要求

将小说改编为场景列表，使用 Markdown 表格格式：

| 场景 ID | 场景描述 | 时长 | segment_break |
|---------|---------|------|---------------|
| E{episode}S01 | 详细的场景描述... | <duration> | 是 |
| E{episode}S02 | 详细的场景描述... | <duration> | 否 |

规则：
- 当前正在生成第 {episode} 集；所有场景 ID 必须使用 `E{episode}S{{两位序号}}` 格式，不得使用其他集号前缀
- 场景描述：改编后的剧本化描述，包含角色动作、对话、环境，适合视觉化呈现
{duration_rules}
- segment_break：场景切换点标记"是"，同一连续场景标"否"
- 每个场景应为一个独立的视觉画面，可以在指定时长内完成
- 避免一个场景包含多个不同的动作或画面切换

仅输出 Markdown 表格，不要包含其他解释文字。
"""
