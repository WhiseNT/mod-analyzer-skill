---
name: mod-analyzer-skill
description: "用于分析 Minecraft Mod / 模组 / mod jar / Forge / NeoForge / Fabric / Quilt 项目。用户要求反编译 jar、探查模组代码与资源、理解模组玩法、梳理物品方块机制、生成模组简介、主要玩法、核心玩法循环、细粒度流程或游戏内容文档时必须使用。该 skill 会先静态检查 jar，再按 loader 元数据、数据包资源、lang、recipe、registry、event、mixin、GUI、网络包与配置追踪证据，避免只凭类名猜测。此外还支持三种专项分析：模组设计分析（评价玩法循环、难度曲线、玩家引导的设计质量）、模组代码分析（面向开发/魔改的代码架构、mixin、事件、API、配置与魔改点梳理）以及物品深度分析（面向 LLM/AI 工具的 JSON 结构化物品数据库，含配方、标签、功能描述和系统关系）。不要把本 skill 用于普通 Java 代码重构、恶意篡改第三方 mod、绕过授权、提取商业资产再发布等任务。"
---

# Mod Analyzer Skill

> Minecraft Mod 反编译、代码探查与游戏内容文档生成 skill。目标不是做代码审计报告，而是把 jar / 源码 / 数据包资源转译成玩家与策划能读懂的"模组游戏内容文档"。

## 何时使用

当用户要求以下任一事项时使用本 skill：
- 分析 Minecraft Mod、模组、mod jar、`.jar` 文件、整合包里的某个 mod
- 反编译 Forge / NeoForge / Fabric / Quilt mod 并理解玩法
- 从源码或 jar 中梳理物品、方块、配方、世界生成、生物、维度、GUI、网络包、事件、mixin、配置
- 输出模组简介、主要玩法、核心玩法循环、细粒度玩家流程、内容设计文档、攻略式玩法说明
- 判断一个 mod "怎么玩""主线是什么""核心循环是什么""代码里有哪些隐藏机制"
- 做模组设计分析（评价玩法循环、难度曲线、新手引导、内容组织的设计质量）
- 做模组代码分析（面向开发/魔改：注册架构、mixin、事件、API、配置项、魔改点梳理）
- 做物品深度分析（为 LLM/AI 工具生成结构化的 JSON 物品数据库，包含配方、功能描述、系统关系）

不要把本 skill 用于普通 Java 代码重构、恶意篡改第三方 mod、绕过授权、提取商业资产再发布等任务。本 skill 默认只做静态分析和文档化。

## 核心目标

最终回答 6 个问题：
1. 这个 mod 承诺玩家体验什么？
2. 它提供了哪些主要游戏内容？
3. 玩家第一次接触、进入核心玩法、形成循环分别发生在什么环节？
4. 核心玩法循环是什么：投入什么、做什么操作、得到什么反馈、解锁什么下一步？
5. 关键流程的细粒度步骤是什么：物品、配方、交互、GUI、世界生成、事件触发、失败条件分别如何串起来？
6. 哪些结论有代码/资源证据，哪些只是推断或仍不确定？

## 绝对原则

1. **先证据，后结论。** 所有玩法判断都要回到 jar 资源、反编译代码、配方、lang、配置或 mixin 证据。
2. **不要运行不可信 jar。** 不启动 Minecraft，不执行 mod jar，不把第三方代码当程序运行。反编译器可以读取 jar，但不要运行 jar 内主类。
3. **不要只凭名称猜玩法。** 类名、物品名只能提供线索，必须结合注册、配方、事件、GUI、tooltip、advancement、worldgen 等证据验证。
4. **数据驱动和代码驱动同等重要。** 很多玩法藏在 `data/`、`assets/`、`lang`、tags、recipes、loot tables、advancements、Patchouli/GuideMe 文档里，不只在 Java/Kotlin 类里。
5. **把代码路径翻译成玩家路径。** 报告要解释玩家如何体验机制，而不是堆类名。
6. **不确定就标注不确定。** 混淆、反编译失败、动态注册、跨 mod API 调用、配置分支、服务端/客户端差异，都要写入"不确定项与需实测项"。
7. **细粒度流程必须可复核。** 每条流程至少列出玩家动作、触发条件、关键代码/资源、产出结果、失败/限制条件。

