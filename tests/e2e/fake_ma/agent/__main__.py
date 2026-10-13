import sys
from pathlib import Path

from pydantic import ValidationError

from campaign_agent.compiler.compiler import merge_onto_base
from campaign_agent.contract.loader import ContractViolation, load_overlay, parse_yaml
from campaign_agent.contract.model import LeadgenFile
from campaign_agent.contract.vocabularies import base_config


def queries(effective: dict) -> list[str]:  # type: ignore[type-arg]
    ctx = effective["project_context"]
    industries = ctx.get("industries") or [""]
    products = ctx.get("products") or [""]
    technologies = ctx.get("technologies") or [""]
    seen: list[str] = []
    for tech in technologies:
        for product in products:
            for industry in industries:
                line = " ".join(x for x in (industry, product, tech) if x)
                if line and line not in seen:
                    seen.append(line)
    if (
        not seen
    ):  # a campaign that says nothing about its target: the base vocabulary, as the real agent does
        capabilities = (effective.get("search") or {}).get("capabilities") or []
        seen = [f"{cap} customers" for cap in capabilities[:5]] or [
            "embedded engineering customers"
        ]
    return seen[:20]


def main(argv: list[str]) -> int:
    if argv[:2] != ["leads", "queries"] or len(argv) != 4 or argv[2] != "--campaign":
        print("usage: python -m agent leads queries --campaign <file>", file=sys.stderr)
        return 2
    text = Path(argv[3]).read_text(encoding="utf-8")
    try:
        load_overlay(text)  # strict: unknown keys, wrong types, out-of-range values
        effective = merge_onto_base(parse_yaml(text), base_config())
        LeadgenFile.model_validate(effective)
    except (ContractViolation, ValidationError, ValueError) as exc:
        print(f"campaign file rejected: {str(exc).splitlines()[0][:200]}", file=sys.stderr)
        return 2
    for line in queries(effective):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
