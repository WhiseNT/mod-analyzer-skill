# Minecraft Mod 反编译与代码探查流程

> 目标：从 jar / 源码中还原“玩家会遇到什么玩法”，而不是只列 Java 类。所有命令都应把 jar 当成输入文件读取，不执行 jar 内代码。

## 1. 输入整理

为每个 mod 建一个独立工作目录：

```text
output/mod-analysis/<modid-or-jar-name>/
├── mod.jar                      # 如需要，可复制原 jar 到这里
├── mod_inventory.json
├── decompiled/
├── source/                      # 如果用户提供源码，可放引用或说明路径
├── notes/
└── 模组游戏内容文档-<mod-name>.md
```

先运行静态清单：

```bash
python mod-analyzer-skill/scripts/inspect_mod_jar.py <mod.jar> -o output/mod-analysis/<modid>/mod_inventory.json
```

静态清单回答：loader 是什么、mod id 是什么、入口在哪里、资源/配方/语言文件/世界生成在哪里。

如果已经有本地 CFR / Vineflower / FernFlower jar，可用包装脚本执行反编译：

```bash
python mod-analyzer-skill/scripts/decompile_mod_jar.py <mod.jar> -o output/mod-analysis/<modid>/decompiled --decompiler-jar <decompiler.jar>
```

如果本地没有反编译器，且网络访问允许，用包装脚本从 Maven Central 显式下载并缓存 Vineflower：

```bash
python mod-analyzer-skill/scripts/decompile_mod_jar.py <mod.jar> -o output/mod-analysis/<modid>/decompiled --download-decompiler vineflower
```

需要 CFR 时改为：

```bash
python mod-analyzer-skill/scripts/decompile_mod_jar.py <mod.jar> -o output/mod-analysis/<modid>/decompiled --download-decompiler cfr
```

## 2. 反编译器选择

优先使用可用工具。若工具缺失且允许联网，应先尝试 `--download-decompiler vineflower`；若不能下载或下载失败，再做资源与 class 级分析，并在报告里标注反编译限制。

### Vineflower / FernFlower

适合现代 Java 字节码，输出较可读。Vineflower 是当前推荐默认项；IDEA 内置反编译器本质上也是 FernFlower 系工具链的 IDE 集成，但其插件路径和命令行入口不稳定，不应作为 skill 的默认 requirement。若用户明确提供 IDEA/FernFlower jar 路径，可以通过 `--decompiler-jar` 使用。

```bash
java -jar <vineflower-or-fernflower.jar> <mod.jar> output/mod-analysis/<modid>/decompiled
```

如工具支持参数，可开启保留泛型、行号、内部类等选项。

### CFR

常用于单 jar 反编译，适合快速得到源码目录。

```bash
java -jar <cfr.jar> <mod.jar> --outputdir output/mod-analysis/<modid>/decompiled --caseinsensitivefs true
```

Windows 上如果遇到大小写文件冲突，保留 `--caseinsensitivefs true`。

### javap 后备方案

没有反编译器时，用 `javap` 看类签名和常量池：

```bash
javap -classpath <mod.jar> -public <package.ClassName>
javap -classpath <mod.jar> -verbose <package.ClassName>
```

`javap` 不足以还原完整逻辑，但能帮助定位方法名、字段名、注解、字符串常量、mixin target。

## 3. 先读 loader 入口

### Fabric / Quilt

从 `fabric.mod.json` / `quilt.mod.json` 读：
- `entrypoints.main`
- `entrypoints.client`
- `entrypoints.server`
- `mixins`
- `accessWidener` / `access_widener`
- `depends` / `breaks`

反编译后优先打开 entrypoint 类：
- `ModInitializer#onInitialize`
- `ClientModInitializer#onInitializeClient`
- server entrypoint

关键搜索词：
- `Registry.register`
- `Registries.`
- `Identifier` / `ResourceLocation`
- `UseBlockCallback`、`AttackEntityCallback`、Fabric events
- `ScreenHandlerRegistry`、`HandledScreens`
- `ServerPlayNetworking`、`ClientPlayNetworking`
- `ResourceManagerHelper`
- `LootTableEvents`、`BiomeModifications`

### Forge / NeoForge

从 `META-INF/mods.toml` / `META-INF/neoforge.mods.toml` 读：
- `modId`
- `displayName`
- `version`
- `dependencies`
- loader 版本范围

反编译后优先搜索：
- `@Mod("modid")`
- `DeferredRegister`
- `RegistryObject`
- `ForgeRegistries` / `BuiltInRegistries`
- `IEventBus`
- `@SubscribeEvent`
- `@EventBusSubscriber`
- `FMLCommonSetupEvent`、`FMLClientSetupEvent`
- `RegisterEvent`
- `ModConfigSpec`
- `SimpleChannel`、payload、packet handler

NeoForge 新版本还要注意：
- data components
- attachments
- payload registrar
- `NeoForge.EVENT_BUS`
- `DeferredRegister.Blocks` / `DeferredRegister.Items`

## 4. 注册系统探查顺序

不要只读主类。按照“玩家可见内容”建立索引：

