"""Display-language support for the creation wizard (English, 简体中文, bilingual).

Canonical identifiers (skill keys, occupation keys) never change with the
language; only rendering does.

``Msg`` is a ``str`` holding the English text, so existing code, logs and tests
keep working unchanged, while also carrying a message ``code`` and its
``params`` so the Discord layer can re-render it in the player's language.
Skill names are passed as skill *keys* in the ``skill`` param and resolved per
language at render time.

Bilingual mode renders "English / 中文" when both fit on one short line, and
otherwise English, then Chinese on the next line. Where Discord imposes a
short limit (button labels, modal titles), bilingual falls back to English
if the combined text would not fit.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, Mapping, Optional

LANGS = ("en", "zh", "both")
LANG_NAMES = {"en": "English", "zh": "简体中文", "both": "English + 简体中文"}
DEFAULT_LANG = "en"

NamesByLang = Mapping[str, Mapping[str, str]]   # {"en": {key: name}, "zh": {key: name}}

# code -> (English, Chinese)
CATALOG: Dict[str, tuple] = {
    # --- rules: review errors & notes ----------------------------------------------
    "enter_characteristics": ("Enter all characteristics first (missing: {stats}).",
                              "请先填写所有属性（缺少：{stats}）。"),
    "picks_missing": ("{n} occupation skill choice(s) still to make.", "还有 {n} 项本职技能尚未选择。"),
    "occ_overspent": ("Occupation points overspent by {n}.", "职业点数超支 {n} 点。"),
    "int_overspent": ("Personal interest points overspent by {n}.", "兴趣点数超支 {n} 点。"),
    "not_occ_skill_has_points": ("{skill} is not an occupation skill but has occupation points.",
                                 "{skill} 不是本职技能，却分配了职业点数。"),
    "mythos_no_raise": ("Cthulhu Mythos cannot be raised during creation.", "创建调查员时不能提升克苏鲁神话。"),
    "over_max": ("{skill} would be {total}; the maximum is {max}.", "{skill} 将达到 {total}，上限为 {max}。"),
    "credit_range": ("Credit Rating is {value}; this occupation requires {min}-{max}.",
                     "信誉为 {value}；该职业要求 {min}–{max}。"),
    "warn_occ_unspent": ("{n} occupation points unspent (they are not carried over).",
                         "还有 {n} 点职业点数未分配（不会保留）。"),
    "warn_int_unspent": ("{n} personal interest points unspent.", "还有 {n} 点兴趣点数未分配。"),
    "manual_mythos": ("Cthulhu Mythos is {value} (entered by hand); it must be 0 at creation. Discard it.",
                      "克苏鲁神话为 {value}（手动输入）；创建时必须为 0，请舍弃。"),
    "manual_value": ("{skill} is {value}, entered by hand rather than paid for with points (base {base}). "
                     "Reconcile it: convert to points or discard.",
                     "{skill} 为 {value}，是手动输入而非用点数购买（基础值 {base}）。请处理：转换为点数或舍弃。"),
    "warn_occ_issues": ("Occupation data needs Keeper review: {issues}", "职业数据需守密人核对：{issues}"),
    "warn_age": ("Age adjustments (rulebook ch. 3) are not applied automatically; check them with your Keeper.",
                 "年龄调整（规则书第 3 章）不会自动应用，请与守密人确认。"),
    # --- rules: single allocation / picks -------------------------------------------------
    "points_negative": ("Points cannot be negative.", "点数不能为负数。"),
    "occ_points_not_allowed": ("Occupation points can only go to this occupation's skills and Credit Rating.",
                               "职业点数只能分配给本职技能和信誉。"),
    "over_max_creation": ("That would make the skill {total}; the maximum at creation is {max}.",
                          "这会使该技能达到 {total}；创建时上限为 {max}。"),
    "budgets_unknown": ("Enter all characteristics first so the point budgets can be calculated.",
                        "请先填写所有属性，才能计算点数预算。"),
    "occ_left": ("Only {n} occupation points are left.", "仅剩 {n} 点职业点数。"),
    "int_left": ("Only {n} personal interest points are left.", "仅剩 {n} 点兴趣点数。"),
    "credit_max": ("Credit Rating for this occupation is at most {max}.", "该职业的信誉最高为 {max}。"),
    "pick_no_such": ("That occupation has no such skill choice.", "该职业没有这一项技能选择。"),
    "pick_not_option": ("That skill is not one of the options for this choice.", "该技能不在此项的可选范围内。"),
    "pick_taken": ("That skill is already one of your occupation skills.", "该技能已是你的本职技能。"),
    # --- batch allocation -----------------------------------------------------------------
    "batch_occ_over": ("Occupation points would exceed the budget by {over} ({after} of {total}; "
                       "{left} occupation points were left).",
                       "职业点数将超出预算 {over} 点（{after}/{total}；修改前剩余 {left} 点）。"),
    "batch_int_over": ("Personal interest points would exceed the budget by {over} ({after} of {total}; "
                       "{left} personal interest points were left).",
                       "兴趣点数将超出预算 {over} 点（{after}/{total}；修改前剩余 {left} 点）。"),
    "batch_remove_too_many": ("{skill}: cannot remove {n} points; only {have} are allocated.",
                              "{skill}：无法减少 {n} 点，目前只分配了 {have} 点。"),
    "batch_bad_number": ("{skill}: “{text}” is not a whole number.", "{skill}：“{text}”不是整数。"),
    "batch_occ_not_allowed": ("{skill}: occupation points can only go to this occupation's skills and Credit Rating.",
                              "{skill}：职业点数只能分配给本职技能和信誉。"),
    "batch_over_max": ("{skill} would be {total}; the maximum at creation is {max}.",
                       "{skill} 将达到 {total}；创建时上限为 {max}。"),
    "batch_credit_max": ("Credit Rating would be {total}; this occupation allows at most {max}.",
                         "信誉将达到 {total}；该职业最高为 {max}。"),
    "batch_mythos": ("Cthulhu Mythos cannot be raised during creation.", "创建调查员时不能提升克苏鲁神话。"),
    # --- service --------------------------------------------------------------------------
    "not_draft": ("Creation steps are only available while the investigator is a draft.",
                  "仅在调查员为草稿时才能进行创建步骤。"),
    "choose_occupation_first": ("Choose an occupation first.", "请先选择职业。"),
    "no_occupation_match": ("No occupation matching {ref}.", "找不到与 {ref} 匹配的职业。"),
    "confirm_occupation_change": ("Changing occupation clears your occupation skill choices and occupation points "
                                  "(personal interest points are kept). Confirm to continue.",
                                  "更换职业会清除本职技能选择和职业点数（兴趣点数保留）。请确认后继续。"),
    "credit_range_format": ("Credit Rating range must look like 10-40.", "信誉范围格式应为 10-40。"),
    "unknown_skill": ("Unknown skill {text}.", "未知技能：{text}。"),
    "formula_choice_invalid": ("{occupation} lets you choose from {stats}.", "{occupation} 可选择的属性为：{stats}。"),
    "base_unknown": ("{skill}'s base needs characteristics that are not entered yet.",
                     "{skill} 的基础值需要尚未填写的属性。"),
    "reconcile_first": ("{skill} has a hand-entered value ({value}). Reconcile it first "
                        "(convert to points or discard) on the Review step.",
                        "{skill} 有手动输入的值（{value}）。请先在“检查”步骤中处理（转换为点数或舍弃）。"),
    "no_manual_value": ("{skill} has no hand-entered value.", "{skill} 没有手动输入的值。"),
    "reconcile_action": ("Action must be 'convert' or 'discard'.", "操作只能是“转换”或“舍弃”。"),
    "mythos_cannot_convert": ("Cthulhu Mythos cannot be bought with points at creation; discard it.",
                              "创建时不能用点数购买克苏鲁神话，请舍弃。"),
    "below_base": ("{skill} ({value}) is below its base ({base}); discard it instead.",
                   "{skill}（{value}）低于基础值（{base}），请改为舍弃。"),
    "convert_failed": ("Cannot convert {skill} {value} into points: {reason}", "无法将 {skill} {value} 转换为点数：{reason}"),
    "set_skill_during_creation": ("During occupation-based creation, skills are set with skill points "
                                  "(Occupation & skills, or /character allocate). {skill} was not changed.",
                                  "职业创建过程中，技能值只能通过技能点数设定（“职业与技能”或 /character allocate）。"
                                  "{skill} 未被修改。"),
    "unspent_points": ("{occ} occupation and {int} personal interest points are unspent and will be lost. "
                       "Confirm to finish anyway.",
                       "还有 {occ} 点职业点数和 {int} 点兴趣点数未分配，完成后将作废。请确认后再完成。"),
    "not_finished": ("Not finished yet:\n- {errors}", "尚未完成：\n- {errors}"),
    "hb_name": ("Occupation name must be 1-64 characters.", "职业名称须为 1–64 个字符。"),
    "hb_credit": ("Credit Rating range must satisfy 0 ≤ min ≤ max ≤ 99.", "信誉范围须满足 0 ≤ 最小值 ≤ 最大值 ≤ 99。"),
    "hb_formula": ("Formula must be EDU*4 or EDU*2 + one characteristic ×2 (e.g. EDU*2+DEX*2).",
                   "公式须为 EDU*4 或 EDU*2 + 一项属性 ×2（例如 EDU*2+DEX*2）。"),
    "hb_formula_stat": ("Second formula characteristic must be one of {stats}.", "公式中的第二项属性须为以下之一：{stats}。"),
    # --- wizard UI ------------------------------------------------------------------------
    "step_characteristics": ("1 · Characteristics", "1 · 属性"),
    "step_occupation": ("2 · Occupation", "2 · 职业"),
    "step_choices": ("3 · Occupation skill choices", "3 · 本职技能选择"),
    "step_allocation": ("4 · Skill points", "4 · 技能点数"),
    "step_review": ("5 · Review", "5 · 检查"),
    "nav_occupation": ("Occupation", "职业"),
    "nav_choices": ("Choices", "技能选择"),
    "nav_allocation": ("Points", "点数"),
    "nav_review": ("Review", "检查"),
    "nav_sheet": ("Sheet", "角色卡"),
    "footer_saves": ("Changes save immediately · resume any time from the sheet editor",
                     "更改会立即保存 · 可随时从角色卡编辑器继续"),
    "enter_chars_first": ("Enter or roll all characteristics and Luck first (sheet editor buttons).",
                          "请先填写或掷出所有属性和幸运（角色卡编辑器按钮）。"),
    "occ_current": ("Current occupation: **{name}**", "当前职业：**{name}**"),
    "occ_intro": ("Pick an occupation from the menu, search by English or Chinese name, or define a custom one.",
                  "从菜单中选择职业，可用中文或英文名称搜索，也可以自定义职业。"),
    "occ_search_result": ("🔎 Search: `{query}` — {n} match(es)", "🔎 搜索：`{query}` — {n} 个结果"),
    "occ_confirm_title": ("Confirm change", "确认更换"),
    "occ_confirm_body": ("Switch to **{name}**? This clears your occupation skill choices and occupation points. "
                         "Personal interest points are kept.",
                         "更换为 **{name}**？这会清除本职技能选择和职业点数，兴趣点数保留。"),
    "occ_placeholder": ("Choose an occupation… (page {page}/{pages})", "选择职业…（第 {page}/{pages} 页）"),
    "occ_option_desc": ("Credit {min}-{max} · {formula}", "信誉 {min}-{max} · {formula}"),
    "btn_search": ("Search", "搜索"),
    "btn_custom": ("Custom occupation", "自定义职业"),
    "btn_confirm_change": ("Confirm change", "确认更换"),
    "btn_cancel": ("Cancel", "取消"),
    "occ_set": ("Occupation set to **{name}**.", "职业已设为 **{name}**。"),
    "occ_search_title": ("Search occupations", "搜索职业"),
    "occ_search_label": ("English or Chinese name (blank = all)", "中文或英文名称（留空显示全部）"),
    "hb_title": ("Custom occupation (agree with Keeper)", "自定义职业（需守密人同意）"),
    "hb_label_name": ("Occupation name", "职业名称"),
    "hb_label_credit": ("Credit Rating range", "信誉范围"),
    "hb_label_formula": ("Skill points formula", "职业点数公式"),
    "hb_created": ("Custom occupation **{name}** created. Now choose its 8 skills.",
                   "已创建自定义职业 **{name}**。现在选择它的 8 项技能。"),
    "f_credit": ("Credit Rating", "信誉"),
    "f_points": ("Occupation skill points", "职业技能点数"),
    "f_skills": ("Occupation skills", "本职技能"),
    "f_notes": ("Notes", "备注"),
    "note_unverified": ("Imported from a community sheet; not yet verified against the rulebook.",
                        "导入自社区角色卡，尚未与规则书核对。"),
    "note_homebrew": ("Homebrew occupation: agree its skills and Credit Rating with your Keeper.",
                      "自定义职业：请与守密人商定技能和信誉。"),
    "note_keeper": ("Keeper option: {text}", "守密人选项：{text}"),
    "tag_classic": ("classic", "古典"),
    "tag_modern": ("modern", "现代"),
    "tag_homebrew": ("homebrew", "自定义"),
    "slot_any_1": ("any 1 other skill", "任意 1 项其他技能"),
    "slot_any_n": ("any {n} other skills", "任意 {n} 项其他技能"),
    "slot_choose": ("{n} of: {options}", "从 {options} 中选 {n} 项"),
    "credit_rating_always": ("Credit Rating (always an occupation skill)", "信誉（始终为本职技能）"),
    "choices_header": ("**{occ}** — {done}/{total} choices made · {left} remaining",
                       "**{occ}** — 已选 {done}/{total} 项 · 剩余 {left} 项"),
    "choices_none": ("**{occ}** has no skills to choose. Continue to **Points**.",
                     "**{occ}** 没有需要选择的技能。请继续到**点数**。"),
    "choices_done": ("All occupation skills chosen. Now spend your points.", "本职技能已全部选好。现在分配点数。"),
    "picker_category_ph": ("Choose a category… ({n} skills available)", "选择类别…（共 {n} 项可选）"),
    "picker_skill_ph": ("Choice {i}/{n} — pick a skill", "第 {i}/{n} 项 — 选择技能"),
    "picker_skill_ph_any": ("Pick a skill", "选择技能"),
    "picker_page_suffix": (" (page {p}/{pages})", "（第 {p}/{pages} 页）"),
    "picker_hint_categories": ("Choose a category below, or **Search** in English or Chinese.",
                               "在下方选择类别，或用中文/英文**搜索**。"),
    "picker_results": ("🔎 `{query}`: {n} match(es)", "🔎 `{query}`：{n} 个结果"),
    "picker_category_shown": ("Category: **{name}** ({n})", "类别：**{name}**（{n}）"),
    "picker_no_results": ("No available skill matches `{query}`.", "没有可选技能匹配 `{query}`。"),
    "picker_option_desc": ("Base {base}", "基础值 {base}"),
    "picker_search_title": ("Search skills", "搜索技能"),
    "picker_search_label": ("Skill name (English or 中文)", "技能名称（中文或英文）"),
    "btn_prev_choice": ("◀ Choice", "◀ 上一项"),
    "btn_next_choice": ("Choice ▶", "下一项 ▶"),
    "btn_all_categories": ("All categories", "全部类别"),
    "btn_prev_page": ("◀ Page", "◀ 上一页"),
    "btn_next_page": ("Page ▶", "下一页 ▶"),
    "btn_back": ("Back", "返回"),
    "budget_occ": ("Occupation", "职业"),
    "budget_int": ("Personal interest", "兴趣"),
    "budget_used": ("{spent}/{total} used · **{left} left**", "已用 {spent}/{total} · **剩余 {left}**"),
    "alloc_page_field": ("Occupation skills (page {p}/{pages})", "本职技能（第 {p}/{pages} 页）"),
    "alloc_other_field": ("Other skills (personal interest)", "其他技能（兴趣点数）"),
    "alloc_how_field": ("How", "说明"),
    "alloc_how_set": ("**Mode: set totals.** Each form field is the total points that skill gets from that budget "
                      "(blank = 0). The whole form is checked together, so you can move points from one skill "
                      "to another in one go.",
                      "**模式：设定总数。** 表单中每一栏是该技能从此预算获得的点数总数（留空 = 0）。"
                      "整张表单一起检查，因此可以一次把点数从一个技能挪到另一个技能。"),
    "alloc_how_add": ("**Mode: add points.** Enter +10 or -5 per field (blank = no change). "
                      "The whole form is checked together.",
                      "**模式：增减点数。** 每一栏输入 +10 或 -5（留空 = 不变）。整张表单一起检查。"),
    "alloc_how_common": ("**Other skill** picks any skill for personal interest points. Max 99 per skill; "
                         "Credit Rating must end in the occupation's range.",
                         "**其他技能**可为任意技能分配兴趣点数。每项技能最高 99；信誉须在职业范围内。"),
    "btn_occ_points": ("Occupation points", "职业点数"),
    "btn_int_points": ("Interest points", "兴趣点数"),
    "btn_other_skill": ("Other skill", "其他技能"),
    "btn_mode_set": ("Mode: set totals", "模式：设定总数"),
    "btn_mode_add": ("Mode: add points", "模式：增减点数"),
    "formula_field": ("Skill-point formula", "职业点数公式"),
    "formula_choose": ("{formula}: you choose which characteristic counts. ", "{formula}：由你选择计入哪一项属性。"),
    "formula_chosen": ("Chosen: **{stat}**.", "已选择：**{stat}**。"),
    "formula_suggested": ("Using the suggestion **{stat}** until you choose.", "在你选择前使用建议值 **{stat}**。"),
    "stat_option": ("{stat}×{mult} = {value}", "{stat}×{mult} = {value}"),
    "stat_suggested": (" (suggested)", "（建议）"),
    "stat_placeholder": ("Characteristic for skill points", "用于职业点数的属性"),
    "stat_set": ("Skill points now use **{stat}**.", "职业点数现使用 **{stat}**。"),
    "modal_set_occ": ("Set occupation points · {left} left", "设定职业点数 · 剩余 {left}"),
    "modal_add_occ": ("Add occupation points · {left} left", "增减职业点数 · 剩余 {left}"),
    "modal_set_int": ("Set interest points · {left} left", "设定兴趣点数 · 剩余 {left}"),
    "modal_add_int": ("Add interest points · {left} left", "增减兴趣点数 · 剩余 {left}"),
    "field_label": ("{skill} · now {current} (base {base})", "{skill} · 当前 {current}（基础 {base}）"),
    "field_ph_set": ("Total points, 0-99", "点数总数，0-99"),
    "field_ph_add": ("+10 or -5", "+10 或 -5"),
    "result_saved": ("Saved.", "已保存。"),
    "result_nothing_changed": ("No changes.", "没有更改。"),
    "result_nothing_saved": ("Nothing was saved:", "未保存任何更改："),
    "change_line": ("{skill}: {budget} {old} → {new} ({delta})", "{skill}：{budget} {old} → {new}（{delta}）"),
    "remaining_line": ("Left: occupation {occ} · personal interest {int}", "剩余：职业 {occ} · 兴趣 {int}"),
    "interest_pick_header": ("Pick a skill to give personal interest points to.", "选择要分配兴趣点数的技能。"),
    "interest_modal_title": ("Interest points · {skill}", "兴趣点数 · {skill}"),
    "interest_modal_label_set": ("Total interest points (now {current}, {left} left)",
                                 "兴趣点数总数（当前 {current}，剩余 {left}）"),
    "interest_modal_label_add": ("Add or remove (now {current}, {left} left)", "增减点数（当前 {current}，剩余 {left}）"),
    "review_must_fix": ("**Must fix before finishing:**", "**完成前必须修正：**"),
    "review_ok": ("✅ Meets the occupation and skill-point rules checked by the bot.",
                  "✅ 符合机器人检查的职业与技能点规则。"),
    "review_notes": ("**Notes:**", "**备注：**"),
    "review_skills": ("Skills", "技能"),
    "review_manual_title": ("Hand-entered values to reconcile", "需处理的手动输入值"),
    "review_manual_body": ("These were typed in before the occupation was chosen, so they are not paid for with points. "
                           "Choose for each: **convert** (spend points to reach the same value, if the budgets and "
                           "rules allow) or **discard** (back to the base value). Nothing changes until you choose.",
                           "这些值是在选择职业前手动输入的，没有用点数购买。请逐项选择：**转换**（在预算和规则允许时"
                           "花费点数保留该值）或**舍弃**（恢复为基础值）。在你选择前不会有任何更改。"),
    "manual_line": ("• {skill}: {value} (base {base})", "• {skill}：{value}（基础 {base}）"),
    "btn_finish": ("Finish creation", "完成创建"),
    "btn_finish_unspent": ("Finish ({n} points unspent)…", "完成（{n} 点未分配）…"),
    "btn_finish_confirm": ("Confirm: finish and lose {n} points", "确认：完成并放弃 {n} 点"),
    "unspent_confirm": ("Unspent points are not carried over. Click again to finish anyway.",
                        "未分配的点数不会保留。再次点击以确认完成。"),
    "reconcile_ph": ("Reconcile a hand-entered value…", "处理手动输入的值…"),
    "reconcile_convert": ("Convert {skill} ({value})", "转换 {skill}（{value}）"),
    "reconcile_discard": ("Discard {skill} ({value})", "舍弃 {skill}（{value}）"),
    "reconcile_convert_desc": ("Spend points to keep this value", "花费点数保留该值"),
    "reconcile_discard_desc": ("Remove it; the skill returns to its base", "删除；技能恢复为基础值"),
    "reconciled_convert": ("{skill} converted into points.", "{skill} 已转换为点数。"),
    "reconciled_discard": ("{skill} discarded (back to base).", "{skill} 已舍弃（恢复为基础值）。"),
    "creation_finished": ("✅ **{name}** is created: occupation, skill choices and skill points meet the rules the bot "
                          "checks. Age adjustments are not automated yet; confirm them with your Keeper.",
                          "✅ **{name}** 创建完成：职业、技能选择和技能点数符合机器人检查的规则。"
                          "年龄调整尚未自动化，请与守密人确认。"),
    "table_skill": ("Skill", "技能"),
    "alloc_reply": ("**{inv}**: {skill} = **{value}**", "**{inv}**：{skill} = **{value}**"),
    "value_base": ("base", "基础值"),
    # --- language preference ------------------------------------------------------------
    "lang_set": ("Display language set to **{name}**. Searches still accept English and Chinese.",
                 "显示语言已设为 **{name}**。搜索仍可使用中文或英文。"),
    "lang_current": ("Your display language is **{name}**. Change it with `/language`.",
                     "你的显示语言为 **{name}**。可用 `/language` 更改。"),
}

# Params holding canonical skill keys, localized at render time.
_SKILL_PARAMS = {"skill"}


def fallback_name(key: str) -> str:
    return key.replace("_", " ").title()


def _render_one(code: str, lang: str, params: Mapping[str, Any], names: Optional[NamesByLang]) -> str:
    en, zh = CATALOG[code]
    template = zh if lang == "zh" else en
    names_l = (names or {}).get(lang) or {}
    names_en = (names or {}).get("en") or {}
    values = {}
    for k, v in params.items():
        if isinstance(v, Msg):
            values[k] = _render_one(v.code, lang, v.params, names)
        elif isinstance(v, (list, tuple)):
            values[k] = "\n- ".join(_render_one(m.code, lang, m.params, names) if isinstance(m, Msg) else str(m)
                                    for m in v)
        elif isinstance(v, dict) and ("en" in v or "zh" in v):
            values[k] = v.get(lang) or v.get("en") or v.get("zh") or ""
        elif k in _SKILL_PARAMS and isinstance(v, str):
            values[k] = names_l.get(v) or names_en.get(v) or fallback_name(v)
        else:
            values[k] = v
    return template.format(**values)


def render(code: str, lang: str = DEFAULT_LANG, names: Optional[NamesByLang] = None, **params: Any) -> str:
    """Render a catalog entry in ``lang`` ('en', 'zh' or 'both')."""
    if lang != "both":
        return _render_one(code, lang if lang in LANGS else DEFAULT_LANG, params, names)
    en = _render_one(code, "en", params, names)
    zh = _render_one(code, "zh", params, names)
    if en == zh:
        return en
    if "\n" not in en and "\n" not in zh and len(en) + len(zh) + 3 <= 80:
        return f"{en} / {zh}"
    return f"{en}\n{zh}"


def label(code: str, lang: str, limit: int, names: Optional[NamesByLang] = None, **params: Any) -> str:
    """Render for a length-limited Discord field; bilingual falls back to English if too long."""
    text = render(code, lang, names, **params)
    if len(text) > limit and lang == "both":
        text = render(code, "en", names, **params)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Msg(str):
    """English message text that remembers how to render itself in other languages."""

    code: str
    params: Dict[str, Any]

    def __new__(cls, code: str, names: Optional[Mapping[str, str]] = None, **params: Any) -> "Msg":
        if code not in CATALOG:
            raise KeyError(code)
        obj = super().__new__(cls, _render_one(code, "en", params, {"en": names or {}}))
        obj.code = code
        obj.params = params
        return obj

    def __reduce__(self):  # keep pickling simple: degrade to plain text
        return (str, (str(self),))


def localize(text: object, lang: str, names: Optional[NamesByLang] = None) -> str:
    """Render a Msg (or anything containing one) in ``lang``; plain strings pass through."""
    if isinstance(text, Msg):
        return render(text.code, lang, names, **text.params)
    return str(text)


# --- names --------------------------------------------------------------------------------

def skill_names_by_lang(conn: sqlite3.Connection) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {"en": {}, "zh": {}}
    for key, lang, name in conn.execute(
        "SELECT sd.key, i.lang, i.name FROM skill_defs sd JOIN skill_def_i18n i ON i.skill_id = sd.skill_id "
        "WHERE i.lang IN ('en', 'zh')"
    ):
        out[lang][key] = name
    return out


def skill_names(conn: sqlite3.Connection, lang: str) -> Dict[str, str]:
    """Display name per skill key in ``lang``; bilingual is 'English · 中文'."""
    by = skill_names_by_lang(conn)
    keys = [r[0] for r in conn.execute("SELECT key FROM skill_defs")]
    out = {}
    for k in keys:
        en = by["en"].get(k) or fallback_name(k)
        zh = by["zh"].get(k) or en
        out[k] = zh if lang == "zh" else (f"{en} · {zh}" if lang == "both" and zh != en else en)
    return out


def localized_names(conn: sqlite3.Connection) -> Dict[str, Dict[str, str]]:
    """Names usable as ``names`` for render(): both languages."""
    return skill_names_by_lang(conn)


def occupation_name(occ: Any, lang: str) -> str:
    en, zh = occ.name_en, (occ.name_zh or occ.name_en)
    if lang == "zh":
        return zh
    if lang == "both" and zh != en:
        return f"{en} / {zh}"
    return en
