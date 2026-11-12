"""Altair 차트용 색: tokens.css :root 토큰(흰 바탕 + 따뜻한 4색)의 hex 사본."""
PAPER = "#ffffff"
PAPER_2 = "#faf6f1"
INK = "#3b2a20"      # 짙은 갈색
INK_2 = "#4e3b2f"
MUTED = "#7a6a5e"
RULE = "#ece3d9"
RULE_2 = "#d9c7b4"
ACCENT = "#b84a32"   # 테라코타
AMBER = "#e8a33d"    # 황토
SAND = "#e6d5c0"     # 모래
FONT = "Onest, 'Pretendard Variable', Pretendard, sans-serif"
# 차트는 이 4색만 쓴다: 갈색, 테라코타, 황토, 모래
SERIES = [INK, ACCENT, AMBER, SAND]


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