## Requirements

- 必需：Python 3、Java Runtime（用于运行 Java 反编译器）。
- 推荐：本地安装或缓存 Vineflower / CFR / FernFlower 反编译器 jar。
- 若本地没有反编译器，且网络访问被允许，使用 `scripts/decompile_mod_jar.py --download-decompiler vineflower` 从 Maven Central 下载并缓存反编译器。下载是显式动作，不运行目标 mod jar；脚本会在可用时校验 Maven Central 的 SHA-1 sidecar。
- 不建议把 IntelliJ IDEA 内置反编译器作为默认依赖：它通常随 IDE 插件打包，命令行入口不如独立 Vineflower/CFR 稳定。若用户明确提供 IDEA/FernFlower 可执行 jar，再通过 `--decompiler-jar` 使用。

## 默认产物位置

若用户没有指定输出路径，使用：

```text
output/mod-analysis/<modid-or-jar-name>/
├── mod_inventory.json          # jar 静态清单，来自 scripts/inspect_mod_jar.py
├── decompiled/                 # 反编译源码，如用户允许且工具可用
├── notes/                      # 探查笔记，可按 feature 拆分
└── 模组游戏内容文档-<mod-name>.md
```

如果用户只需要对话回答，也仍应按该结构组织内容，只是不落盘或少落盘。

## 分析类型选择

执行前先确认用户需要哪种输出类型：

| 分析类型 | 输出产物 | 目标受众 | 核心问题 |
|----------|----------|----------|----------|
| **游戏内容文档**（默认） | `模组游戏内容文档-<mod-name>.md` | 玩家、策划 | 这个 mod 怎么玩？ |
| **设计分析报告** | `模组设计分析报告-<mod-name>.md` | 策划、整合包作者 | 这个 mod 设计得好不好？ |
| **代码分析报告** | `模组代码分析报告-<mod-name>.md` | 开发者、魔改作者 | 这个 mod 代码怎么组织的？怎么改？ |
| **物品深度分析** | `<mod-name>_items_deep.json` | LLM/AI 工具、数据集成 | 这个 mod 的物品有什么功能？配方？系统关系？ |

三种分析共享阶段 0~2（输入确认 + 静态清单 + 反编译/源码读取），阶段 3 开始分岔。如果用户没有明确指定，默认输出"游戏内容文档"。

## 工作流总览

### 阶段 0：确定输入与边界

先确认当前可用材料：
- jar 文件路径
- 是否有源码仓库
- Minecraft 版本、loader、mod 版本
- 是否有依赖 mod、整合包上下文、配置文件
- 用户需要的输出深度：快速简介、完整内容文档、机制追踪、面向玩家攻略、面向策划拆解

若用户只给了 jar，直接进入静态清单和反编译。不要因为缺源码而停下。

### 阶段 1：静态清单盘点

优先运行：

```bash
python mod-analyzer-skill/scripts/inspect_mod_jar.py <path-to-mod.jar> -o output/mod-analysis/<modid-or-jar-name>/mod_inventory.json
```

读取 `mod_inventory.json`，先建立全局地图：
- loader 与元数据：`fabric.mod.json`、`quilt.mod.json`、`META-INF/mods.toml`、`META-INF/neoforge.mods.toml`
- mod id、名称、版本、依赖、entrypoints
- class 包分布与可疑核心包
- mixin 配置与注入目标
- lang key、tooltip、item/block/entity 名称
- recipes、tags、loot tables、advancements、worldgen、structures、dimensions
- GUI/texture/model/sound/particle 等资源
- Patchouli、GuideMe 或其他内置手册内容

这一阶段的目标是"知道从哪里开始读"，不是直接下结论。

### 阶段 2：反编译或读取源码

优先级：
1. 如果用户提供源码，直接读源码，并用 jar 静态清单校验资源完整性。
2. 如果只有 jar，尝试使用本地可用反编译器：Vineflower / CFR / FernFlower。详细命令参考 `references/decompilation-workflow.md`。
3. 如果不能下载反编译器或反编译失败，使用 `javap`、class 路径、资源文件、lang、recipes、mixin 先做不完整分析，并明确限制。

