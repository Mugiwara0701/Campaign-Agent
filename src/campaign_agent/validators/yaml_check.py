"""L5: the YAML text is safe and exactly what the serializer would write.

Rejects anchors, aliases, explicit tags, several documents, duplicate keys, tabs and comments
outside the header; runs yamllint; and checks round-trip equality (parse, dump again, compare).
"""

from typing import Any

import yaml
from yamllint import linter
from yamllint.config import YamlLintConfig

from campaign_agent.contract.loader import MAX_BYTES, ContractViolation, parse_yaml
from campaign_agent.serializer.yaml_serializer import dump_body, strip_header
from campaign_agent.validators.report import Issue, LevelResult, level_result

LEVEL = 5

_LINT = YamlLintConfig(
    "extends: default\n"
    "rules:\n"
    "  line-length: {max: 120}\n"
    "  document-start: disable\n"
    "  truthy: {check-keys: false}\n"
)


def _issue(code: str, message: str) -> Issue:
    return Issue(LEVEL, code, "(yaml)", message)


def _structure(text: str) -> list[Issue]:
    issues: list[Issue] = []
    documents = 0
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.DocumentStartEvent):
                documents += 1
            if isinstance(event, yaml.AliasEvent):
                issues.append(_issue("alias", "aliases are not allowed"))
            elif isinstance(event, yaml.NodeEvent) and getattr(event, "anchor", None):
                issues.append(_issue("anchor", "anchors are not allowed"))
            tag = getattr(event, "tag", None)
            if tag:
                issues.append(_issue("tag", f"explicit tag {tag!r} is not allowed"))
    except yaml.YAMLError as exc:
        return [_issue("syntax", f"not valid YAML: {exc}")]
    if documents > 1:
        issues.append(_issue("multi_document", "only one document is allowed"))
    return issues


def check_yaml(text: str) -> LevelResult:
    issues: list[Issue] = []
    if len(text.encode("utf-8")) > MAX_BYTES:
        issues.append(_issue("too_large", f"larger than {MAX_BYTES} bytes"))
        return level_result(LEVEL, "yaml", issues)
    if "\t" in text:
        issues.append(_issue("tab", "tabs are not allowed"))
    issues.extend(_structure(text))
    body = strip_header(text)
    if any(line.lstrip().startswith("#") for line in body.splitlines()):
        issues.append(_issue("comment_outside_header", "comments are only allowed in the header"))
    if issues:
        return level_result(LEVEL, "yaml", issues)

    for problem in linter.run(text, _LINT):
        issues.append(
            Issue(
                LEVEL,
                "yamllint",
                f"line {problem.line}",
                problem.desc,
                severity="error" if problem.level == "error" else "warning",
            )
        )
    try:
        data: Any = parse_yaml(text)
    except ContractViolation as exc:
        issues.append(_issue("parse", str(exc)))
        return level_result(LEVEL, "yaml", issues)
    if dump_body(data) != body:
        issues.append(_issue("round_trip", "re-serialising the parsed YAML gives different text"))
    return level_result(LEVEL, "yaml", issues)
