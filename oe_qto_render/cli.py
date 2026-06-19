"""CLI: render a QTO sheet from a plan-data JSON.

    python -m oe_qto_render render --data plan.json --out sheet.svg [--pdf] [--no-base]
"""
from __future__ import annotations

import argparse
import json

from .model import PlanData
from .renderer import render_pdf, render_svg


def main(argv=None):
    ap = argparse.ArgumentParser(prog="oe_qto_render")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="render a QTO sheet")
    r.add_argument("--data", required=True, help="plan-data JSON path")
    r.add_argument("--out", required=True, help="output SVG path")
    r.add_argument("--pdf", action="store_true", help="also write <out>.pdf")
    r.add_argument("--no-base", action="store_true", help="skip the gray base underlay")
    args = ap.parse_args(argv)

    with open(args.data) as f:
        plan = PlanData.model_validate(json.load(f))

    with_base = not args.no_base
    svg = render_svg(plan, with_base=with_base)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(svg)
    print("wrote", args.out)
    if args.pdf:
        pdf_out = args.out.rsplit(".", 1)[0] + ".pdf"
        render_pdf(plan, pdf_out, with_base=with_base)
        print("wrote", pdf_out)


if __name__ == "__main__":
    main()