反编译后优先定位：
- 主 mod 类与初始化入口
- Registry / DeferredRegister / Registry.register 调用
- 事件监听器、tick、交互、实体行为、能力/组件/附件
- RecipeSerializer、Menu、Screen、BlockEntity、Container、Network packet
- Config、gamerule、datapack reload listener
- Mixin 注入点及其改变的原版行为

### 阶段 3：建立"内容清单"而非"文件清单"

把代码与资源归并成玩家可理解的内容模块。推荐表格：

| 内容模块 | 玩家可见内容 | 证据文件/类 | 入口方式 | 与核心循环关系 | 置信度 |
|---|---|---|---|---|---|
| 新矿物/材料 | 采集 X 获得 Y | `data/.../worldgen`、`ModItems` | 世界生成/掉落 | 提供基础投入 | 高 |
| 机器/工作站 | 使用 A 处理 B | `BlockEntity`、`Menu`、recipes | 合成后放置交互 | 转换资源 | 高 |
| 事件机制 | 满足条件触发效果 | Event/Mixin class | 攻击/右键/tick | 改变风险与收益 | 中 |

注意：同一个类可能对应多个玩法，同一个玩法也可能跨多个类和 JSON。

### 阶段 4：抽取核心玩法循环

从"玩家反复做什么"而不是"mod 注册了什么"出发。至少输出：

```text
触发/入口 → 资源获取 → 加工/交互 → 风险或限制 → 奖励/产出 → 解锁/升级 → 回到下一轮
```

为每个环节标注证据：
- 触发/入口：advancement、guide book、item tooltip、配方、自然生成、事件、命令、配置
- 资源获取：worldgen、loot、mob drop、crafting、trading、API integration
- 加工/交互：recipe type、BlockEntity、Menu、right-click/use、entity interaction、tick logic
- 风险或限制：耐久、能量、冷却、维度、结构、biome、NBT、权限、配置项、依赖 mod
- 奖励/产出：物品、能力、状态、世界变化、解锁 recipe、advancement
- 回流：产物如何成为下一轮投入

如果 mod 没有强循环，而是工具箱/装饰/辅助型，也要说明其"弱循环"或"使用场景循环"。

### 阶段 5：细粒度流程追踪

至少选择 3 条流程，除非 mod 极小：
1. **新手入口流程**：玩家如何第一次发现并开始使用 mod。
2. **核心循环流程**：最能代表 mod 的完整玩法链。
3. **复杂/隐藏/高风险流程**：mixin、事件、世界生成、boss、维度、GUI、自动化、跨 mod 交互等。

每条流程使用固定结构：

```markdown
### 流程 N：[流程名]

**玩家目标**：[玩家想达成什么]
**入口条件**：[物品/配方/维度/事件/配置/依赖]
**证据链**：[关键 JSON、类、方法、lang key、recipe id]

| 步骤 | 玩家动作 | 系统判定/代码路径 | 资源/消耗 | 结果反馈 | 失败或限制 |
|---|---|---|---|---|---|
| 1 | ... | `Class#method` / `data/...json` | ... | ... | ... |

