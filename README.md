# mod-analyzer-skill

用于分析 Minecraft Mod / 模组的 Claude 技能。

## 概述

本技能将 Minecraft Mod 的 jar / 源码 / 数据包资源转译成玩家与策划能读懂的"模组游戏内容文档"。它先静态检查 jar，再按 loader 元数据、数据包资源、lang、recipe、registry、event、mixin、GUI、网络包与配置追踪证据，避免只凭类名猜测。

## 目录结构

```
mod-analyzer-skill/
├── SKILL.md                          # 主技能文件（YAML frontmatter + 核心指令）
├── scripts/
│   ├── inspect_mod_jar.py            # jar 静态清单生成脚本
│   └── decompile_mod_jar.py          # 反编译辅助脚本（支持 Vineflower/CFR）
├── references/
│   ├── decompilation-workflow.md     # 反编译与代码探查流程
│   └── report-template.md            # 最终内容文档模板
├── evals/
│   └── evals.json                    # 测试用例
└── assets/                           # 输出模板（预留）
```

## 六阶段工作流

1. **阶段 0：确定输入与边界** — 确认 jar/源码/版本/loader/输出深度
2. **阶段 1：静态清单盘点** — 运行 `inspect_mod_jar.py` 建立全局地图
3. **阶段 2：反编译或读取源码** — 使用 Vineflower/CFR 或直接读源码
4. **阶段 3：建立内容清单** — 将代码与资源归并为玩家可理解的内容模块
5. **阶段 4：抽取核心玩法循环** — 从玩家反复做什么出发定义循环
6. **阶段 5：细粒度流程追踪** — 至少 3 条流程（新手入口/核心循环/复杂隐藏）
7. **阶段 6：输出模组游戏内容文档** — 按 `report-template.md` 结构输出

## 使用场景

- 分析 Minecraft Mod、mod jar、整合包里的某个 mod
- 反编译 Forge/NeoForge/Fabric/Quilt mod 并理解玩法
- 从源码或 jar 中梳理物品、方块、配方、世界生成、生物、维度、GUI 等
- 输出模组简介、主要玩法、核心玩法循环、细粒度玩家流程、内容设计文档

## 绝对原则

- 先证据，后结论
- 不要运行不可信 jar
- 不要只凭名称猜玩法
- 数据驱动和代码驱动同等重要
- 把代码路径翻译成玩家路径
- 不确定就标注不确定

## 依赖

- Python 3
- Java Runtime（用于运行 Java 反编译器）
- 推荐：Vineflower / CFR / FernFlower 反编译器 jar
