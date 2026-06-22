#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用模组物品深度分析生成器模板
Generate deep item analysis with recipes, functions, and system relationships.

=== 使用说明 ===
此脚本是 mod-analyzer-skill 的"物品深度分析"工作流的参考实现。
它以通用模组分析为目标，生成包含配方、标签解析、功能描述和系统关系的
结构化 JSON 物品数据库，供其他 LLM/AI 工具直接消费。

=== 通用化说明 ===
要适配任何 Mod，需要修改以下路径变量和规则：
1. BASE → 指向目标 mod 的反编译输出目录
2. RECIPES_DIR → 指向目标 mod 的 recipes 目录
3. LANG_EN / LANG_ZH → 指向目标 mod 的语言文件
4. ITEM_CLASS_MAP → 根据目标 mod 的注册代码更新物品分类
5. SYSTEMS → 根据目标 mod 的子系统定义更新系统关系
6. functional_block_ids → 根据目标 mod 的功能方块更新
7. OUTPUT → 输出路径
8. generate_block_function() 内的方块特定描述 → 根据目标 mod 的方块机制更新
9. 如存在魔改层，还要合并 kubejs / crafttweaker / 补丁脚本扫描结果

=== 核心流程 ===
1. 加载语言文件 → 获取名称和 tooltip
2. 加载标签系统 → 解析 tag 引用
3. 遍历所有配方 → 建立输入/输出双向索引
4. 读取注册源码 → 获取物品分类和稀有度
5. 生成功能描述 → 基于工具提示、配方、用途自动生成
6. 构建系统关系 → 将物品按子系统分组
7. 输出 JSON → 写入结构化数据库