1. **Items / Blocks / Fluids**
   - 读注册类，列出 id、名称、属性、creative tab。
   - 对每个核心物品追踪：tooltip、use/useOn、inventoryTick、hurtEnemy、appendHoverText。

2. **BlockEntity / Machine / Menu / Screen**
   - 先看注册，再看 BlockEntity tick、capability/IO、recipe lookup。
   - Menu 决定槽位、按钮、同步数据；Screen 决定玩家看到的 UI。

3. **Recipes / RecipeSerializer / RecipeType**
   - JSON recipes 给出内容数量。
   - RecipeSerializer/Type 给出自定义工序逻辑。
   - 若有 JEI/REI/EMI 插件，读它可快速理解作者如何解释配方。

4. **Entities / AI / Combat**
   - 实体注册、属性、spawn placement、AI goals、damage sources、loot tables。
   - Boss/特殊实体要追踪阶段切换、技能冷却、掉落、成就。

5. **Worldgen / Structures / Dimensions**
   - `data/*/worldgen/**`、biome modifiers、configured/placed features。
   - 结构入口、传送逻辑、维度类型、noise settings。

6. **Events / Tick / Interaction**
   - 右键、攻击、方块破坏、实体死亡、玩家 tick、世界 tick、登录、维度切换。
   - 这些通常是“隐藏玩法”或“全局规则改变”的来源。

7. **Mixin**
   - 每个 mixin 都要转译为玩家可见影响：改变掉落、改变移动、改变合成、改变 AI、改变 UI、改变世界生成等。
   - 对 `@Redirect`、`@ModifyVariable`、`@Overwrite` 特别谨慎，因为它们可能大幅改变原版逻辑。

8. **Config**
   - 配置项可能决定机制是否启用、倍率、黑名单、维度限制、生成概率。
   - 报告中不要把默认关闭的玩法写成必然存在。

## 5. 数据资源探查顺序

### lang

`assets/<namespace>/lang/*.json` 是玩家可见内容索引。优先抓：
- `item.*`
- `block.*`
- `entity.*`
- `effect.*`
- `tooltip.*`
- `gui.*`
- `advancement.*`
- `message.*`

lang 可以帮助识别隐藏系统、手册章节、UI 按钮、错误提示。

### recipes

从 `data/<namespace>/recipes/**/*.json` 识别：
- 玩家最早能合成什么
- 哪些材料反复出现
- 是否存在机器配方/自定义 recipe type
- 产物如何成为下一轮投入

### tags

tags 代表兼容和分组，不要忽略：
- `items/`：材料替代、矿辞兼容
- `blocks/`：可挖掘、可交互、结构组成
- `entity_types/`：生物分类、事件过滤
- `biomes/`：生成限制

### loot tables

loot tables 决定资源获取和战斗奖励：
- 方块掉落
- 生物掉落
- 战利品箱
- 稀有概率与条件

### advancements / guide book

advancements 和手册通常是作者预期的新手路径：
- parent 链就是进度链
- criteria 是实际触发条件
- display 文本是作者给玩家的解释
- rewards 可能解锁 recipes、函数或战利品

Patchouli / GuideMe / 自定义手册优先阅读目录与入门页。

## 6. 证据笔记格式

建议在探查时用以下格式记录：

```markdown
## 证据：[系统或流程名]

- 玩家可见现象：...
- 入口线索：`assets/<modid>/lang/zh_cn.json` key `item.<modid>.xxx`
- 注册证据：`.../ModItems.java` -> `XXX_ITEM`
- 数据证据：`data/<modid>/recipes/xxx.json`
- 行为证据：`.../XxxItem.java#use` / `.../XxxBlockEntity.java#tick`
- UI/反馈：`.../XxxScreen.java` / lang key / sound / particle
- 限制条件：config、dimension、biome、NBT、cooldown、energy、dependency
- 推断：...
- 不确定项：...
```

## 7. 混淆与反编译失败处理

如果反编译结果有大量 `m_12345_`、`f_12345_` 或局部变量丢失：
- 先依靠 loader 元数据、lang、recipes、tags、loot、advancements 建立内容框架。
- 用字符串常量搜索定位 tooltip、message、GUI、id。
- 用注册 id 反查类：搜索 `"modid:item_name"`、`ResourceLocation("modid"`、`Identifier.of("modid"`。
- 对 mixin 目标使用 config 中的 target class 和方法名；不要过度解释看不懂的注入。
- 报告里把对应流程置信度降为“中/低”，列入需实测项。

## 8. 从代码到玩法的转换规则

- `Item#use` / `useOn`：玩家右键物品或方块触发的主动玩法。
- `Block#use` / `onUse`：玩家右键方块触发。
- `BlockEntity#tick`：机器、自动化、持续加工、能量/流体/物品 IO。
- `Entity#tick` / AI goals：生物行为、boss 阶段、战斗循环。
- Event handler：全局规则、被动触发、隐藏奖励/惩罚。
- Recipe JSON：玩家投入与产出。
- Loot table：资源获取方式与概率。
- Advancement criteria：作者认可的进度节点。
- Config：默认体验边界。
- Mixin：原版规则或其他 mod 行为被改写。

每个“玩法”最好同时有至少两类证据，例如注册类 + 配方，或者 lang + 事件代码。只有单一证据时标注置信度。