**流程设计判断**：[这条流程是否顺、是否有黑箱、是否形成循环]
**不确定项**：[需要实测或依赖外部 mod 的部分]
```

### 阶段 6：输出模组游戏内容文档

最终报告使用 `references/report-template.md` 的结构。必须包含：
- 模组简介
- 基础信息与分析范围
- 主要玩法总览
- 内容模块清单
- 核心玩法循环
- 细粒度模组流程
- 关键系统拆解
- 玩家进度建议或上手路径
- 配置、兼容、依赖与边界条件
- 证据索引
- 不确定项与需实测项

### 分析类型分支：模组设计分析

如果用户需要**模组设计分析**，在完成阶段 0~2 后，按照以下流程：

1. **完成阶段 3~6 的基础内容清单和玩法循环**（这是设计判断的证据基础）
2. **按 `references/design-analysis-template.md` 结构输出报告**
3. 核心判断维度：
   - 设计目标是什么？是否达成？
   - 核心玩法循环是否完整、闭合、有反馈？
   - 新手引导是否有效？是否存在黑箱机制？
   - 内容组织是否有清晰的阶段划分？
   - 难度曲线是否平滑？是否存在重复劳动/等待/黑箱成本？
   - 跨 mod 兼容和整合包定制空间如何？
4. 评分采用 80 分制（设计目标清晰度 10 + 核心循环完整性 15 + 新手引导 15 + 内容组织 10 + 难度平衡 10 + 整合包友好度 10 + 创新性 10）
5. **所有评分必须有具体代码/资源证据支撑**，禁止无依据打分

### 分析类型分支：模组代码分析

如果用户需要**模组代码分析**，在完成阶段 0~2 后，按照以下流程：

1. **完成阶段 1（静态清单）**，获取完整的 jar 清单和资源索引
2. **完成阶段 2（反编译/源码读取）**，确保关键类可读
3. 按 `references/code-analysis-template.md` 结构输出报告
4. 核心梳理维度：
   - 注册架构：DeferredRegister / Registry.register 如何组织？
   - Mixin：注入点、目标、修改逻辑、兼容风险
   - 事件系统：监听哪些事件？全局还是条件触发？
   - 网络包：同步什么数据？CS 如何分工？
   - 配置系统：哪些参数可调？默认值？
   - 数据驱动：哪些可通过 datapack 修改？哪些硬编码？
   - API/扩展点：是否提供公开 API？
   - 魔改指导：KubeJS/CraftTweaker 示例、配置推荐、修改策略
5. **报告面向开发者/魔改作者**，必须提供具体的魔改示例代码

### 分析类型分支：物品深度分析（LLM 物品数据库）

如果用户需要**物品深度分析**（为 LLM/AI 工具生成结构化的 JSON 物品数据库），在完成阶段 0~2 后，按照以下流程：

1. **完成阶段 1（静态清单）和阶段 2（反编译/源码读取）**，确保 lang 文件和配方数据可用
2. **提取物品和方块清单**：
   - 从 `assets/<modid>/lang/en_us.json` 提取所有 `item.<modid>.xxx` 和 `block.<modid>.xxx` 键
   - 从 `assets/<modid>/lang/zh_cn.json`（或其他中文 lang）获取中文名称
   - 从 `assets/<modid>/lang/en_us.json` 提取每个物品的 tooltip（`tooltip.summary` 和 `tooltip.behaviour/condition`）
3. **解析所有配方文件**（`data/<modid>/recipes/`）：
   - 遍历所有 JSON 配方，提取输入物品和输出物品
   - 识别配方类型（crafting_shaped/shapeless、create:crushing、create:mixing 等），映射到对应机器
   - 建立双向索引：`产出物品 → 配方列表`（用于 how_to_obtain）和 `消耗物品 → 配方列表`（用于 used_in_recipes）
4. **加载标签系统**（`data/<modid>/tags/items/` 及 `data/forge/tags/items/`）：
   - 建立 `tag_id → [物品列表]` 的映射
   - 用标签解析配方中的 tag 引用（如 `#forge:plates/brass` → `create:brass_sheet`）
   - 递归解析嵌套标签引用
5. **生成物品功能描述**：
   - 读取注册源码（如 `AllItems.java`、`AllBlocks.java`），获取每个物品的 Java 类名、稀有度、分类
   - 对基础材料：说明用途（基于 used_in_recipes 列出被用于合成什么）
   - 对工具/装备：结合 tooltip behaviours 生成详细用法说明
   - 对机器方块：基于其处理的配方类型和输入输出关系，说明工作机制
   - 对半成品：说明在什么流程中使用
6. **构建系统关系图**：
   - 将物品/方块按子系统分组（动力系统、流体系统、物品运输系统、加工系统、动态结构系统、铁路系统等）
   - 为每个子系统编写描述和核心流程
7. **输出 JSON 数据库**：
   - 输出到 `output/extracted-data/<modid>_items_deep.json`
   - 每条条目包含：type、name_en、name_zh、category、rarity、tooltip、how_to_obtain（按机器分组的配方）、used_in_recipes（使用此物品的配方）、function（详细功能描述）
   - 顶层包含：mod_info、systems（子系统定义）、total_entries、entries

