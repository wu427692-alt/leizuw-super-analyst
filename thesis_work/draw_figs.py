import sys
from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
S = 2  # supersample scale relative to original pixel size


def font(px):
    return ImageFont.truetype(FONT, int(px * S))


def P(v):
    return int(v * S)


def box(d, x0, y0, x1, y1, text=None, f=None, fill="white", width=2, dash=False, lines=None):
    if dash:
        dashed_rect(d, x0, y0, x1, y1)
    else:
        d.rectangle([P(x0), P(y0), P(x1), P(y1)], outline="black", width=P(width), fill=fill)
    if text is not None:
        center_text(d, (x0 + x1) / 2, (y0 + y1) / 2, text, f)


def center_text(d, cx, cy, text, f):
    lines = text.split("\n")
    lh = f.size * 1.35
    total = lh * len(lines)
    for i, ln in enumerate(lines):
        w = d.textlength(ln, font=f)
        y = P(cy) - total / 2 + i * lh + (lh - f.size) / 2 - f.size * 0.08
        d.text((P(cx) - w / 2, y), ln, font=f, fill="black")


def dashed_line(d, x0, y0, x1, y1, dash=8, gap=6, width=1.5):
    import math
    L = math.hypot(x1 - x0, y1 - y0)
    n = int(L // (dash + gap)) + 1
    for i in range(n):
        s = i * (dash + gap)
        e = min(s + dash, L)
        if s >= L:
            break
        d.line([P(x0 + (x1 - x0) * s / L), P(y0 + (y1 - y0) * s / L),
                P(x0 + (x1 - x0) * e / L), P(y0 + (y1 - y0) * e / L)], fill="black", width=P(width))


def dashed_rect(d, x0, y0, x1, y1):
    dashed_line(d, x0, y0, x1, y0)
    dashed_line(d, x1, y0, x1, y1)
    dashed_line(d, x1, y1, x0, y1)
    dashed_line(d, x0, y1, x0, y0)


def arrow(d, x0, y0, x1, y1, width=2, head=12):
    import math
    d.line([P(x0), P(y0), P(x1), P(y1)], fill="black", width=P(width))
    ang = math.atan2(y1 - y0, x1 - x0)
    a1 = ang + math.radians(155)
    a2 = ang - math.radians(155)
    pts = [(P(x1), P(y1)),
           (P(x1 + head * math.cos(a1)), P(y1 + head * math.sin(a1))),
           (P(x1 + head * math.cos(a2)), P(y1 + head * math.sin(a2)))]
    d.polygon(pts, fill="black")


def brace(d, x, y0, y1, depth=14):
    # left curly brace "{" opening to the left (as in the original figure)
    ym = (y0 + y1) / 2
    r = depth
    w = P(1.5)
    d.arc([P(x), P(y0), P(x + 2 * r), P(y0 + 2 * r)], 180, 270, fill="black", width=w)
    d.line([P(x), P(y0 + r), P(x), P(ym - r)], fill="black", width=w)
    d.arc([P(x - 2 * r), P(ym - 2 * r), P(x), P(ym)], 0, 90, fill="black", width=w)
    d.arc([P(x - 2 * r), P(ym), P(x), P(ym + 2 * r)], 270, 360, fill="black", width=w)
    d.line([P(x), P(ym + r), P(x), P(y1 - r)], fill="black", width=w)
    d.arc([P(x), P(y1 - 2 * r), P(x + 2 * r), P(y1)], 90, 180, fill="black", width=w)


def tech_route(out):
    W_, H_ = 1039, 1377
    im = Image.new("RGB", (P(W_), P(H_)), "white")
    d = ImageDraw.Draw(im)
    f = font(18)
    cx = 650
    stages = [
        (30, 255, "基础理论"),
        (295, 535, "模式归纳与\n模型构建"),
        (575, 895, "实验设计与\n数据收集"),
        (935, 1130, "数据分析"),
        (1170, 1355, "研究总结\n与展望"),
    ]
    for y0, y1, name in stages:
        box(d, 33, (y0 + y1) / 2 - 38, 188, (y0 + y1) / 2 + 38, name, f)
        brace(d, 228, y0, y1)
    for y in (272, 552, 912, 1147):
        dashed_line(d, 30, y, 1015, y, dash=6, gap=5)

    # stage 1
    box(d, 514, 33, 785, 84, "研究背景、目的及意义", f)
    arrow(d, cx, 84, cx, 133)
    box(d, 290, 140, 1010, 237, dash=True)
    box(d, 303, 153, 509, 223, "在线评论与\nAI评论摘要研究", f)
    box(d, 546, 153, 752, 223, "信息采纳模型", f)
    box(d, 789, 153, 996, 223, "感知有用性与\n采纳意愿研究", f)
    arrow(d, cx, 237, cx, 302)
    # stage 2
    box(d, 468, 309, 830, 359, "平台调研，归纳AI评论摘要呈现模式", f)
    arrow(d, cx, 359, cx, 383)
    box(d, 442, 390, 857, 440, "确定四种呈现模式", f)
    arrow(d, cx, 440, cx, 464)
    box(d, 468, 471, 830, 521, "提出研究假设，构建研究模型", f)
    arrow(d, cx, 521, cx, 582)
    # stage 3
    box(d, 442, 589, 857, 639, "四组组间实验设计（每组一种呈现模式）", f)
    arrow(d, cx, 639, cx, 663)
    box(d, 468, 670, 830, 720, "实验材料制作与问卷设计", f)
    arrow(d, cx, 720, cx, 744)
    box(d, 468, 751, 830, 801, "预实验", f)
    arrow(d, cx, 801, cx, 825)
    box(d, 468, 832, 830, 882, "正式实验与数据收集", f)
    arrow(d, cx, 882, cx, 942)
    # stage 4
    box(d, 442, 949, 857, 999, "描述性统计与信效度分析", f)
    arrow(d, cx, 999, 405, 1062)
    arrow(d, cx, 999, cx, 1062)
    arrow(d, cx, 999, 893, 1062)
    box(d, 303, 1067, 509, 1117, "各呈现模式分组分析", f)
    box(d, 546, 1067, 752, 1117, "回归分析比较影响", f)
    box(d, 789, 1067, 996, 1117, "中介效应检验", f)
    arrow(d, 405, 1117, 588, 1190)
    arrow(d, cx, 1117, cx, 1190)
    arrow(d, 893, 1117, 712, 1190)
    # stage 5
    box(d, 290, 1198, 1010, 1322, dash=True)
    box(d, 303, 1235, 509, 1285, "理论贡献", f)
    box(d, 546, 1235, 752, 1285, "实践启示", f)
    box(d, 789, 1235, 996, 1285, "局限与展望", f)
    im.save(out)


def model(out):
    W_, H_ = 1195, 679
    im = Image.new("RGB", (P(W_), P(H_)), "white")
    d = ImageDraw.Draw(im)
    f = font(19)
    fb = font(24)
    # left big box
    d.rectangle([P(25), P(127), P(440), P(597)], outline="black", width=P(2.5))
    d.rectangle([P(25), P(127), P(440), P(182)], outline="black", width=P(2.5), fill="#e8e8e8")
    center_text(d, 232, 155, "AI评论摘要呈现模式", fb)
    labels = ["整体概括式", "属性分项式", "优缺点分组式", "总分结合式"]
    y = 215
    for lb in labels:
        box(d, 95, y, 370, y + 60, lb, f, width=2)
        y += 95
    center_text(d, 232, 632, "自变量", font(18))
    # mediator & DV
    box(d, 567, 95, 818, 168, "感知有用性", fb, width=2.5)
    center_text(d, 692, 62, "中介变量", font(18))
    box(d, 880, 350, 1160, 430, "消费者采纳意愿", fb, width=2.5)
    center_text(d, 1020, 465, "因变量", font(18))
    arrow(d, 442, 275, 563, 146, width=2, head=14)
    center_text(d, 640, 225, "H1a、H2a、H3a、H4a", f)
    arrow(d, 442, 392, 876, 392, width=2, head=14)
    center_text(d, 660, 362, "H1b、H2b、H3b、H4b", f)
    arrow(d, 818, 145, 1020, 348, width=2, head=14)
    center_text(d, 960, 205, "H5", f)
    center_text(d, 760, 545, "H6a—H6d：感知有用性在各呈现模式与采纳意愿之间的中介作用", font(17))
    im.save(out)


if __name__ == "__main__":
    tech_route(sys.argv[1])
    model(sys.argv[2])
