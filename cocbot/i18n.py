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
    # --- campaigns: errors -----------------------------------------------------------------
    "campaign_not_found": ("Campaign not found, or you are not a member of it.", "找不到该团，或你不是该团成员。"),
    "keeper_only": ("Only this campaign's Keepers can do that.", "只有本团的守密人可以执行此操作。"),
    "primary_keeper_only": ("Only this campaign's primary Keeper can do that.", "只有本团的主守密人可以执行此操作。"),
    "reason_too_long": ("Reason must be at most {max} characters.", "原因最多 {max} 个字符。"),
    "campaign_name_invalid": ("Campaign name must be 1-{max} characters.", "团名须为 1–{max} 个字符。"),
    "channel_taken": ("This channel already belongs to another campaign.", "该频道已属于其他团。"),
    "channel_not_linked": ("This channel is not linked to the campaign.", "该频道未关联到本团。"),
    "cannot_invite_self": ("You cannot invite yourself.", "不能邀请自己。"),
    "already_member": ("That user is already a member of this campaign.", "该用户已是本团成员。"),
    "join_needs_ready": ("Only finished investigators can join a campaign (finish the sheet first).",
                         "只有已完成的调查员才能加入团（请先完成角色卡）。"),
    "keepers_do_not_join": ("Keepers of this campaign do not join it with an investigator.",
                            "本团守密人不能以调查员身份加入。"),
    "already_participating": ("You already play an investigator in this campaign. Leave first to switch.",
                              "你已在本团中使用一名调查员。如需更换，请先退出。"),
    "no_request": ("That user has no pending request to join.", "该用户没有待处理的加入申请。"),
    "request_investigator_unavailable": ("The investigator in that request is no longer available.",
                                         "该申请中的调查员已不可用。"),
    "not_a_member": ("That user is not a member of this campaign.", "该用户不是本团成员。"),
    "remove_keeper_via_assistant": ("Keepers are managed with /keeper assistant.", "守密人请通过 /keeper assistant 管理。"),
    "primary_cannot_leave": ("The primary Keeper cannot leave the campaign.", "主守密人不能退出本团。"),
    "cannot_change_own_role": ("You cannot change your own role.", "不能更改自己的角色。"),
    "assistant_is_player": ("That user plays an investigator here; remove them as a player first.",
                            "该用户正在本团中使用调查员；请先将其作为玩家移除。"),
    "not_an_assistant": ("That user is not an assistant Keeper.", "该用户不是助理守密人。"),
    "not_in_campaign": ("That investigator is not participating in this campaign.", "该调查员没有参与本团。"),
    "unknown_resource": ("Unknown value {field}.", "未知数值：{field}。"),
    "resource_unknown_max": ("{field} has no maximum yet (characteristics missing).", "{field} 尚无上限（缺少属性）。"),
    "resource_out_of_range": ("{field} must be between 0 and {max} (got {value}).",
                              "{field} 须在 0 到 {max} 之间（输入为 {value}）。"),
    "confirm_needed": ("This change lowers a value or reaches 0. Confirm to apply it.",
                       "此更改会降低数值或降至 0，请确认后应用。"),
    "condition_invalid": ("Condition label must be 1-{max} characters.", "状态标签须为 1–{max} 个字符。"),
    "condition_duplicate": ("That condition is already active.", "该状态已存在。"),
    "condition_limit": ("At most {max} active conditions per investigator.", "每名调查员最多 {max} 个状态。"),
    "condition_not_found": ("Condition not found (it may already be removed).", "找不到该状态（可能已被移除）。"),
    "event_not_found": ("History entry not found.", "找不到该历史记录。"),
    "already_reverted": ("That change was already reverted.", "该更改已被撤销。"),
    "not_revertible": ("Only resource and condition changes can be reverted (not reverts themselves).",
                       "只能撤销数值和状态的更改（撤销操作本身不能再撤销）。"),
    "changed_since": ("{field} changed again since (now {current}, that change set {expected}); "
                      "adjust it directly instead.",
                      "{field} 之后已再次更改（当前 {current}，该记录设为 {expected}）；请直接调整。"),
    "changed_since_condition": ("That condition changed again since; adjust it directly instead.",
                                "该状态之后已再次更改；请直接调整。"),
    "rules_unknown": ("Unsupported rule setting(s): {keys}.", "不支持的规则设置：{keys}。"),
    "rules_critical_range": ("Critical range must be between {lo} and {hi}.", "大成功范围须在 {lo} 到 {hi} 之间。"),
    "rules_fumble_mode": ("Fumble rule must be one of: {modes}.", "大失败规则须为以下之一：{modes}。"),
    "rules_visibility_mode": ("Roll visibility must be one of: {modes}.", "检定可见性须为以下之一：{modes}。"),
    # --- rolls and Luck spending ---------------------------------------------------------
    "difficulty_invalid": ("Difficulty must be one of: {modes}.", "难度须为以下之一：{modes}。"),
    "roll_not_found": ("No such roll of yours.", "找不到你的这次检定。"),
    "roll_changed": ("This roll changed since this prompt was shown. Nothing was spent; open it again.",
                     "此提示显示后检定已有变化，未消耗幸运；请重新打开。"),
    "luck_changed": ("Your Luck changed while confirming. Nothing was spent; try again.",
                     "确认期间你的幸运值已变化，未消耗幸运；请重试。"),
    "luck_not_on_pushed": ("Luck cannot be spent on a pushed roll.", "孤注一掷的检定不能消耗幸运。"),
    "luck_needs_investigator": ("Luck can only be spent on your investigator's own skill or characteristic rolls.",
                                "只能在你的调查员自己的技能或属性检定上消耗幸运。"),
    "luck_not_on_luck": ("Luck cannot be spent on Luck rolls.", "幸运检定不能消耗幸运。"),
    "luck_not_on_san": ("Luck cannot be spent on Sanity rolls.", "理智检定不能消耗幸运。"),
    "luck_rule_off": ("Luck spending is not enabled for this roll (campaign rule).",
                      "此检定未启用幸运消耗（团规则）。"),
    "luck_already_spent": ("Luck was already spent on this roll.", "此检定已消耗过幸运。"),
    "luck_not_on_fumble": ("A fumble cannot be changed with Luck.", "大失败不能用幸运改变。"),
    "luck_already_met": ("This roll already meets the required difficulty.", "此检定已达到所需难度。"),
    "luck_not_latest": ("You have rolled again with this investigator since; Luck can only be spent on the latest roll.",
                        "此后你已用该调查员再次检定；只能在最近一次检定上消耗幸运。"),
    "luck_expired": ("Luck must be spent right after the roll; this one is too old.",
                     "幸运须在检定后立即消耗；此检定已过时。"),
    "luck_unavailable": ("This investigator's Luck is not available here (left the campaign, or archived).",
                         "此处无法使用该调查员的幸运（已退出团或已归档）。"),
    "luck_unreachable": ("The required difficulty cannot be reached at this target, even with Luck.",
                         "以此目标值即使消耗幸运也无法达到所需难度。"),
    "luck_not_enough": ("Not enough Luck to reach the required difficulty.", "幸运不足以达到所需难度。"),
    "diff_regular": ("Regular", "常规"),
    "diff_hard": ("Hard", "困难"),
    "diff_extreme": ("Extreme", "极难"),
    "lvl_CRITICAL": ("Critical success", "大成功"),
    "lvl_EXTREME": ("Extreme success", "极难成功"),
    "lvl_HARD": ("Hard success", "困难成功"),
    "lvl_SUCCESS": ("Regular success", "常规成功"),
    "lvl_FAIL": ("Failure", "失败"),
    "lvl_FUMBLE": ("Fumble", "大失败"),
    "roll_required": ("Required: **{difficulty}** — {verdict}", "所需难度：**{difficulty}** — {verdict}"),
    "roll_met": ("✅ met", "✅ 达成"),
    "roll_not_met": ("❌ not met", "❌ 未达成"),
    "roll_luck_line": ("🍀 Luck spent: {cost} — {natural} → **{effective}** ({level})",
                       "🍀 消耗幸运：{cost} — {natural} → **{effective}**（{level}）"),
    "roll_luck_note": ("Rolled {natural} ({level}); the original roll is kept.", "原始掷骰 {natural}（{level}），已保留。"),
    "roll_footer": ("Roll #{id}", "检定 #{id}"),
    "luck_button": ("Spend Luck…", "消耗幸运…"),
    "luck_prompt": ("Spend Luck on roll #{id} ({subject}, rolled {natural} vs {target})? Your Luck: {luck}.",
                    "在检定 #{id}（{subject}，掷出 {natural}，目标 {target}）上消耗幸运？当前幸运：{luck}。"),
    "luck_option": ("{level}: spend {cost} → {effective} (Luck {after})", "{level}：消耗 {cost} → {effective}（幸运剩 {after}）"),
    "luck_option_short": ("{difficulty}: −{cost} Luck", "{difficulty}：−{cost} 幸运"),
    "luck_campaign_note": ("This spends your campaign Luck in **{campaign}**; your personal sheet is unchanged.",
                           "这会消耗你在 **{campaign}** 中的团幸运；个人角色卡不受影响。"),
    "luck_personal_note": ("This spends your investigator's personal Luck.", "这会消耗你的调查员的个人幸运。"),
    "luck_spent_done": ("🍀 Spent {cost} Luck: {natural} → {effective} ({level}). Luck {before} → {after}.",
                        "🍀 已消耗 {cost} 幸运：{natural} → {effective}（{level}）。幸运 {before} → {after}。"),
    "luck_not_yours": ("Only the player who rolled can spend Luck on this roll.", "只有掷骰的玩家才能在此检定上消耗幸运。"),
    "rolls_title": ("🎲 Your recent rolls", "🎲 你最近的检定"),
    "rolls_empty": ("You have no recorded rolls yet.", "你还没有检定记录。"),
    "rolls_detail_title": ("🎲 Roll #{id} — {subject}", "🎲 检定 #{id} — {subject}"),
    "rolls_private": ("private", "暗骰"),
    "rolls_public": ("public", "公开"),
    "rolls_dice": ("Dice", "骰子"),
    "rolls_context": ("Context", "场合"),
    "rolls_changes": ("Changes", "变更"),
    "rolls_none": ("none", "无"),
    "rolls_personal": ("personal", "个人"),
    "rolls_bp": ("{mode} dice {count}: {candidates} (units {ones})", "{mode}骰 {count}：{candidates}（个位 {ones}）"),
    "rolls_bonus": ("bonus", "奖励"),
    "rolls_penalty": ("penalty", "惩罚"),
    "rolls_luck_status": ("Luck: {status}", "幸运：{status}"),
    "rolls_luck_available": ("can be spent (see the roll's button)", "可消耗（使用检定上的按钮）"),
    "ev_luck_spend": ("spent Luck {old} → {new}", "消耗幸运 {old} → {new}"),
    "rules_luck_spending": ("Luck spending must be on or off.", "幸运消耗须为开启或关闭。"),
    "in_campaign": ("{name} is playing in a campaign ({campaigns}). Leave the campaign first.",
                    "{name} 正在参与团（{campaigns}）。请先退出该团。"),
    "adjust_invalid": ("Enter a number like `7`, `+2` or `-3` for {field}.",
                       "{field} 请输入数字，例如 `7`、`+2` 或 `-3`。"),
    "changed_while_confirming": ("{field} changed to {current} while you were confirming (you saw {expected}). "
                                 "Nothing was saved; refresh and try again.",
                                 "确认期间 {field} 已变为 {current}（你看到的是 {expected}）。未保存，请刷新后重试。"),
    "adjust_nothing": ("Nothing to change.", "没有需要更改的内容。"),
    "user_mention_invalid": ("Give a user mention or ID.", "请提供用户提及或用户 ID。"),
    # --- campaigns & Keeper Console UI ----------------------------------------------------
    "res_hp": ("HP", "HP"),
    "res_mp": ("MP", "MP"),
    "res_san": ("SAN", "理智"),
    "res_luck": ("Luck", "幸运"),
    "sheet_campaign_title": ("🎭 Campaign: {name}", "🎭 团：{name}"),
    "sheet_campaign_text": ("HP, MP, SAN and Luck above are this campaign's values, managed by the Keeper. "
                            "Your personal sheet is unchanged.",
                            "上方的 HP、MP、理智和幸运为本团数值，由守秘人管理。你的个人角色卡不受影响。"),
    "sheet_conditions": ("Conditions: {labels}", "状态：{labels}"),
    "kp_roster_title": ("🕯️ Keeper Console — {name}", "🕯️ 守秘人控制台 — {name}"),
    "kp_roster_empty": ("No investigators yet. Invite players with `/keeper invite`; they join with `/campaign join`.",
                        "暂无调查员。用 `/keeper invite` 邀请玩家，玩家用 `/campaign join` 加入。"),
    "kp_pending": ("⏳ Join requests: {count}", "⏳ 待审批加入请求：{count}"),
    "kp_footer": ("Rules v{version} · {rules} · your role: {role}",
                  "规则 v{version} · {rules} · 你的身份：{role}"),
    "kp_role_keeper": ("Keeper", "守秘人"),
    "kp_role_assistant": ("Assistant Keeper", "助理守秘人"),
    "kp_role_player": ("Player", "玩家"),
    "kp_pick_investigator": ("Inspect an investigator…", "查看调查员…"),
    "kp_pick_request": ("Review a join request…", "审核加入请求…"),
    "kp_btn_refresh": ("Refresh", "刷新"),
    "kp_btn_back": ("Back", "返回"),
    "kp_btn_adjust": ("Adjust HP·MP·SAN·Luck", "调整 HP·MP·理智·幸运"),
    "kp_btn_add_condition": ("Add condition", "添加状态"),
    "kp_btn_remove_condition": ("Remove condition…", "移除状态…"),
    "kp_btn_history": ("History", "记录"),
    "kp_btn_campaign_history": ("Campaign history", "团记录"),
    "kp_btn_confirm": ("Confirm", "确认"),
    "kp_btn_cancel": ("Cancel", "取消"),
    "kp_btn_approve": ("Approve", "批准"),
    "kp_btn_deny": ("Deny", "拒绝"),
    "kp_pick_revert": ("Revert a change…", "撤销某项更改…"),
    "kp_detail_player": ("Player: {user}", "玩家：{user}"),
    "kp_detail_resources": ("Session resources", "本团资源"),
    "kp_detail_conditions": ("Conditions", "状态"),
    "kp_none": ("none", "无"),
    "kp_recent": ("Recent changes", "最近更改"),
    "kp_no_history": ("No recorded changes yet.", "暂无记录。"),
    "kp_history_title": ("📜 History — {name}", "📜 记录 — {name}"),
    "kp_adjust_title": ("Adjust {name}", "调整 {name}"),
    "kp_adjust_hint": ("blank = keep · 7 = set · +2 / -3 = change", "留空=不变 · 7=设为 · +2/-3=增减"),
    "kp_reason": ("Reason (optional, logged)", "原因（可选，会记录）"),
    "kp_condition_label": ("Condition label", "状态名称"),
    "kp_confirm_changes": ("Confirm these changes to **{name}**:", "确认对 **{name}** 的以下更改："),
    "kp_confirm_revert": ("Revert this change? {line}", "撤销这项更改？{line}"),
    "kp_confirm_remove_condition": ("Remove condition **{label}** from **{name}**?",
                                    "从 **{name}** 移除状态 **{label}**？"),
    "kp_change_line": ("{field}: {old} → {new}{max}", "{field}：{old} → {new}{max}"),
    "kp_reaches_zero": (" ⚠️ reaches 0", " ⚠️ 降至 0"),
    "kp_applied": ("✅ Saved.", "✅ 已保存。"),
    "kp_cancelled": ("Cancelled — nothing changed.", "已取消，未作任何更改。"),
    "kp_reverted": ("↩️ Change reverted.", "↩️ 已撤销更改。"),
    "kp_approved": ("✅ Request approved.", "✅ 已批准请求。"),
    "kp_denied": ("Request denied.", "已拒绝请求。"),
    "kp_request_line": ("{user} wants to join with **{name}**.", "{user} 申请以 **{name}** 加入。"),
    "kp_request_gone": ("{user} (investigator no longer available)", "{user}（该调查员已不可用）"),
    "kp_no_campaigns": ("You are not a Keeper of any campaign. Create one with `/campaign create`.",
                        "你不是任何团的守秘人。可用 `/campaign create` 创建。"),
    "kp_choose_campaign": ("You keep several campaigns; name one with the `campaign` option.",
                           "你管理多个团，请在 `campaign` 选项中指定一个。"),
    "ev_created": ("created the campaign", "创建了团"),
    "ev_channel_link": ("linked channel {new}", "关联了频道 {new}"),
    "ev_channel_unlink": ("unlinked channel {old}", "取消关联频道 {old}"),
    "ev_invite": ("invited {new}", "邀请了 {new}"),
    "ev_request": ("asked to join", "申请加入"),
    "ev_join": ("joined", "加入"),
    "ev_deny": ("denied {new}", "拒绝了 {new}"),
    "ev_remove": ("removed {old}", "移除了 {old}"),
    "ev_leave": ("left", "退出"),
    "ev_assistant_add": ("made {new} assistant Keeper", "任命 {new} 为助理守秘人"),
    "ev_assistant_remove": ("removed assistant Keeper {old}", "撤销 {old} 的助理守秘人身份"),
    "ev_resource": ("{field} {old} → {new}", "{field} {old} → {new}"),
    "ev_condition_add": ("added condition {new}", "添加状态 {new}"),
    "ev_condition_remove": ("removed condition {old}", "移除状态 {old}"),
    "ev_revert": ("reverted #{ref}", "撤销了 #{ref}"),
    "ev_rules": ("changed rules", "修改了规则"),
    "ev_roll": ("private roll: {new}", "暗骰：{new}"),
    "ev_other": ("{kind}", "{kind}"),
    "ev_reverted_mark": (" (reverted)", "（已撤销）"),
    "rules_summary": ("critical 01-{critical} · fumble {fumble} · rolls {visibility} · Luck spending {luck}",
                      "大成功 01-{critical} · 大失败 {fumble} · 检定{visibility} · 幸运消耗{luck}"),
    "rules_on": ("on", "开启"),
    "rules_off": ("off", "关闭"),
    "rules_fumble_raw": ("RAW (96-100 below 50)", "规则书（技能<50 时 96-100）"),
    "rules_fumble_only_100": ("100 only", "仅 100"),
    "rules_vis_public": ("public", "公开"),
    "rules_vis_private": ("private (roller + Keeper log)", "暗骰（仅掷骰者可见，记入守秘人记录）"),
    "camp_created": ("🎭 Campaign **{name}** created (#{id}). {channel}Invite players with `/keeper invite`.",
                     "🎭 已创建团 **{name}**（#{id}）。{channel}用 `/keeper invite` 邀请玩家。"),
    "camp_created_channel": ("This channel is linked to it. ", "本频道已与之关联。"),
    "camp_info_title": ("🎭 {name}", "🎭 {name}"),
    "camp_info_none": ("This channel is not linked to a campaign.", "本频道未关联任何团。"),
    "camp_info_body": ("Keeper: {keeper}\nYour role: {role}\nRules v{version}: {rules}",
                       "守秘人：{keeper}\n你的身份：{role}\n规则 v{version}：{rules}"),
    "camp_info_playing": ("Your investigator here: **{name}**", "你在本团的调查员：**{name}**"),
    "camp_joined": ("✅ **{name}** joined **{campaign}**. Campaign HP/MP/SAN/Luck start from your current values.",
                    "✅ **{name}** 已加入 **{campaign}**。本团 HP/MP/理智/幸运以你当前数值为起点。"),
    "camp_requested": ("⏳ Request sent. A Keeper of **{campaign}** must approve it.",
                       "⏳ 已发送申请，需由 **{campaign}** 的守秘人批准。"),
    "camp_left": ("You left **{campaign}**. Its campaign values are kept if you return.",
                  "你已退出 **{campaign}**。若返回，本团数值仍会保留。"),
    "kp_invited": ("✉️ {user} is invited to **{campaign}**. They join with `/campaign join`.",
                   "✉️ 已邀请 {user} 加入 **{campaign}**，对方可用 `/campaign join` 加入。"),
    "kp_invite_approved": ("✅ {user}'s pending request to **{campaign}** is approved.",
                           "✅ 已批准 {user} 加入 **{campaign}** 的申请。"),
    "kp_removed": ("{user} was removed from **{campaign}**.", "已将 {user} 移出 **{campaign}**。"),
    "kp_confirm_remove_player": ("Remove {user} from **{campaign}**? Their investigator leaves the roster; "
                                 "campaign values are kept if they return.",
                                 "将 {user} 移出 **{campaign}**？其调查员会离开名单；若返回，本团数值保留。"),
    "kp_assistant_added": ("{user} is now an assistant Keeper of **{campaign}**.",
                           "{user} 现为 **{campaign}** 的助理守秘人。"),
    "kp_assistant_removed": ("{user} is no longer an assistant Keeper of **{campaign}**.",
                             "{user} 不再是 **{campaign}** 的助理守秘人。"),
    "kp_channel_linked": ("This channel is now linked to **{campaign}**.", "本频道已关联 **{campaign}**。"),
    "kp_channel_unlinked": ("This channel is no longer linked to **{campaign}**.", "本频道已取消关联 **{campaign}**。"),
    "kp_rules_now": ("Rules for **{campaign}** — v{version}: {rules}", "**{campaign}** 的规则 — v{version}：{rules}"),
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