**JSON 结构示例**：
```json
{
  "mod_info": { "name": "...", "id": "...", "version": "..." },
  "systems": {
    "动力系统": {
      "description": "...",
      "core_flow": "...",
      "source_blocks": [...],
      "transmission_blocks": [...],
      "processing_blocks": [...]
    }
  },
  "entries": {
    "create:wrench": {
      "type": "item",
      "name_en": "Wrench",
      "name_zh": "扳手",
      "category": "工具",
      "tooltip": { "summary_en": "...", "behaviours": [...] },
      "how_to_obtain": {
        "crafting_table": [{ "recipe_id": "...", "inputs": [...] }]
      },
      "used_in_recipes": [
        { "recipe_id": "...", "machine": "...", "outputs": [...] }
      ],
      "function": "Wrench（扳手）是机械动力的工具..."
    }
  }
}
```

**关键要求**：
- 所有功能描述必须有证据支撑（lang、recipe、tag 或源码）
- 配方中的 tag 引用必须通过标签系统解析为具体物品
- 生成的 JSON 必须能让其他 LLM 仅凭此文件就准确理解每个物品的完整语义

## Loader 专项探查路径

### Forge / NeoForge

优先查：
- `META-INF/mods.toml` 或 `META-INF/neoforge.mods.toml`
- `@Mod("modid")` 主类
- `DeferredRegister`、`RegistryObject`、`BuiltInRegistries`、`ForgeRegistries`
- `@SubscribeEvent`、`IEventBus`、`EventBusSubscriber`
- `BlockEntityType`、`MenuType`、`RecipeSerializer`、`LootModifier`
- Capability / Attachment / DataComponent / SavedData
- ConfigSpec、server/common/client config
- Network channel、payload、packet handler

### Fabric / Quilt

优先查：
- `fabric.mod.json` / `quilt.mod.json`
- `entrypoints.main`、`client`、`server`
- `ModInitializer`、`ClientModInitializer`
- `Registry.register`、`Registries.*`
- Fabric API callbacks、events、resource reload listener
- Component API、cardinal components、data attachments
- `ScreenHandler`、`HandledScreen`、networking payload
- access widener、mixin 配置

### Mixin

Mixin 往往定义"这个 mod 真正改变了什么"。必须记录：
- mixin 配置文件路径
- mixin 类名
- target class
- injection method / field / redirect / overwrite
- 玩家可见影响
- 风险：是否改变原版基础行为、是否依赖其他 mod、是否可能造成兼容问题

## 常见内容类型检查表

分析时逐项排查，不要漏掉数据驱动内容：
- 物品、方块、流体、实体、生物群系、维度、结构、特征/worldgen
- 配方：crafting、smelting、stonecutting、自定义 recipe type、机器配方
- 战斗：武器、护甲、附魔、状态效果、伤害类型、AI、boss、掉落
- 经济/交易：村民交易、商店、货币、战利品池、稀有掉落
- 进度：advancement、成就、guide book、任务集成、recipe unlock
- UI：菜单、屏幕、按钮、tooltip、JEI/REI/EMI 集成
- 自动化：方块实体 tick、能量/流体/物品 IO、红石、管道兼容
- 世界：矿物生成、结构、维度、传送、biome modifier、spawn rules
- 配置：难度、倍率、开关、黑名单/白名单、客户端显示
- 跨 mod：Curios/Trinkets、Patchouli、GeckoLib、Architectury、Create、Botania、AE2 等集成

## 输出语气

- 默认中文，冷静、证据优先、面向玩家体验和内容设计。
- 少写空洞赞美，少写"这个 mod 很有趣"这种不可复核判断。
- 对不确定内容明确标注"推断""未证实""需要实测"。
- 把类名、方法名、JSON 路径放在证据里，不要让正文变成代码索引。

## 子文档

- `scripts/inspect_mod_jar.py`：jar 静态清单生成脚本
- `scripts/decompile_mod_jar.py`：调用本地 CFR/Vineflower/FernFlower 反编译器的辅助脚本
- `scripts/generate_item_deep_analysis.py`：物品深度分析（LLM 物品数据库）生成脚本模板
- `references/decompilation-workflow.md`：反编译与代码探查流程（含详细命令和 loader 入口识别路径）
- `references/report-template.md`：游戏内容文档模板
- `references/design-analysis-template.md`：模组设计分析报告模板
- `references/code-analysis-template.md`：模组代码分析报告模板
