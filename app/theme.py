"""Altair 차트용 색: style.css / tokens.css :root 토큰(Ultraviolet, lavender)의 hex 사본."""
PAPER = "#f0ecfa"
PAPER_2 = "#e7e1f6"
INK = "#131019"
INK_2 = "#2b2636"
MUTED = "#5c5270"
RULE = "#cec5e1"
RULE_2 = "#a295cb"
ACCENT = "#6b2ae3"
ACCENT_SOFT = "#d4c7ff"
FONT = "Onest, 'Pretendard Variable', Pretendard, sans-serif"
# 대분류·사유 색: 바이올렛 한 계열의 명도 단계 (잉크 → 연보라). 강조색은 그중 한 칸.
SERIES = ["#2b2636", "#6b2ae3", "#a295cb", "#5c5270", "#d4c7ff", "#8a7fa6", "#cec5e1"]


def altair_theme():
    return {
        "config": {
            "background": PAPER,
            "font": FONT,
            "view": {"stroke": None},
            "axis": {"labelColor": MUTED, "titleColor": MUTED, "gridColor": RULE, "domainColor": RULE_2,
                     "tickColor": RULE_2, "labelFontSize": 12, "titleFontSize": 12, "titleFontWeight": 500},
            "legend": {"labelColor": INK_2, "titleColor": MUTED, "labelFontSize": 12, "titleFontSize": 12,
                       "titleFontWeight": 500, "orient": "top"},
            "range": {"category": SERIES},
            "bar": {"color": ACCENT},
            "line": {"color": ACCENT, "strokeWidth": 2},
        }
    }