=== 输出结构 ===
参见 SKILL.md 中的"分析类型分支：物品深度分析"章节
"""

import json
import os
import re
from collections import Counter, defaultdict

MODID = os.environ.get('MODID', '<modid>')
BASE = os.environ.get('BASE', r'output/mod-analysis/<mod-name>/decompiled')
RECIPES_DIR = os.environ.get('RECIPES_DIR', rf'{BASE}/data/{MODID}/recipes')
LANG_EN = os.environ.get('LANG_EN', rf'{BASE}/assets/{MODID}/lang/en_us.json')
LANG_ZH = os.environ.get('LANG_ZH', rf'{BASE}/assets/{MODID}/lang/zh_cn.json')
OUTPUT = os.environ.get('OUTPUT', r'output/extracted-data/<mod-name>_items_deep.json')
SCRIPT_SCAN_DIRS = [p.strip() for p in os.environ.get('SCRIPT_SCAN_DIRS', '').split(';') if p.strip()]
SOURCE_SCAN_ROOT = os.environ.get('SOURCE_SCAN_ROOT', BASE)

# ====== Load lang ======
with open(LANG_EN, 'r', encoding='utf-8') as f:
    LANG_EN_DATA = json.load(f)
with open(LANG_ZH, 'r', encoding='utf-8') as f:
    LANG_ZH_DATA = json.load(f)


def collect_namespaced_keys(data, prefix):
    ids = []
    head = f'{prefix}.{MODID}.'
    for key in data:
        if key.startswith(head) and key.count('.') == 2:
            ids.append(key[len(head):])
    return sorted(set(ids))


def normalize_label(text):
    if not text:
        return ''
    return re.sub(r'\s+', ' ', str(text)).strip()


def derive_tokens(text):
    text = normalize_label(text)
    if not text:
        return []
    tokens = re.findall(r'[A-Za-z][A-Za-z0-9_\-]{2,}', text)
    return [t.lower() for t in tokens if len(t) >= 3]

def lang_name(prefix, item_id):
    return LANG_EN_DATA.get(f'{prefix}.{item_id}', '')

def lang_zh(prefix, item_id):
    return LANG_ZH_DATA.get(f'{prefix}.{item_id}', '')

def tooltip(prefix, item_id):
    base = f'{prefix}.{item_id}'
    summary_en = LANG_EN_DATA.get(f'{base}.tooltip.summary', '')
    summary_zh = LANG_ZH_DATA.get(f'{base}.tooltip.summary', '')
    behaviours = []
    for i in range(1, 10):
        cond_en = LANG_EN_DATA.get(f'{base}.tooltip.condition{i}', '')
        beh_en = LANG_EN_DATA.get(f'{base}.tooltip.behaviour{i}', '')
        cond_zh = LANG_ZH_DATA.get(f'{base}.tooltip.condition{i}', '')
        beh_zh = LANG_ZH_DATA.get(f'{base}.tooltip.behaviour{i}', '')
        if cond_en or beh_en:
            behaviours.append({
                'condition_en': cond_en, 'behaviour_en': beh_en,
                'condition_zh': cond_zh, 'behaviour_zh': beh_zh
            })
    return {'summary_en': summary_en, 'summary_zh': summary_zh, 'behaviours': behaviours}

# ====== Load tag system for resolving tag references ======
print("Loading tag system...")
TAG_ITEM_MAP = {}  # "forge:plates/brass" -> ["examplemod:brass_sheet", ...]
for tag_ns in ['forge', 'minecraft', MODID]:
    tag_dir = rf'{BASE}/data/{tag_ns}/tags/items'
    if os.path.isdir(tag_dir):
        for root, dirs, files in os.walk(tag_dir):
            for fname in files:
                if not fname.endswith('.json'):
                    continue
                try:
                    with open(os.path.join(root, fname), 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    rel = os.path.relpath(os.path.join(root, fname), tag_dir).replace('\\', '/').replace('.json', '')
                    tag_id = f'{tag_ns}:{rel}'
                    values = data.get('values', [])
                    TAG_ITEM_MAP[tag_id] = values
                except:
                    pass

def resolve_tag(tag_id):
    """Resolve a tag reference to concrete item IDs"""
    items = TAG_ITEM_MAP.get(tag_id, [])
    result = []
    for item in items:
        if isinstance(item, str):
            if item.startswith('#'):
                result.extend(resolve_tag(item[1:]))
            else:
                result.append(item)
        elif isinstance(item, dict) and 'id' in item:
            # Tags may have objects with "id" and "required" fields
            item_id = item['id']
            if item_id.startswith('#'):
                result.extend(resolve_tag(item_id[1:]))
            else:
                result.append(item_id)
    return result

# ====== Step 1: Parse all recipes ======
print("Step 1: Parsing recipes...")
recipe_output_index = defaultdict(list)   # item_id -> recipes that produce it
recipe_input_index = defaultdict(list)    # item_id -> recipes that consume it
recipe_machine_index = defaultdict(list)  # machine item_id -> recipes it processes
all_recipes = {}  # recipe_name -> recipe_data

RECIPE_TYPE_MAP = {
    # ========== 示例模板数据 ==========
    # 用户替换 MODID 后，需要根据目标 mod 的 recipe type 重新映射：
    # 键是 recipe 子目录名或 recipe type 的 namespace 名，
    # 值是"输出 JSON 中的 machine 字段名"（会用于 how_to_obtain 分组）
    'crafting': 'crafting_table',
    'crafting/kinetics': 'mechanical_crafter',
    'crushing': 'custom_processing',
    'milling': 'custom_processing',
    'compacting': 'custom_processing',
    'mixing': 'custom_processing',
    'pressing': 'custom_processing',
    'cutting': 'custom_processing',
    'blasting': 'furnace_blast',
    'smelting': 'furnace',
    'smoking': 'smoker',
    'campfire_cooking': 'campfire',
    'splashing': 'custom_processing',
    'haunting': 'custom_processing',
    'sandpaper': 'custom_processing',
    'deploying': 'custom_processing',
    'filling': 'custom_processing',
    'emptying': 'custom_processing',
    'stonecutting': 'stonecutter',
    'sequenced_assembly': 'custom_processing',
}

def extract_item_id(item_str):
    """Extract item ID from various recipe field formats"""
    if isinstance(item_str, str):
        # Could be "create:brass_ingot" or "minecraft:iron_ingot"
        return item_str
    if isinstance(item_str, dict):
        if 'item' in item_str:
            return item_str['item']
        if 'tag' in item_str:
            return f"#{item_str['tag']}"
    return None

def extract_items_from_ingredient(ing):
    """Extract item IDs from an ingredient field"""
    items = []
    if isinstance(ing, list):
        for i in ing:
            items.extend(extract_items_from_ingredient(i))
    elif isinstance(ing, dict):
        item = extract_item_id(ing)
        if item:
            items.append(item)
    return items

for root, dirs, files in os.walk(RECIPES_DIR):
    for fname in files:
        if not fname.endswith('.json'):
            continue
        fpath = os.path.join(root, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except:
            continue

        recipe_type = data.get('type', 'unknown')
        # Determine machine from path
        rel_path = os.path.relpath(fpath, RECIPES_DIR).replace('\\', '/')
        path_parts = rel_path.replace('.json', '').split('/')
        
        # Determine recipe category
        if path_parts[0] in RECIPE_TYPE_MAP:
            machine = RECIPE_TYPE_MAP[path_parts[0]]
        elif recipe_type == 'minecraft:crafting_shaped' or recipe_type == 'minecraft:crafting_shapeless':
            machine = 'crafting_table'
        elif recipe_type == 'create:mechanical_crafting':
            machine = 'mechanical_crafter'
        elif recipe_type == 'create:crushing':
            machine = 'crushing_wheel'
        elif recipe_type == 'create:milling':
            machine = 'millstone'
        elif recipe_type == 'create:compacting':
            machine = 'mechanical_press_basin'
        elif recipe_type == 'create:mixing':
            machine = 'mechanical_mixer_basin'
        elif recipe_type == 'create:pressing':
            machine = 'mechanical_press'
        elif recipe_type == 'create:cutting':
            machine = 'mechanical_saw'
        elif recipe_type == 'create:splashing':
            machine = 'fan_splash'
        elif recipe_type == 'create:haunting':
            machine = 'fan_haunt'
        elif recipe_type == 'create:sandpaper_polishing':
            machine = 'sand_paper'
        elif recipe_type == 'create:deploying':
            machine = 'deployer'
        elif recipe_type == 'create:filling':
            machine = 'spout'
        elif recipe_type == 'create:emptying':
            machine = 'item_drain'
        elif recipe_type == 'create:sequenced_assembly':
            machine = 'sequenced_assembly'
        else:
            machine = recipe_type

        # Extract input items
        inputs = []
        # Check key/pattern for shaped
        if 'key' in data:
            for k, v in data['key'].items():
                inputs.extend(extract_items_from_ingredient(v))
        # Check ingredients list
        if 'ingredients' in data:
            for ing in data['ingredients']:
                inputs.extend(extract_items_from_ingredient(ing))
        # Check ingredient (singular)
        if 'ingredient' in data:
            inputs.extend(extract_items_from_ingredient(data['ingredient']))
        # Check sequenced assembly steps
        if 'sequence' in data:
            for step in data['sequence']:
                if 'input' in step:
                    inputs.extend(extract_items_from_ingredient(step['input']))

        # Extract output items
        outputs = []
        if 'result' in data:
            r = data['result']
            if isinstance(r, str):
                outputs.append(r)
            elif isinstance(r, dict):
                outputs.append(r.get('item', ''))
                if 'results' in r:
                    for rr in r['results']:
                        outputs.append(rr.get('item', ''))
        if 'results' in data:
            for r in data['results']:
                if isinstance(r, str):
                    outputs.append(r)
                elif isinstance(r, dict):
                    outputs.append(r.get('item', ''))

        recipe_info = {
            'recipe_id': f"{MODID}:{rel_path.replace('.json', '')}",
            'type': recipe_type,
            'machine': machine,
            'inputs': inputs,
            'outputs': outputs
        }

        rname = rel_path.replace('.json', '')
        all_recipes[rname] = recipe_info

        # Build indexes - index items that belong to the target mod namespace
        # Also resolve tag references to find namespaced items
        resolved_inputs = []
        for inp in inputs:
            if inp.startswith(f'{MODID}:'):
                resolved_inputs.append(inp)
            elif inp.startswith('#'):
                resolved = resolve_tag(inp[1:])
                resolved_inputs.extend(resolved)
            elif ':' in inp:
                resolved_inputs.append(inp)

        for out in outputs:
            if out.startswith(f'{MODID}:'):
                oid = out.replace(f'{MODID}:', '')
                recipe_output_index[oid].append(recipe_info)

        for inp in resolved_inputs:
            if inp.startswith(f'{MODID}:'):
                iid = inp.replace(f'{MODID}:', '')
                recipe_input_index[iid].append(recipe_info)

        # Build machine index
        if machine not in ('crafting_table', 'stonecutter', 'furnace', 'furnace_blast', 'smoker', 'campfire'):
            for inp in inputs:
                if inp.startswith(f'{MODID}:'):
                    iid = inp.replace(f'{MODID}:', '')
                    recipe_machine_index[iid].append(recipe_info)

print(f"  Parsed {len(all_recipes)} recipes")
print(f"  Items with recipes: {len(recipe_output_index)}")
print(f"  Items used as material: {len(recipe_input_index)}")

# ====== Step 2: Pre-analyze key mod settings ======
print("Step 2: Pre-analyzing key mod settings...")

IMPORTANT_SETTINGS = {
    'core_resources': [],
    'core_systems': [],
    'progression_stages': [],
    'special_rules': [],
    'key_terms': [],
    'important_blocks': [],
    'important_items': [],
    'important_recipes': [],
    'evidence': {
        'lang': [],
        'recipes': [],
        'source': [],
        'scripts': [],
    },
}


def add_unique(bucket, value):
    value = normalize_label(value)
    if value and value not in bucket:
        bucket.append(value)


def scan_source_text_for_settings(text, label):
    if not text:
        return
    lower = text.lower()
    tokens = derive_tokens(text)
    for token in tokens[:40]:
        if token not in IMPORTANT_SETTINGS['key_terms']:
            IMPORTANT_SETTINGS['key_terms'].append(token)
    heuristics = [
        ('energy', '能量系统'),
        ('stress', '应力系统'),
        ('kinetic', '动能系统'),
        ('contraption', '动态结构'),
        ('fluid', '流体系统'),
        ('recipe', '配方系统'),
        ('registry', '注册系统'),
        ('mixin', 'Mixin 修改点'),
        ('progression', '进度阶段'),
        ('gating', '阶段门槛'),
        ('automation', '自动化'),
        ('transport', '运输系统'),
        ('tag', '标签系统'),
        ('tooltip', '说明文本系统'),
        ('event', '事件系统'),
        ('config', '配置系统'),
    ]
    for needle, item in heuristics:
        if needle in lower:
            add_unique(IMPORTANT_SETTINGS['core_systems'], item)
    for label_text in ['core', 'main', 'primary', 'special', 'advanced', 'tier', 'stage']:
        if label_text in lower:
            add_unique(IMPORTANT_SETTINGS['special_rules'], f'{label} contains {label_text}')


def scan_lang_for_settings():
    for key, value in LANG_EN_DATA.items():
        if not key.startswith(f'item.{MODID}.') and not key.startswith(f'block.{MODID}.'):
            continue
        if any(s in key for s in ['tooltip.summary', 'tooltip.behaviour', 'tooltip.condition']):
            scan_source_text_for_settings(str(value), key)
        if key.endswith('.name') or key.count('.') == 2:
            add_unique(IMPORTANT_SETTINGS['important_items'] if key.startswith(f'item.{MODID}.') else IMPORTANT_SETTINGS['important_blocks'], key.rsplit('.', 1)[-1])
            if isinstance(value, str):
                if len(value) >= 8:
                    add_unique(IMPORTANT_SETTINGS['key_terms'], value)


def scan_recipes_for_settings():
    recipe_counter = Counter()
    output_counter = Counter()
    input_counter = Counter()
    for recipe in all_recipes.values():
        recipe_counter[recipe['machine']] += 1
        for out in recipe.get('outputs', []):
            if isinstance(out, str) and out.startswith(f'{MODID}:'):
                output_counter[out.split(':', 1)[1]] += 1
        for inp in recipe.get('inputs', []):
            if isinstance(inp, str) and inp.startswith(f'{MODID}:'):
                input_counter[inp.split(':', 1)[1]] += 1
    for item_id, count in output_counter.most_common(25):
        add_unique(IMPORTANT_SETTINGS['important_items'], item_id)
        if count >= 2:
            add_unique(IMPORTANT_SETTINGS['core_resources'], item_id)
    for item_id, count in input_counter.most_common(20):
        if count >= 2:
            add_unique(IMPORTANT_SETTINGS['core_resources'], item_id)
    for machine, count in recipe_counter.most_common(20):
        add_unique(IMPORTANT_SETTINGS['core_systems'], machine)


def scan_scripts_for_settings():
    for root_dir in SCRIPT_SCAN_DIRS:
        if not os.path.isdir(root_dir):
            continue
        for root, _, files in os.walk(root_dir):
            for fname in files:
                if not fname.endswith(('.js', '.ts', '.json', '.zs', '.txt', '.mcfunction')):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        text = f.read()
                except OSError:
                    continue
                low = text.lower()
                if MODID.lower() not in low and not any(k in low for k in ['kubejs', 'crafttweaker', 'craftweak', 'recipe', 'event', 'tag', 'registry']):
                    continue
                IMPORTANT_SETTINGS['evidence']['scripts'].append(fpath)
                scan_source_text_for_settings(text, fpath)
                for pattern, bucket in [
                    (rf'{re.escape(MODID)}:[a-z0-9_./-]+', IMPORTANT_SETTINGS['important_items']),
                    (r'recipe[_\- ]id[:= ]+[A-Za-z0-9_:\./\-]+', IMPORTANT_SETTINGS['important_recipes']),
                ]:
                    for match in re.findall(pattern, text, flags=re.I):
                        add_unique(bucket, match)


def scan_source_for_settings():
    if not os.path.isdir(SOURCE_SCAN_ROOT):
        return
    for root, _, files in os.walk(SOURCE_SCAN_ROOT):
        for fname in files:
            if not fname.endswith(('.java', '.kt', '.json', '.mcmeta', '.toml', '.mixins.json', '.cfg')):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
            except OSError:
                continue
            if MODID.lower() not in text.lower() and not any(token in text.lower() for token in ['registry', 'mixin', 'recipe', 'tooltip', 'tag', 'event', 'config']):
                continue
            IMPORTANT_SETTINGS['evidence']['source'].append(fpath)
            scan_source_text_for_settings(text, fpath)
            for match in re.findall(rf'{re.escape(MODID)}:[a-z0-9_./-]+', text, flags=re.I):
                add_unique(IMPORTANT_SETTINGS['important_items'], match.split(':', 1)[1])
            for match in re.findall(r'block\.' + re.escape(MODID) + r'\.[A-Za-z0-9_./-]+', text, flags=re.I):
                add_unique(IMPORTANT_SETTINGS['important_blocks'], match.rsplit('.', 1)[-1])


scan_lang_for_settings()
scan_source_for_settings()
scan_scripts_for_settings()
scan_recipes_for_settings()

print("Pre-analysis summary:")
print(f"  core_resources={len(IMPORTANT_SETTINGS['core_resources'])}")
print(f"  core_systems={len(IMPORTANT_SETTINGS['core_systems'])}")
print(f"  key_terms={len(IMPORTANT_SETTINGS['key_terms'])}")
print(f"  important_items={len(IMPORTANT_SETTINGS['important_items'])}")
print(f"  important_blocks={len(IMPORTANT_SETTINGS['important_blocks'])}")

# Item categories based on registry/source analysis
ITEM_CLASS_MAP = {
    # Basic materials
    'andesite_alloy': {'class': 'Item', 'category': '材料'},
    'brass_ingot': {'class': 'Item', 'category': '材料'},
    'brass_nugget': {'class': 'Item', 'category': '材料'},
    'brass_sheet': {'class': 'Item', 'category': '材料'},
    'copper_nugget': {'class': 'Item', 'category': '材料'},
    'copper_sheet': {'class': 'Item', 'category': '材料'},
    'iron_sheet': {'class': 'Item', 'category': '材料'},
    'golden_sheet': {'class': 'Item', 'category': '材料'},
    'zinc_ingot': {'class': 'Item', 'category': '材料'},
    'zinc_nugget': {'class': 'Item', 'category': '材料'},
    'raw_zinc': {'class': 'Item', 'category': '材料'},
    'sturdy_sheet': {'class': 'Item', 'category': '材料'},
    'wheat_flour': {'class': 'Item', 'category': '材料'},
    'dough': {'class': 'Item', 'category': '材料'},
    'cinder_flour': {'class': 'Item', 'category': '材料'},
    'powdered_obsidian': {'class': 'Item', 'category': '材料'},
    'rose_quartz': {'class': 'Item', 'category': '材料'},
    'polished_rose_quartz': {'class': 'Item', 'category': '材料'},
    'electron_tube': {'class': 'Item', 'category': '材料'},
    'propeller': {'class': 'Item', 'category': '材料'},
    'whisk': {'class': 'Item', 'category': '材料'},
    'brass_hand': {'class': 'Item', 'category': '材料'},
    'crafter_slot_cover': {'class': 'Item', 'category': '材料'},
    'pulp': {'class': 'Item', 'category': '材料'},
    'cardboard': {'class': 'CombustibleItem', 'category': '材料'},
    'precision_mechanism': {'class': 'Item', 'category': '材料'},
    'incomplete_precision_mechanism': {'class': 'SequencedAssemblyItem', 'category': '半成品'},
    'unprocessed_obsidian_sheet': {'class': 'SequencedAssemblyItem', 'category': '半成品'},
    'incomplete_track': {'class': 'SequencedAssemblyItem', 'category': '半成品'},
    'chromatic_compound': {'class': 'ChromaticCompoundItem', 'category': '特殊材料', 'rarity': 'UNCOMMON'},
    'shadow_steel': {'class': 'ShadowSteelItem', 'category': '特殊材料', 'rarity': 'UNCOMMON'},
    'refined_radiance': {'class': 'RefinedRadianceItem', 'category': '特殊材料', 'rarity': 'UNCOMMON'},
    'experience_nugget': {'class': 'ExperienceNuggetItem', 'category': '材料', 'rarity': 'UNCOMMON'},

    # Crushed ores
    'crushed_raw_iron': {'class': 'Item', 'category': '粉碎矿石'},
    'crushed_raw_gold': {'class': 'Item', 'category': '粉碎矿石'},
    'crushed_raw_copper': {'class': 'Item', 'category': '粉碎矿石'},
    'crushed_raw_zinc': {'class': 'Item', 'category': '粉碎矿石'},

    # Tools
    'wrench': {'class': 'WrenchItem', 'category': '工具'},
    'goggles': {'class': 'GogglesItem', 'category': '工具'},
    'sand_paper': {'class': 'SandPaperItem', 'category': '工具'},
    'red_sand_paper': {'class': 'SandPaperItem', 'category': '工具'},
    'extendo_grip': {'class': 'ExtendoGripItem', 'category': '工具', 'rarity': 'UNCOMMON'},
    'potato_cannon': {'class': 'PotatoCannonItem', 'category': '工具'},
    'wand_of_symmetry': {'class': 'SymmetryWandItem', 'category': '工具', 'rarity': 'UNCOMMON'},
    'handheld_worldshaper': {'class': 'WorldshaperItem', 'category': '工具', 'rarity': 'EPIC'},
    'tree_fertilizer': {'class': 'TreeFertilizerItem', 'category': '工具'},
    'super_glue': {'class': 'SuperGlueItem', 'category': '工具'},
    'linked_controller': {'class': 'LinkedControllerItem', 'category': '工具'},
    'transmitter': {'class': 'Item', 'category': '工具'},
    'attribute_filter': {'class': 'FilterItem', 'category': '工具'},
    'filter': {'class': 'FilterItem', 'category': '工具'},
    'package_filter': {'class': 'FilterItem', 'category': '工具'},
    'crafting_blueprint': {'class': 'BlueprintItem', 'category': '工具'},
    'clipboard': {'class': 'Item', 'category': '工具'},

    # Equipment / Armor
    'copper_backtank': {'class': 'BacktankItem', 'category': '装备'},
    'netherite_backtank': {'class': 'BacktankItem.Layered', 'category': '装备'},
    'copper_diving_helmet': {'class': 'DivingHelmetItem', 'category': '装备'},
    'netherite_diving_helmet': {'class': 'DivingHelmetItem', 'category': '装备'},
    'copper_diving_boots': {'class': 'DivingBootsItem', 'category': '装备'},
    'netherite_diving_boots': {'class': 'DivingBootsItem', 'category': '装备'},
    'cardboard_helmet': {'class': 'CardboardHelmetItem', 'category': '装备'},
    'cardboard_chestplate': {'class': 'CardboardArmorItem', 'category': '装备'},
    'cardboard_leggings': {'class': 'CardboardArmorItem', 'category': '装备'},
    'cardboard_boots': {'class': 'CardboardArmorItem', 'category': '装备'},
    'cardboard_sword': {'class': 'CardboardSwordItem', 'category': '装备'},

    # Food
    'bar_of_chocolate': {'class': 'Item', 'category': '食物'},
    'sweet_roll': {'class': 'Item', 'category': '食物'},
    'chocolate_glazed_berries': {'class': 'Item', 'category': '食物'},
    'honeyed_apple': {'class': 'Item', 'category': '食物'},
    'builders_tea': {'class': 'BuildersTeaItem', 'category': '食物'},
    'blaze_cake': {'class': 'CombustibleItem', 'category': '燃料'},
    'blaze_cake_base': {'class': 'Item', 'category': '半成品'},
    'creative_blaze_cake': {'class': 'CombustibleItem', 'category': '燃料', 'rarity': 'EPIC'},

    # Logistics
    'belt_connector': {'class': 'BeltConnectorItem', 'category': '物流'},
    'vertical_gearbox': {'class': 'VerticalGearboxItem', 'category': '动力'},
    'empty_blaze_burner': {'class': 'BlazeBurnerBlockItem', 'category': '燃料'},
    'minecart_coupling': {'class': 'MinecartCouplingItem', 'category': '物流'},
    'minecart_contraption': {'class': 'MinecartContraptionItem', 'category': '物流'},
    'furnace_minecart_contraption': {'class': 'MinecartContraptionItem', 'category': '物流'},
    'chest_minecart_contraption': {'class': 'MinecartContraptionItem', 'category': '物流'},
    'schedule': {'class': 'ScheduleItem', 'category': '物流'},
    'shopping_list': {'class': 'ShoppingListItem', 'category': '物流'},
    'empty_schematic': {'class': 'Item', 'category': '工具'},
    'schematic_and_quill': {'class': 'SchematicAndQuillItem', 'category': '工具'},
    'schematic': {'class': 'SchematicItem', 'category': '工具'},
}

# ====== Step 3: Build system definitions ======
print("Step 3: Building system relationships...")

SYSTEMS = {
    # ========== 示例模板数据 ==========
    # 以下 SYSTEMS 包含 Create 机械动力的系统定义。
    # 适配其他 mod 时请根据该 mod 的实际子系统结构替换全部内容。
    '动力系统': {
        'description': '机械动力的核心系统。所有机器需要旋转动力（Kinetic Energy）才能工作。动力源产生旋转速度（RPM）和应力（Stress Units, SU）。传动组件传递、改变方向和转速。加工机器消耗应力来执行工作。当消耗超过产能时网络会过载停转。',
        'core_flow': '动力源 → 传动杆/齿轮/齿轮箱 → 转速/应力调节 → 加工机器',
        'source_blocks': [
            'create:hand_crank', 'create:water_wheel', 'create:large_water_wheel',
            'create:windmill_bearing', 'create:steam_engine', 'create:creative_motor',
            'create:furnace_minecart_contraption'
        ],
        'transmission_blocks': [
            'create:shaft', 'create:cogwheel', 'create:large_cogwheel',
            'create:gearbox', 'create:vertical_gearbox', 'create:belt',
            'create:encased_chain_drive', 'create:chain_conveyor',
            'create:adjustable_chain_gearshift', 'create:gearshift',
            'create:sequenced_gearshift', 'create:rotation_speed_controller',
            'create:clutch', 'create:powered_shaft',
            'create:andesite_encased_shaft', 'create:brass_encased_shaft',
            'create:andesite_encased_cogwheel', 'create:andesite_encased_large_cogwheel',
            'create:brass_encased_cogwheel', 'create:brass_encased_large_cogwheel',
            'create:metal_girder_encased_shaft',
            'create:gantry_shaft', 'create:gantry_carriage',
        ],
        'processing_blocks': [
            'create:millstone', 'create:crushing_wheel', 'create:crushing_wheel_controller',
            'create:mechanical_press', 'create:mechanical_mixer',
            'create:mechanical_drill', 'create:mechanical_saw',
            'create:mechanical_harvester', 'create:mechanical_plough', 'create:mechanical_roller',
            'create:encased_fan', 'create:nozzle',
            'create:mechanical_pump', 'create:mechanical_arm', 'create:deployer',
            'create:mechanical_crafter',
            'create:flywheel', 'create:steam_engine',
        ],
        'gauge_blocks': ['create:speedometer', 'create:stressometer'],
    },

    '物品运输系统': {
        'description': '负责在机器之间运输物品。传送带是最基础的运输方式，漏斗和隧道负责物品输入/输出/分流，动力臂可在较大范围内拾取/放置物品，溜槽用于垂直运输。',
        'core_flow': '漏斗/隧道输入 → 传送带运输 → 漏斗/隧道输出到机器 → 动力臂跨区域搬运',
        'components': [
            'create:belt', 'create:belt_connector',
            'create:andesite_funnel', 'create:andesite_belt_funnel', 'create:andesite_tunnel',
            'create:brass_funnel', 'create:brass_belt_funnel', 'create:brass_tunnel',
            'create:depot', 'create:weighted_ejector',
            'create:chute', 'create:smart_chute',
            'create:mechanical_arm', 'create:deployer',
            'create:item_vault', 'create:item_hatch',
        ],
    },

    '流体系统': {
        'description': '负责运输和处理流体（水、熔岩、以及 Create 添加的巧克力、蜂蜜、茶饮等）。动力泵产生压力推动流体在管道中流动。阀门控制通断，注液器将流体注入物品，分液池从物品中提取流体。软管滑轮可从无限水源/熔岩源大量抽取。',
        'core_flow': '泵 → 管道运输 → 阀门控制 → 注液器/分液池处理 → 储罐存储',
        'components': [
            'create:fluid_pipe', 'create:glass_fluid_pipe', 'create:encased_fluid_pipe',
            'create:smart_fluid_pipe', 'create:fluid_valve',
            'create:mechanical_pump',
            'create:fluid_tank', 'create:creative_fluid_tank',
            'create:spout', 'create:item_drain',
            'create:hose_pulley',
            'create:chocolate', 'create:honey',
        ],
    },

    '加工系统': {
        'description': '将原材料加工为更高级的材料和组件。核心流程：矿石→粉碎轮→粉碎矿石→清洗/熔炼→锭→冲压→板→组装→构件。加工机器需要接入动力网络才能工作。',
        'core_flow': '原材料 → 粉碎轮/石磨粉碎 → 鼓风机清洗/熔炼 → 熔炉冶炼 → 冲压机压板 → 动力合成器/序列组装 → 精密构件',
        'machines': {
            'crushing_wheel': {'input': '矿石/原材料', 'output': '粉碎矿石/余烬面粉', 'process': '粉碎'},
            'millstone': {'input': '小麦/矿石', 'output': '面粉/粉碎矿石', 'process': '研磨'},
            'mechanical_press': {'input': '锭/原料', 'output': '板/压缩产物', 'process': '冲压'},
            'mechanical_mixer': {'input': '多种材料', 'output': '合金/混合物', 'process': '搅拌'},
            'encased_fan_splash': {'input': '粉碎矿石', 'output': '粗矿/粒', 'process': '清洗'},
            'encased_fan_haunt': {'input': '普通物品', 'output': '下界/灵魂相关物品', 'process': '缠魂'},
            'encased_fan_smoke': {'input': '食物', 'output': '熏制食物', 'process': '烟熏'},
            'mechanical_saw': {'input': '木头/原木', 'output': '木板/台阶', 'process': '切割'},
            'mechanical_crafter': {'input': '多种材料', 'output': '机械合成产物', 'process': '动力合成'},
            'sand_paper': {'input': '玫瑰石英', 'output': '磨制玫瑰石英', 'process': '打磨'},
            'deployer': {'input': '方块+手持物品', 'output': '应用产物', 'process': '部署'},
            'spout': {'input': '物品+流体', 'output': '注液产物', 'process': '注液'},
            'item_drain': {'input': '含流体物品', 'output': '物品+流体', 'process': '排液'},
            'basin': {'input': '多物品/流体', 'output': '加工产物', 'process': '工作盆（与搅拌器/冲压机配合）'},
        },
    },

    '动态结构系统': {
        'description': '让方块组作为整体运动的系统。轴承使结构旋转，活塞使结构直线移动，滑轮使结构上下移动，底盘用于扩展装配范围。黏着器在移动时临时粘合方块。动态结构可以携带物品、流体、实体。',
        'core_flow': '轴承/活塞/滑轮 → 连接底盘 → 黏着器扩展 → 结构整体运动 → 与环境和物品交互',
        'components': [
            'create:mechanical_bearing', 'create:windmill_bearing', 'create:clockwork_bearing',
            'create:mechanical_piston', 'create:sticky_mechanical_piston',
            'create:piston_extension_pole',
            'create:rope_pulley', 'create:elevator_pulley', 'create:elevator_contact',
            'create:gantry_carriage', 'create:gantry_shaft',
            'create:linear_chassis', 'create:radial_chassis', 'create:secondary_linear_chassis',
            'create:sticker', 'create:super_glue',
            'create:cart_assembler', 'create:minecart_anchor',
            'create:portable_storage_interface', 'create:portable_fluid_interface',
            'create:contraption_controls', 'create:redstone_contact',
            'create:sail_frame',
            'create:minecart_contraption', 'create:chest_minecart_contraption',
            'create:furnace_minecart_contraption',
        ],
    },

    '铁路系统': {
        'description': '基于列车的运输系统。铺设轨道、设置站点、调度列车在站点间运输物品/流体/实体。列车可以装载多个转向架，支持信号机控制区间。',
        'core_flow': '铺设轨道 → 放置转向架 → 装配列车结构 → 设置站点 → 编写时刻表 → 列车自动运输',
        'components': [
            'create:track', 'create:track_station', 'create:track_signal', 'create:track_observer',
            'create:controller_rail',
            'create:small_bogey', 'create:large_bogey',
            'create:controls', 'create:schedule',
            'create:train_door', 'create:train_trapdoor',
            'create:steam_whistle', 'create:steam_whistle_extension',
            'create:railway_casing',
        ],
    },

    '红石/逻辑系统': {
        'description': '增强的红石元件。无线红石信号终端可在远距离传输信号，脉冲电路提供延时/计数功能，显示链接器可从方块读取数据并显示。',
        'core_flow': '信号源 → 无线红石传输 → 脉冲处理/延迟 → 控制机器',
        'components': [
            'create:redstone_link', 'create:redstone_contact', 'create:redstone_requester',
            'create:powered_latch', 'create:powered_toggle_latch',
            'create:pulse_extender', 'create:pulse_repeater', 'create:pulse_timer',
            'create:analog_lever', 'create:stockpile_switch', 'create:content_observer',
            'create:display_link', 'create:display_board', 'create:nixie_tube',
            'create:lectern_controller', 'create:linked_controller',
            'create:placard', 'create:desk_bell', 'create:haunted_bell', 'create:peculiar_bell',
            'create:turntable',
            'create:stock_link', 'create:stock_ticker',
            'create:factory_gauge',
        ],
    },

    '打包系统': {
        'description': '基于地址的物品邮寄系统。将物品封装为包裹，通过蛙港/打包机发送到指定地址的邮箱。支持跨维度运输。',
        'core_flow': '打包机封装 → 设置地址 → 蛙港发送 → 邮箱接收 → 理包机拆包',
        'components': [
            'create:packager', 'create:repackager', 'create:package_frogport',
            'create:stock_link', 'create:stock_ticker', 'create:redstone_requester',
            'create:postbox', 'create:package', 'create:rare_package', 'create:package_filter',
        ],
    },

    '蓝图/投影系统': {
        'description': '保存和自动建造结构的系统。使用蓝图与笔保存结构为 .nbt 文件，蓝图加农炮自动放置方块来建造。创造板条箱可为蓝图加农炮提供无限材料。',
        'core_flow': '蓝图与笔框选区域 → 保存为 .nbt → 蓝图桌写入空白蓝图 → 部署蓝图 → 蓝图加农炮自动建造',
        'components': [
            'create:schematic_and_quill', 'create:empty_schematic', 'create:schematic',
            'create:schematic_table', 'create:schematicannon',
            'create:creative_crate',
            'create:crafting_blueprint',
        ],
    },
}

# ====== Step 4: Build function descriptions ======
print("Step 4: Generating function descriptions...")

def get_item_category(item_id, prefix='item'):
    """Get category from ITEM_CLASS_MAP or infer from context"""
    if item_id in ITEM_CLASS_MAP:
        return ITEM_CLASS_MAP[item_id].get('category', '未知')
    return '未知'

def generate_item_function(item_id):
    """Generate function description for items, using IMPORTANT_SETTINGS as context"""
    cat = get_item_category(item_id, 'item')
    name = lang_name(f'item.{MODID}', item_id)
    name_zh = lang_zh(f'item.{MODID}', item_id)
    tip = tooltip(f'item.{MODID}', item_id)
    summary = tip['summary_en'] + ' | ' + tip['summary_zh']
    behaviours = tip['behaviours']
    context_bits = []
    for bucket in ('core_resources', 'core_systems', 'progression_stages', 'special_rules'):
        context_bits.extend(IMPORTANT_SETTINGS.get(bucket, [])[:3])
    context_tail = ''
    if context_bits:
        context_tail = '；结合重要设定：' + '、'.join(list(dict.fromkeys(context_bits))[:6])
    
    # Used in recipes
    used_in = recipe_input_index.get(item_id, [])
    used_in_machines = set()
    for r in used_in:
        used_in_machines.add(r['machine'])
    
    # How to obtain
    obtain_recipes = recipe_output_index.get(item_id, [])
    obtain_machines = set()
    for r in obtain_recipes:
        obtain_machines.add(r['machine'])

    # Generate description based on category
    if cat == '材料':
        desc = f'{name}（{name_zh}）是该模组的基础材料'
        if used_in:
            used_names = set()
            for r in used_in[:10]:
                for o in r['outputs'][:3]:
                    if ':' in o and o != f'{MODID}:{item_id}':
                        used_names.add(lang_name(f'item.{MODID}', o.split(':', 1)[1]))
            if used_names:
                desc += f'，用于合成：{", ".join(sorted([x for x in used_names if x])[:8])}'
        desc += context_tail + '。'
    
    elif cat == '半成品':
        desc = f'{name}（{name_zh}）是该模组的中间产物，通常不能直接作为终端物品使用，需要通过专门流程加工得到。'
        if obtain_recipes:
            machines = ', '.join(sorted(set(r['machine'] for r in obtain_recipes)))
            desc += f' 可通过 {machines} 获得。'
        desc += context_tail
    
    elif cat == '工具':
        desc = f'{name}（{name_zh}）是该模组的工具。'
        if summary:
            desc += f' {summary}'
        if behaviours:
            for b in behaviours:
                cond = b.get('condition_en', '')
                beh = b.get('behaviour_en', '')
                if cond and beh:
                    desc += f' {cond}：{beh}'
    
    elif cat == '装备':
        desc = f'{name}（{name_zh}）是机械动力的可穿戴装备。'
        if summary:
            desc += f' {summary}'
        if behaviours:
            for b in behaviours:
                cond = b.get('condition_en', '')
                beh = b.get('behaviour_en', '')
                if cond and beh:
                    desc += f' {cond}：{beh}'
    
    elif cat == '食物':
        desc = f'{name}（{name_zh}）是机械动力的食物/饮品。'
        if summary:
            desc += f' {summary}'
    
    elif cat == '燃料':
        desc = f'{name}（{name_zh}）是烈焰人燃烧室的燃料。'
        if summary:
            desc += f' {summary}'
    
    elif cat == '粉碎矿石':
        desc = f'{name}（{name_zh}）是矿石经粉碎轮加工后的中间产物。可通过清洗（Splashing）获得粗矿粒，或通过熔炼直接获得锭。'
    
    elif cat == '特殊材料':
        desc = f'{name}（{name_zh}）是机械动力的稀有材料，需要特殊流程获取。'
        if summary:
            desc += f' {summary}'
    
    elif cat == '物流':
        desc = f'{name}（{name_zh}）是机械动力的物流相关物品。'
        if summary:
            desc += f' {summary}'
    
    else:
        desc = f'{name}（{name_zh}）。'
        if summary:
            desc += f' {summary}'

    return desc.strip()

def generate_block_function(block_id):
    """Generate function description for blocks"""
    name = lang_name(f'block.{MODID}', block_id)
    name_zh = lang_zh(f'block.{MODID}', block_id)
    tip = tooltip(f'block.{MODID}', block_id)
    summary = tip['summary_en'] + ' | ' + tip['summary_zh']
    behaviours = tip['behaviours']

    # Check which system this block belongs to
    systems_belong = []
    for sys_name, sys_data in SYSTEMS.items():
        if 'components' in sys_data and block_id in sys_data.get('components', []):
            systems_belong.append(sys_name)
        if 'transmission_blocks' in sys_data and block_id in sys_data.get('transmission_blocks', []):
            systems_belong.append(sys_name)
        if 'processing_blocks' in sys_data and block_id in sys_data.get('processing_blocks', []):
            systems_belong.append(sys_name)
        if 'source_blocks' in sys_data and block_id in sys_data.get('source_blocks', []):
            systems_belong.append(sys_name)
        if 'gauge_blocks' in sys_data and block_id in sys_data.get('gauge_blocks', []):
            systems_belong.append(sys_name)

    # Check recipes this block processes
    processed_recipes = [r for r in all_recipes.values() if r['machine'] == block_id or 
                         (block_id == 'mechanical_press' and r['machine'] == 'mechanical_press') or
                         (block_id == 'crushing_wheel' and r['machine'] == 'crushing_wheel')]
    
    # Collect input-output pairs
    io_pairs = []
    for r in recipe_output_index.get(block_id, []):
        if r['machine'] not in ('crafting_table',):
            for inp in r['inputs']:
                for out in r['outputs']:
                    if ':' in out and out.split(':', 1)[0] == MODID:
                        io_pairs.append((inp, out))

    desc = f'{name}（{name_zh}）'

    if systems_belong:
        desc += f' 属于【{"、".join(systems_belong)}】。'
    
    if summary:
        desc += f' {summary}'

    if behaviours:
        for b in behaviours:
            cond = b.get('condition_en', '')
            beh = b.get('behaviour_en', '')
            if cond and beh:
                desc += f' {cond}：{beh}'

    # Add machine-specific descriptions
    # ========== 示例模板数据开始 ==========
    # 以下 elif 链包含 Create 机械动力专属的方块描述。
    # 适配其他 mod 时请根据目标 mod 的方块机制替换全部内容。
    if block_id == 'shaft':
        desc += ' 传动杆是最基础的动力传输组件，沿一个轴向传递旋转动力。多个传动杆可首尾相连延长。'
    elif block_id == 'cogwheel':
        desc += ' 小齿轮用于在平行轴之间传递动力并反转旋转方向。相邻齿轮自动啮合。'
    elif block_id == 'large_cogwheel':
        desc += ' 大齿轮用于在平行轴之间传递动力，转速为小齿轮的一半（2:1减速），应力容量加倍。'
    elif block_id == 'gearbox':
        desc += ' 十字齿轮箱将传动杆的旋转方向从水平变为垂直（或反之），不改变转速。'
    elif block_id == 'belt':
        desc += ' 传送带同时运输物品和传递动力。物品在传送带上移动，相邻的漏斗/隧道可输入/输出物品。'
    elif block_id == 'mechanical_press':
        desc += ' 动力冲压机上下往复运动，对下方物品施加冲压。配合工作盆可执行压缩（Compacting）配方，如将锭压成板、将金属混合物压缩为合金。'
    elif block_id == 'mechanical_mixer':
        desc += ' 动力搅拌器旋转搅拌工作盆内的物品/流体，执行搅拌（Mixing）配方。可同时处理多物品和流体输入。'
    elif block_id == 'crushing_wheel':
        desc += ' 一对对向旋转的粉碎轮，将投入的物品粉碎为更细的产物。矿石粉碎后获得两份粉碎矿石，可提升矿物产出。'
    elif block_id == 'millstone':
        desc += ' 石磨通过上盘旋转研磨下方的物品。基础加工机器，适合处理小麦（→面粉）和矿石的初级粉碎。'
    elif block_id == 'encased_fan':
        desc += ' 鼓风机产生气流，可对经过气流的物品进行加工：经过水=清洗（Splashing）、经过熔岩=熔炼（Blasting）、经过灵魂火=缠魂（Haunting）、经过火=烟熏（Smoking）。'
    elif block_id == 'mechanical_pump':
        desc += ' 动力泵推动流体在管道网络中流动，产生压力差。转速越高流量越大。'
    elif block_id == 'fluid_pipe':
        desc += ' 流体管道连接泵和储罐/机器，运输流体。多个管道会自动连接。'
    elif block_id == 'fluid_tank':
        desc += ' 流体储罐存储大量流体。多个储罐可堆叠扩展容量。'
    elif block_id == 'spout':
        desc += ' 注液器从上方储罐中抽取流体注入下方物品。用于填充瓶罐、制作巧克力浆果等。'
    elif block_id == 'item_drain':
        desc += ' 分液池从物品中提取流体。将含流体的物品（如湿海绵、流体桶）放入可分离出流体。'
    elif block_id == 'mechanical_arm':
        desc += ' 动力臂可在其圆形范围内拾取、运输和放置物品。可配置输入/输出端和过滤规则。'
    elif block_id == 'deployer':
        desc += ' 机械手模拟玩家右键操作：手持物品对前方方块/实体使用。可用于自动放置方块、应用砂纸打磨、填充物品等。'
    elif block_id == 'mechanical_crafter':
        desc += ' 动力合成器按 3×3 配方自动合成物品。多个合成器可组成合成阵列，通过传送带输入材料。'
    elif block_id == 'mechanical_bearing':
        desc += ' 动力轴承使连接的方块组围绕轴旋转。最大范围 32 格，需要连续的动力输入。'
    elif block_id == 'windmill_bearing':
        desc += ' 风车轴承利用风帆产生动力而不消耗应力。连接的风帆越多产生的应力越大，但转速受风帆数量影响。'
    elif block_id == 'mechanical_piston':
        desc += ' 动力活塞推动连接的方块组直线前后移动。需要粘性版本（Sticky）才能拉回方块。'
    elif block_id == 'rope_pulley':
        desc += ' 绳索滑轮使用绳索上下移动连接的方块组。常用于电梯、桥梁、矿井升降机。'
    elif block_id == 'elevator_pulley':
        desc += ' 升降机滑轮配合升降机锚点实现多楼层停靠。玩家可在不同楼层召唤电梯。'
    elif block_id == 'steam_engine':
        desc += ' 蒸汽引擎将下方锅炉（加热的流体储罐组）产生的蒸汽转化为旋转动力。锅炉越大产生的应力越大。'
    elif block_id == 'flywheel':
        desc += ' 飞轮装饰性地连接到蒸汽引擎，提升蒸汽引擎产生的最大应力。'
    elif block_id == 'speedometer':
        desc += ' 转速表显示所在动力网络的当前转速（RPM）。'
    elif block_id == 'stressometer':
        desc += ' 应力表显示所在动力网络的当前应力和最大容量（SU）。'
    elif block_id == 'rotation_speed_controller':
        desc += ' 转速控制器可独立调节输出端的转速，不依赖输入端的转速比例。常用于精确控制机器速度。'
    elif block_id == 'sequenced_gearshift':
        desc += ' 可编程齿轮箱按预设的时序序列改变输出转速和方向。支持加速、减速、等待、反转等指令。'
    elif block_id == 'clutch':
        desc += ' 离合器通过红石信号控制是否传递动力。信号=断开，无信号=连接。'
    elif block_id == 'gearshift':
        desc += ' 反转齿轮箱通过红石信号控制输出方向反转。'
    elif block_id == 'adjustable_chain_gearshift':
        desc += ' 可调节链式传动箱改变传动杆之间的转速比，支持 1:1 到 4:1 的变速。'
    elif block_id == 'andesite_funnel' or block_id == 'brass_funnel':
        desc += ' 漏斗从上方容器/传送带提取物品或向下方容器/传送带输入物品。黄铜版本支持过滤配置。'
    elif block_id == 'andesite_tunnel' or block_id == 'brass_tunnel':
        desc += ' 隧道从传送带上提取物品并分配到多个输出方向。黄铜版本支持更复杂的分流逻辑。'
    elif block_id == 'chute':
        desc += ' 溜槽让物品在重力作用下垂直下落，可连接上下容器。'
    elif block_id == 'smart_chute':
        desc += ' 智能溜槽在垂直运输时可配置过滤规则，只允许特定物品通过。'
    elif block_id == 'depot':
        desc += ' 置物台提供一个物品的暂存位置，动力臂可从置物台拾取/放置物品。'
    elif block_id == 'weighted_ejector':
        desc += ' 弹射置物台将物品弹射到远处的传送带或置物台上，弹射距离可调节。'
    elif block_id == 'item_vault':
        desc += ' 物品保险库用于大量存储物品。多个保险库可并排/堆叠以扩展容量。'
    elif block_id == 'redstone_link':
        desc += ' 无线红石信号终端通过频率配对实现无线红石信号传输。发射端和接收端设置相同频率即可通信。'
    elif block_id == 'display_link':
        desc += ' 显示链接器从连接的方块（如储罐、箱子、应力表）读取数据并在翻牌显示器或辉光管上显示。'
    elif block_id == 'display_board':
        desc += ' 翻牌显示器以翻牌动画显示文本/数字信息，通常配合显示链接器使用。'
    elif block_id == 'nixie_tube' or block_id.endswith('_nixie_tube'):
        desc += ' 辉光管以霓虹灯管风格显示数字/文本。可显示来自显示链接器的数据。'
    elif block_id == 'stockpile_switch':
        desc += ' 存量转信器监测上方容器的物品/流体储量，在达到设定阈值时输出红石信号。'
    elif block_id == 'content_observer':
        desc += ' 智能侦测器监测传送带/漏斗上的物品，在检测到指定物品时输出红石信号。'
    elif block_id == 'track':
        desc += ' 列车轨道是铁路系统的基础，铺设在地面上供列车行驶。支持斜坡和转弯。'
    elif block_id == 'track_station':
        desc += ' 列车站点是列车时刻表中的停靠点，列车到达后装卸物品/实体。'
    elif block_id == 'track_signal':
        desc += ' 列车信号机将轨道划分为区间，防止多列车碰撞。支持单向/双向信号。'
    elif block_id == 'controller_rail':
        desc += ' 控制铁轨根据红石信号强度控制经过矿车的速度，实现精确速度控制。'
    elif block_id == 'small_bogey' or block_id == 'large_bogey':
        desc += ' 转向架是列车的基础移动单元，放置在轨道上后可在上面搭建列车结构。大转向架更宽、承重更大。'
    elif block_id == 'controls':
        desc += ' 列车驾驶台安装在列车上后，玩家可以右键进入驾驶模式控制列车行驶。'
    elif block_id == 'blaze_burner':
        desc += ' 烈焰人燃烧室是 Create 的热源/燃料来源。投入烈焰蛋糕可提升热量等级（普通→烈炎），用于驱动蒸汽引擎和鼓风机加工。'
    elif block_id == 'hand_crank':
        desc += ' 手摇曲柄通过玩家右键手动产生旋转动力。适合测试机械或应急使用。'
    elif block_id == 'water_wheel':
        desc += ' 水车利用流动的水产生旋转动力。水流经水车叶片即可驱动，是前期最实用的动力源。'
    elif block_id == 'large_water_wheel':
        desc += ' 大型水车比普通水车产生更大的应力，但体积更大。'
    elif block_id == 'packager':
        desc += ' 打包机将物品封装为包裹，可设置目标地址。用于打包系统中的物品邮寄。'
    elif block_id == 'repackager':
        desc += ' 理包机拆开包裹取出其中的物品。'
    elif block_id == 'package_frogport':
        desc += ' 货物蛙港是打包系统的物流节点，接收和发送包裹。可跨维度传输。'
    elif block_id == 'andesite_casing':
        desc += ' 安山机壳是基础级的机械外壳，用于合成安山系列齿轮箱和漏斗等组件。'
    elif block_id == 'brass_casing':
        desc += ' 黄铜机壳是进阶级的机械外壳，用于合成黄铜系列齿轮箱和精密组件。'
    elif block_id == 'copper_casing':
        desc += ' 铜机壳用于合成流体相关组件，如管道、泵等。'
    elif block_id == 'gantry_carriage':
        desc += ' 起重机取物器在起重机杆上滑动，可携带方块组/机器沿杆移动。'
    elif block_id == 'linear_chassis':
        desc += ' 轴向底盘用于扩展动力活塞/轴承的连接范围。底盘上的黏着器可粘附更多方块。'
    elif block_id == 'radial_chassis':
        desc += ' 径向底盘在径向方向扩展连接范围，配合动力轴承使用。'
    elif block_id == 'sticker':
        desc += ' 黏着器在动态结构移动时临时粘合前方方块。接到红石信号时伸出/收回。'
    elif block_id == 'cart_assembler':
        desc += ' 矿车装配站将矿车上的方块组装配为动态结构。扳手右键可移动已装配的结构。'
    elif block_id == 'portable_storage_interface':
        desc += ' 移动式存储接口让动态结构在接触时与静态容器交换物品。'
    elif block_id == 'portable_fluid_interface':
        desc += ' 移动式流体接口让动态结构在接触时与静态流体储罐交换流体。'
    elif block_id == 'copycat_panel':
        desc += ' 伪装板可模仿其他方块的外观，用于装饰。手持目标方块右键即可改变外观。'
    elif block_id == 'copycat_step':
        desc += ' 伪装半阶可模仿其他方块的外观，功能同伪装板但为半阶形状。'
    elif block_id == 'seat' or block_id.endswith('_seat'):
        desc += ' 坐垫让玩家/实体坐下，并在动态结构移动时固定在上方。'
    elif block_id == 'toolbox' or block_id.endswith('_toolbox'):
        desc += ' 工具箱可存储 8 种不同物品各大量。放置后附近的玩家可通过快捷键远程访问。'
    elif block_id == 'schematicannon':
        desc += ' 蓝图加农炮自动放置方块来构建已部署的蓝图结构。使用火药作为燃料，从相邻容器取材料。'
    elif block_id == 'schematic_table':
        desc += ' 蓝图桌将保存的 .nbt 蓝图文件写入空白蓝图。'
    elif block_id == 'mechanical_saw':
        desc += ' 动力锯切割木材/石材等方块，可放在传送带旁自动切割经过的物品。执行切割（Cutting）配方。'
    elif block_id == 'mechanical_drill':
        desc += ' 动力钻头破坏前方方块，可安装在动态结构上自动采矿。'
    elif block_id == 'mechanical_harvester':
        desc += ' 动力收割机破坏前方的作物，可安装在动态结构上自动收割。'
    elif block_id == 'mechanical_plough':
        desc += ' 动力犁耕犁前方土地，可安装在动态结构上自动耕地。'
    elif block_id == 'mechanical_roller':
        desc += ' 动力压路机碾压前方地形，可铺设道路/清除障碍。'
    elif block_id == 'pulse_extender':
        desc += ' 脉冲延长器将输入的红石脉冲延长到设定时长。'
    elif block_id == 'pulse_repeater':
        desc += ' 脉冲中继器在检测到红石脉冲后输出一个脉冲，可用于边沿触发。'
    elif block_id == 'pulse_timer':
        desc += ' 脉冲计时器在收到信号后按设定周期循环输出脉冲。'
    elif block_id == 'powered_latch':
        desc += ' 锁存器在收到设置信号时打开输出并保持，收到重置信号时关闭。'
    elif block_id == 'powered_toggle_latch':
        desc += ' 转换锁存器每次收到信号时切换输出状态。'
    elif block_id == 'redstone_contact':
        desc += ' 接触式红石信号发生器在动态结构接触时输出红石信号，用于检测结构位置。'
    elif block_id == 'analog_lever':
        desc += ' 模拟拉杆输出可调节强度的红石信号（0-15），右键旋转调节。'
    elif block_id == 'placard':
        desc += ' 置物板展示一个物品，当手持相同物品右键时发出红石信号。'
    elif block_id == 'turntable':
        desc += ' 转盘使上方的实体/玩家旋转，通常用于展示或装饰。'
    elif block_id == 'hose_pulley':
        desc += ' 软管滑轮可向下延伸软管从下方抽取/排放流体。放在无限水源/熔岩源上方时可无限抽取。'
    elif block_id == 'steam_whistle':
        desc += ' 蒸汽笛由蒸汽引擎驱动时发出声音，音调取决于蒸汽压力。'
    elif block_id == 'desk_bell':
        desc += ' 呼唤铃右键点击发出声音和红石信号。放在升降机中时到站自动响铃。'
    elif block_id == 'haunted_bell':
        desc += ' 缠魂钟手持或鸣响时高亮附近可刷怪的位置。'
    elif block_id == 'peculiar_bell':
        desc += ' 奇异钟装饰用黄铜钟，放在灵魂火上方可能产生特殊效果。'
    elif block_id == 'rope':
        desc += ' 绳索配合绳索滑轮使用，连接滑轮和下方结构。'
    elif block_id == 'sail_frame' or block_id == 'white_sail' or block_id.endswith('_sail'):
        desc += ' 风帆/风帆框架是风车轴承产生动力的关键，连接的数量越多风车产生的应力越大。'
    elif block_id == 'experience_block':
        desc += ' 经验块是经验颗粒的存储方块，发出 15 级亮度。'

    # ========== 示例模板数据结束 ==========
    return desc.strip()

# ====== Step 5: Build output ======
print("Step 5: Assembling output...")

entries = {}

# Process items
item_ids = set()
for k in LANG_EN_DATA:
    if k.startswith(f'item.{MODID}.') and '.' not in k[len(f'item.{MODID}.'):]:
        item_ids.add(k.replace(f'item.{MODID}.', ''))

for item_id in sorted(item_ids):
    name_en = lang_name(f'item.{MODID}', item_id)
    name_zh = lang_zh(f'item.{MODID}', item_id)
    tip = tooltip(f'item.{MODID}', item_id)
    cat = get_item_category(item_id, 'item')

    obtain_recipes = recipe_output_index.get(item_id, [])
    used_in_recipes = recipe_input_index.get(item_id, [])

    # Format how_to_obtain
    how_to_obtain = {}
    for r in obtain_recipes:
        machine = r['machine']
        if machine not in how_to_obtain:
            how_to_obtain[machine] = []
        # Simplify the recipe info
        info = {
            'recipe_id': r['recipe_id'],
            'type': r['type'],
        }
        if r['inputs']:
            info['inputs'] = r['inputs'][:8]
        how_to_obtain[machine].append(info)

    # Format used_in
    used_in_formatted = []
    for r in used_in_recipes[:15]:
        used_in_formatted.append({
            'recipe_id': r['recipe_id'],
            'machine': r['machine'],
            'outputs': r['outputs'][:3],
        })

    entries[f'{MODID}:{item_id}'] = {
        'type': 'item',
        'name_en': name_en,
        'name_zh': name_zh,
        'category': cat,
        'rarity': ITEM_CLASS_MAP.get(item_id, {}).get('rarity', 'COMMON'),
        'tooltip': tip,
        'how_to_obtain': how_to_obtain if how_to_obtain else None,
        'used_in_recipes': used_in_formatted if used_in_formatted else None,
        'function': generate_item_function(item_id),
    }

# Process blocks (only functional ones - skip pure decorative stone variants)
functional_block_ids = [
    # ========== 示例模板数据 ==========
    # 以下 functional_block_ids 是 Create 机械动力的功能方块列表。
    # 适配其他 mod 时请根据该 mod 的实际功能方块替换全部内容。
    # Kinetic core
    'shaft', 'cogwheel', 'large_cogwheel', 'gearbox', 'vertical_gearbox',
    'belt', 'encased_chain_drive', 'chain_conveyor',
    'adjustable_chain_gearshift', 'gearshift', 'sequenced_gearshift',
    'rotation_speed_controller', 'clutch',
    'hand_crank', 'water_wheel', 'large_water_wheel',
    'flywheel', 'steam_engine', 'powered_shaft',
    'speedometer', 'stressometer',

    # Processing
    'millstone', 'crushing_wheel', 'crushing_wheel_controller',
    'mechanical_press', 'mechanical_mixer',
    'mechanical_drill', 'mechanical_saw',
    'mechanical_harvester', 'mechanical_plough', 'mechanical_roller',
    'encased_fan', 'nozzle',
    'basin', 'spout', 'depot', 'weighted_ejector',
    'mechanical_crafter',

    # Fluids
    'mechanical_pump', 'fluid_pipe', 'glass_fluid_pipe',
    'encased_fluid_pipe', 'smart_fluid_pipe', 'fluid_valve',
    'hose_pulley', 'item_drain',
    'fluid_tank', 'creative_fluid_tank',

    # Items & logistics
    'chute', 'smart_chute', 'item_vault', 'item_hatch',
    'andesite_funnel', 'andesite_belt_funnel', 'andesite_tunnel',
    'brass_funnel', 'brass_belt_funnel', 'brass_tunnel',
    'mechanical_arm', 'deployer',
    'packager', 'repackager', 'package_frogport',
    'stock_link', 'stock_ticker', 'redstone_requester',
    'creative_crate',

    # Contraptions
    'mechanical_bearing', 'windmill_bearing', 'clockwork_bearing',
    'mechanical_piston', 'sticky_mechanical_piston',
    'piston_extension_pole', 'rope_pulley', 'elevator_pulley',
    'elevator_contact',
    'gantry_carriage', 'gantry_shaft',
    'linear_chassis', 'radial_chassis', 'secondary_linear_chassis',
    'sticker', 'cart_assembler', 'minecart_anchor',
    'portable_storage_interface', 'portable_fluid_interface',
    'sail_frame', 'contraption_controls',
    'rope', 'pulley_magnet',

    # Railway
    'track', 'track_station', 'track_signal', 'track_observer',
    'controller_rail', 'train_door', 'train_trapdoor',
    'controls', 'small_bogey', 'large_bogey',
    'steam_whistle', 'steam_whistle_extension',
    'railway_casing',

    # Redstone / display
    'redstone_link', 'redstone_contact',
    'powered_latch', 'powered_toggle_latch',
    'pulse_extender', 'pulse_repeater', 'pulse_timer',
    'analog_lever', 'stockpile_switch', 'content_observer',
    'display_link', 'display_board', 'nixie_tube',
    'placard', 'desk_bell', 'haunted_bell', 'peculiar_bell',
    'cuckoo_clock', 'mysterious_cuckoo_clock',
    'turntable', 'lectern_controller',

    # Schematics
    'schematicannon', 'schematic_table',

    # Special
    'blaze_burner', 'lit_blaze_burner',
    'creative_motor', 'copper_backtank', 'netherite_backtank',
    'experience_block', 'rose_quartz_lamp',

    # Copycat
    'copycat_panel', 'copycat_step', 'copycat_base', 'copycat_bars',

    # Brackets
    'metal_bracket', 'wooden_bracket', 'metal_girder', 'metal_girder_encased_shaft',

    # Casings & encased
    'andesite_casing', 'brass_casing', 'copper_casing',
    'andesite_encased_cogwheel', 'andesite_encased_large_cogwheel', 'andesite_encased_shaft',
    'brass_encased_cogwheel', 'brass_encased_large_cogwheel', 'brass_encased_shaft',

    # Doors/ladders/scaffolds (functional variants)
    'andesite_door', 'brass_door', 'copper_door', 'train_door', 'framed_glass_door',
    'train_trapdoor', 'framed_glass_trapdoor',
    'andesite_ladder', 'brass_ladder', 'copper_ladder',
    'andesite_scaffolding', 'brass_scaffolding', 'copper_scaffolding',
    'andesite_bars', 'brass_bars', 'copper_bars',
    'industrial_iron_block', 'weathered_iron_block',

    # Storage blocks
    'andesite_alloy_block', 'brass_block', 'zinc_block', 'raw_zinc_block',
    'cardboard_block', 'bound_cardboard_block',
    'rose_quartz_block', 'rose_quartz_tiles', 'small_rose_quartz_tiles',
    'zinc_ore', 'deepslate_zinc_ore',

    # Copper variants
    'copper_shingles', 'copper_shingle_slab', 'copper_shingle_stairs',
    'copper_tiles', 'copper_tile_slab', 'copper_tile_stairs',
    'chocolate', 'honey',
]

# Add colored variants
colors = ['white', 'orange', 'magenta', 'light_blue', 'yellow', 'lime',
          'pink', 'gray', 'light_gray', 'cyan', 'purple', 'blue', 'brown', 'green', 'red', 'black']
for color in colors:
    functional_block_ids.append(f'{color}_nixie_tube')
    functional_block_ids.append(f'{color}_postbox')
    functional_block_ids.append(f'{color}_sail')
    functional_block_ids.append(f'{color}_seat')
    functional_block_ids.append(f'{color}_table_cloth')
    functional_block_ids.append(f'{color}_toolbox')
    functional_block_ids.append(f'{color}_valve_handle')
functional_block_ids.append('copper_valve_handle')
functional_block_ids.append('nixie_tube')  # base orange
functional_block_ids.append('white_sail')
functional_block_ids.append('seat')
functional_block_ids.append('toolbox')

for block_id in functional_block_ids:
    key = f'block.{MODID}.{block_id}'
    if key not in LANG_EN_DATA:
        continue
    
    name_en = lang_name(f'block.{MODID}', block_id)
    name_zh = lang_zh(f'block.{MODID}', block_id)
    tip = tooltip(f'block.{MODID}', block_id)

    # Check if it's a kinetic block (has stress impact)
    is_kinetic = block_id in [
        # ========== 示例模板数据 ==========
        'shaft', 'cogwheel', 'large_cogwheel', 'gearbox', 'vertical_gearbox',
        'belt', 'encased_chain_drive', 'chain_conveyor',
        'adjustable_chain_gearshift', 'gearshift', 'sequenced_gearshift',
        'rotation_speed_controller', 'clutch', 'hand_crank', 'water_wheel',
        'large_water_wheel', 'flywheel', 'steam_engine', 'powered_shaft',
        'millstone', 'crushing_wheel', 'mechanical_press', 'mechanical_mixer',
        'mechanical_drill', 'mechanical_saw', 'mechanical_harvester',
        'mechanical_plough', 'mechanical_roller', 'encased_fan',
        'mechanical_pump', 'mechanical_arm', 'deployer',
        'mechanical_bearing', 'windmill_bearing', 'clockwork_bearing',
        'mechanical_piston', 'sticky_mechanical_piston',
        'rope_pulley', 'elevator_pulley', 'gantry_carriage',
        'speedometer', 'stressometer', 'mechanical_crafter',
        'flywheel', 'steam_engine', 'cuckoo_clock',
        'schematicannon', 'rotation_speed_controller',
        'creative_motor', 'copper_backtank', 'netherite_backtank',
        'hose_pulley', 'turntable',
    ]

    how_to_obtain = {}
    obtain_recipes = recipe_output_index.get(block_id, [])
    for r in obtain_recipes:
        machine = r['machine']
        if machine not in how_to_obtain:
            how_to_obtain[machine] = []
        how_to_obtain[machine].append({
            'recipe_id': r['recipe_id'],
            'type': r['type'],
            'inputs': r['inputs'][:8] if r['inputs'] else [],
        })

    # Format used_in for blocks
    used_in_block = recipe_input_index.get(block_id, [])
    used_in_formatted_block = []
    for r in used_in_block[:15]:
        used_in_formatted_block.append({
            'recipe_id': r['recipe_id'],
            'machine': r['machine'],
            'outputs': r['outputs'][:3],
        })

    entries[f'{MODID}:{block_id}'] = {
        'type': 'block',
        'name_en': name_en,
        'name_zh': name_zh,
        'is_kinetic': is_kinetic,
        'tooltip': tip,
        'how_to_obtain': how_to_obtain if how_to_obtain else None,
        'used_in_recipes': used_in_formatted_block if used_in_formatted_block else None,
        'function': generate_block_function(block_id),
    }

# Build output
output = {
    'mod_info': {
        'name': MODID,
        'id': MODID,
        'version': os.environ.get('MOD_VERSION', 'unknown'),
        'mc_version': os.environ.get('MC_VERSION', 'unknown'),
        'loader': os.environ.get('MOD_LOADER', 'unknown'),
        'description': f'{MODID} mod item database generated by mod-analyzer-skill.',
        'description_zh': f'{MODID} 模组物品深度分析数据库。',
    },
    'important_settings': IMPORTANT_SETTINGS,
    'systems': SYSTEMS,
    'total_entries': len(entries),
    'entries': entries,
}

os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\nDone! Total entries: {len(entries)}")
print(f"Saved to: {OUTPUT}")
