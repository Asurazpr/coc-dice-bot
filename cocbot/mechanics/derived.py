import re
from typing import Optional, Tuple, Dict

STAT_KEYS = {"STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU"}

def eval_derived_formula(formula: str, stats: Dict[str, int]) -> Tuple[Optional[int], str]:
    """
    Supports:
      - STAT (e.g., EDU)
      - STAT/n (e.g., DEX/2)
    Returns (value_or_None, explanation).
    """
    f = (formula or "").strip().upper()
    if not f:
        return None, ""

    if f in STAT_KEYS:
        v = stats.get(f)
        if v is None:
            return None, f"{f} missing"
        return int(v), f"{f}={v}"

    m = re.fullmatch(r"(STR|CON|SIZ|DEX|APP|INT|POW|EDU)\s*/\s*(\d+)", f)
    if m:
        stat = m.group(1)
        div = int(m.group(2))
        v = stats.get(stat)
        if v is None:
            return None, f"{stat} missing"
        return int(v // div), f"{stat}={v} → {stat}/{div}={v//div}"

    return None, f"unsupported: {formula}"
