"""The Chinese lunar calendar, for festivals that move: 春节, 中秋 and the rest.

The first day of every lunar month from 2025 to 2041, copied from the Hong Kong Observatory's
Gregorian-Lunar conversion tables (hko.gov.hk, T2025e.txt to T2041e.txt): the calendar that is
published, not one worked out here. A computed one can be a day off when the new moon falls near
midnight: the calendar data on the machine this was written on puts 春节 2027 and 2030 a day
wrong, and agrees with these tables on the other 208 month starts.

Outside the table a date has no lunar date, so lunar festivals simply don't come round: add the
years to STARTS before they run out (the bot's log says so in the table's last year).
"""
import bisect
from datetime import date, timedelta


# year -> "MM-DD month" for each lunar month that starts in it; "+" marks a leap month.
STARTS = {
    2025: "01-29 1, 02-28 2, 03-29 3, 04-28 4, 05-27 5, 06-25 6, 07-25 6+, 08-23 7, 09-22 8, 10-21 9, 11-20 10, 12-20 11",
    2026: "01-19 12, 02-17 1, 03-19 2, 04-17 3, 05-17 4, 06-15 5, 07-14 6, 08-13 7, 09-11 8, 10-10 9, 11-09 10, 12-09 11",
    2027: "01-08 12, 02-06 1, 03-08 2, 04-07 3, 05-06 4, 06-05 5, 07-04 6, 08-02 7, 09-01 8, 09-30 9, 10-29 10, 11-28 11, 12-28 12",
    2028: "01-26 1, 02-25 2, 03-26 3, 04-25 4, 05-24 5, 06-23 5+, 07-22 6, 08-20 7, 09-19 8, 10-18 9, 11-16 10, 12-16 11",
    2029: "01-15 12, 02-13 1, 03-15 2, 04-14 3, 05-13 4, 06-12 5, 07-11 6, 08-10 7, 09-08 8, 10-08 9, 11-06 10, 12-05 11",
    2030: "01-04 12, 02-03 1, 03-04 2, 04-03 3, 05-02 4, 06-01 5, 07-01 6, 07-30 7, 08-29 8, 09-27 9, 10-27 10, 11-25 11, 12-25 12",
    2031: "01-23 1, 02-21 2, 03-23 3, 04-22 3+, 05-21 4, 06-20 5, 07-19 6, 08-18 7, 09-17 8, 10-16 9, 11-15 10, 12-14 11",
    2032: "01-13 12, 02-11 1, 03-12 2, 04-10 3, 05-09 4, 06-08 5, 07-07 6, 08-06 7, 09-05 8, 10-04 9, 11-03 10, 12-03 11",
    2033: "01-01 12, 01-31 1, 03-01 2, 03-31 3, 04-29 4, 05-28 5, 06-27 6, 07-26 7, 08-25 8, 09-23 9, 10-23 10, 11-22 11, 12-22 11+",
    2034: "01-20 12, 02-19 1, 03-20 2, 04-19 3, 05-18 4, 06-16 5, 07-16 6, 08-14 7, 09-13 8, 10-12 9, 11-11 10, 12-11 11",
    2035: "01-09 12, 02-08 1, 03-10 2, 04-08 3, 05-08 4, 06-06 5, 07-05 6, 08-04 7, 09-02 8, 10-01 9, 10-31 10, 11-30 11, 12-29 12",
    2036: "01-28 1, 02-27 2, 03-28 3, 04-26 4, 05-26 5, 06-24 6, 07-23 6+, 08-22 7, 09-20 8, 10-19 9, 11-18 10, 12-17 11",
    2037: "01-16 12, 02-15 1, 03-17 2, 04-16 3, 05-15 4, 06-14 5, 07-13 6, 08-11 7, 09-10 8, 10-09 9, 11-07 10, 12-07 11",
    2038: "01-05 12, 02-04 1, 03-06 2, 04-05 3, 05-04 4, 06-03 5, 07-02 6, 08-01 7, 08-30 8, 09-29 9, 10-28 10, 11-26 11, 12-26 12",
    2039: "01-24 1, 02-23 2, 03-25 3, 04-23 4, 05-23 5, 06-22 5+, 07-21 6, 08-20 7, 09-18 8, 10-18 9, 11-16 10, 12-16 11",
    2040: "01-14 12, 02-12 1, 03-13 2, 04-11 3, 05-11 4, 06-10 5, 07-09 6, 08-08 7, 09-06 8, 10-06 9, 11-05 10, 12-04 11",
    2041: "01-03 12, 02-01 1, 03-02 2, 04-01 3, 04-30 4, 05-30 5, 06-28 6, 07-28 7, 08-27 8, 09-25 9, 10-25 10, 11-24 11, 12-23 12",
}
MONTH_NAMES = {"正月": 1, "一月": 1, "二月": 2, "三月": 3, "四月": 4, "五月": 5, "六月": 6, "七月": 7, "八月": 8,
               "九月": 9, "十月": 10, "十一月": 11, "冬月": 11, "十二月": 12, "腊月": 12}
DAY_NAMES = {name: n for n, name in enumerate(
    "初一 初二 初三 初四 初五 初六 初七 初八 初九 初十 十一 十二 十三 十四 十五 十六 十七 十八 十九 二十 "
    "廿一 廿二 廿三 廿四 廿五 廿六 廿七 廿八 廿九 三十".split(), 1)}
DAY_NAMES.update({"二十一": 21, "二十二": 22, "二十三": 23, "二十四": 24, "二十五": 25, "二十六": 26, "二十七": 27,
                  "二十八": 28, "二十九": 29, "卅": 30})


def _months():
    found = []
    for year, text in STARTS.items():
        for item in text.split(", "):
            day, month = item.split()
            found.append((date.fromisoformat(f"{year}-{day}"), int(month.rstrip("+")), month.endswith("+")))
    return sorted(found)


_MONTHS = _months()
_DAYS = [start for start, _, _ in _MONTHS]
FIRST = _DAYS[0]
# The last month in the table has no next one to say how long it is: its 30th day, if it has one, is left out.
LAST = _DAYS[-1] + timedelta(days=28)


def lunar(day):
    """(month, day of the month, whether it's a leap month) for a date, or None outside the table."""
    if not FIRST <= day <= LAST:
        return None
    start, month, leap = _MONTHS[bisect.bisect_right(_DAYS, day) - 1]
    return month, (day - start).days + 1, leap


def is_eve(day):
    """Whether a date is 除夕: the day before 正月初一, be it the 29th or the 30th of 腊月."""
    return lunar(day + timedelta(days=1)) == (1, 1, False)
