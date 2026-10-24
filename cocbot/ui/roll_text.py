"""Text for roll results, Luck prompts and `/rolls` (no Discord dependency)."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from cocbot.i18n import render
from cocbot.rolls.rules import LuckOption
from cocbot.rolls.service import LuckStatus, Roll, RollEvent


def difficulty_name(difficulty: str, lang: str) -> str:
    return render(f"diff_{difficulty}", lang)


def level_name(level, lang: str) -> str:
    return render(f"lvl_{level.name}", lang)


def result_lines(roll: Roll, lang: str) -> List[str]:
    """Lines added under the dice: Luck spent (if any) and whether the required difficulty was met."""
    lines: List[str] = []
    if roll.luck_spent:
        lines.append(render("roll_luck_line", lang, cost=roll.luck_spent, natural=f"{roll.natural:02d}",
                            effective=f"{roll.effective:02d}", level=level_name(roll.effective_level, lang)))
        lines.append(render("roll_luck_note", lang, natural=f"{roll.natural:02d}",
                            level=level_name(roll.natural_level, lang)))
    if roll.difficulty != "regular" or roll.luck_spent:
        verdict = render("roll_met" if roll.met else "roll_not_met", lang)
        lines.append(render("roll_required", lang, difficulty=difficulty_name(roll.difficulty, lang),
                            verdict=verdict))
    return lines


def option_label(option: LuckOption, luck: int, lang: str) -> str:
    return render("luck_option", lang, level=level_name(option.level, lang), cost=option.cost,
                  effective=f"{option.effective:02d}", after=luck - option.cost)


def luck_prompt(status: LuckStatus, lang: str, campaign_name: Optional[str]) -> str:
    roll = status.roll
    lines = [render("luck_prompt", lang, id=roll.id, subject=roll.subject_label, natural=f"{roll.natural:02d}",
                    target=roll.target, luck=status.current_luck)]
    lines += [f"• {option_label(o, status.current_luck or 0, lang)}" for o in status.options]
    if roll.luck_scope == "campaign" and campaign_name:
        lines.append(render("luck_campaign_note", lang, campaign=campaign_name))
    else:
        lines.append(render("luck_personal_note", lang))
    return "\n".join(lines)


def recent_line(roll: Roll, lang: str, campaign_names: Optional[dict] = None) -> str:
    stamp = roll.created_at[5:16].replace("T", " ")
    who = f" · {roll.investigator_name}" if roll.investigator_name else ""
    diff = f" ({difficulty_name(roll.difficulty, lang)})" if roll.difficulty != "regular" else ""
    result = f"{roll.natural:02d}"
    if roll.luck_spent:
        result += f" → {roll.effective:02d} 🍀{roll.luck_spent}"
    verdict = "✅" if roll.met else "❌"
    tags = []
    if roll.private:
        tags.append(render("rolls_private", lang))
    if roll.campaign_id and campaign_names and roll.campaign_id in campaign_names:
        tags.append(campaign_names[roll.campaign_id])
    tail = f" · {' · '.join(tags)}" if tags else ""
    return (f"`#{roll.id}` {stamp}{who} · {roll.subject_label} {roll.target}{diff} · {result} · "
            f"{level_name(roll.effective_level, lang)} {verdict}{tail}")


def detail_fields(roll: Roll, events: Sequence[RollEvent], status: Optional[LuckStatus], lang: str,
                  campaign_name: Optional[str]) -> List[Tuple[str, str]]:
    d = roll.dice
    if roll.bonus_penalty:
        mode = render("rolls_bonus" if roll.bonus_penalty > 0 else "rolls_penalty", lang)
        dice_text = render("rolls_bp", lang, mode=mode, count=abs(roll.bonus_penalty),
                           candidates=", ".join(f"{c:02d}" for c in d.get("candidates", [])), ones=d.get("ones"))
    else:
        dice_text = f"{roll.natural:02d}"
    dice_text += f" → {level_name(roll.natural_level, lang)}"
    context = [campaign_name or render("rolls_personal", lang)]
    if roll.rules_version:
        context.append(f"rules v{roll.rules_version}")
    context.append(render("rolls_private" if roll.private else "rolls_public", lang))
    context.append(roll.created_at[:19].replace("T", " ") + " UTC")
    changes = [render("roll_luck_line", lang, cost=e.luck_before - e.luck_after, natural=f"{e.old_effective:02d}",
                      effective=f"{e.new_effective:02d}", level=render(f"lvl_{e.new_level}", lang))
               + f" · {e.created_at[11:19]}" for e in events] or [render("rolls_none", lang)]
    fields = [(render("rolls_dice", lang), dice_text), (render("rolls_context", lang), " · ".join(context)),
              (render("rolls_changes", lang), "\n".join(changes))]
    if roll.subject_kind == "skill":
        if roll.improvement_eligible:
            imp = render("rolls_improvement_yes", lang)
        elif roll.met and roll.luck_spent:
            imp = render("rolls_improvement_no_luck", lang)
        else:
            imp = render("rolls_improvement_no", lang)
        fields.append(("📈", render("rolls_improvement", lang, status=imp)))
    if status is not None:
        state = render("rolls_luck_available", lang) if status.eligible else render(status.reason, lang)
        fields.append(("🍀", render("rolls_luck_status", lang, status=state)))
    return [(n[:256], v[:1024]) for n, v in fields]
