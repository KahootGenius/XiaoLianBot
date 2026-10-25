"""The weekly 群聊小报 post."""
from core.style import join, t
from modules.manager.achievements import BY_CODE


def _names(rows, limit):
    names = join([r["nickname"] or t("common.someone") for r in rows[:limit]])
    return names + (t("manager.digest.more_people", count=len(rows)) if len(rows) > limit else "")


def build(start, end, totals, previous_valid, busiest_hour, top, newcomers, badges, on_leave):
    """Compose the digest text from pre-computed numbers, in the current style.

    start/end are dates; top is [(nickname, count)]; newcomers and badges are
    store rows; on_leave is a count.
    """
    lines = [t("manager.digest.intro", start=f"{start.month}/{start.day}", end=f"{end.month}/{end.day}")]

    valid = totals["valid"]
    trend = ""
    if previous_valid:
        change = round((valid - previous_valid) * 100 / previous_valid)
        trend = t("manager.digest.trend", change=f"{'+' if change >= 0 else ''}{change}")
    lines.append(t("manager.digest.messages", count=valid, trend=trend, speakers=totals["speakers"]))
    if busiest_hour is not None:
        lines.append(t("manager.digest.busiest", hour=busiest_hour))
    if top:
        items = [t("manager.digest.top_item", member=name, count=count) for name, count in top]
        lines.append(t("manager.digest.top", list=join(items)))
    if newcomers:
        lines.append(t("manager.digest.newcomers", names=_names(newcomers, 5)))
    announced = [b for b in badges if BY_CODE.get(b["code"]) and BY_CODE[b["code"]].announce]
    if announced:
        items = [t("manager.digest.badge_item", member=b["nickname"] or t("common.someone"),
                   badge=BY_CODE[b["code"]].name) for b in announced[:4]]
        extra = t("manager.digest.more_badges", count=len(announced)) if len(announced) > 4 else ""
        lines.append(t("manager.digest.badges", list=join(items) + extra))
    if on_leave:
        lines.append(t("manager.digest.on_leave", count=on_leave))
    lines.append(t("manager.digest.outro"))
    return "\n".join(lines)
