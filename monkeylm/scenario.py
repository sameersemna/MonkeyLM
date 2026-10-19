"""Natural-language scenario parsing and artifact generation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from monkeylm.types import CriticalFlow, PersonaGoal, Settings, TestingStrategy

_ACTION_VERBS = frozenset(
    {
        "accept",
        "add",
        "apply",
        "bookmark",
        "browse",
        "cancel",
        "check",
        "click",
        "close",
        "confirm",
        "create",
        "delete",
        "edit",
        "enter",
        "fill",
        "filter",
        "find",
        "follow",
        "go",
        "login",
        "log in",
        "logout",
        "navigate",
        "open",
        "pay",
        "post",
        "register",
        "remove",
        "save",
        "search",
        "select",
        "send",
        "sign in",
        "sign up",
        "submit",
        "toggle",
        "type",
        "update",
        "upload",
        "verify",
        "visit",
    }
)
_EDGE_KEYWORDS = (
    "invalid",
    "empty",
    "blank",
    "boundary",
    "minimum",
    "maximum",
    "max length",
    "min length",
    "duplicate",
    "too long",
    "too short",
    "missing",
    "unauthorized",
    "forbidden",
    "timeout",
    "offline",
    "error",
    "mismatch",
)
_SECURITY_KEYWORDS = (
    "xss",
    "sql injection",
    "sqli",
    "csrf",
    "injection",
    "security",
    "permission",
    "privilege",
    "authentication",
    "authorization",
    "access control",
    "secret",
    "token",
    "sanitize",
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
_STEP_SPLIT_RE = re.compile(r"\s+(?:then|and then)\s+|;\s*", re.IGNORECASE)


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _unique(items: Iterable[str]) -> List[str]:
    result: List[str] = []
    for item in items:
        cleaned = _clean(item)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _sentences(text: str) -> List[str]:
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]


def _flow_name(index: int, sentence: str) -> str:
    cleaned = re.sub(r"^(?:and\s+then|then)\s+", "", sentence.strip(), flags=re.IGNORECASE)
    words = re.findall(r"[a-zA-Z0-9]+", cleaned.lower())[:4]
    base = "_".join(words) or f"flow_{index}"
    return f"flow_{index}_{base}"[:80]


def _flow_steps(sentence: str) -> List[str]:
    parts = [_clean(part) for part in _STEP_SPLIT_RE.split(sentence) if _clean(part)]
    return parts or [sentence]


def _default_persona() -> PersonaGoal:
    return PersonaGoal(
        name="QA engineer",
        description="An exploratory tester executing the user-provided scenario.",
        behaviors=["follow the requested flow", "probe edge cases and security-relevant inputs"],
    )


def _extract_persona(text: str, sentences: List[str]) -> Optional[PersonaGoal]:
    match = re.search(r"\bas an? ([^,.;]+)", text, re.IGNORECASE)
    if not match:
        return None
    name = _clean(match.group(1))
    if not name:
        return None
    return PersonaGoal(
        name=name,
        description=f"User described by the scenario: {name}.",
        behaviors=sentences[:3] or [text],
    )


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    lower = text.lower()
    return any(term in lower for term in terms)


def parse_scenario_text(text: str, name: str = "") -> TestingStrategy:
    """Convert a plain-English scenario into the existing TestingStrategy model."""
    cleaned = _clean(text)
    if not cleaned:
        raise ValueError("Scenario text must not be empty.")

    sentences = _sentences(cleaned) or [cleaned]
    flows: List[CriticalFlow] = []
    edge_cases: List[str] = []
    security_focus: List[str] = []

    for sentence in sentences:
        lower = sentence.lower()
        if _contains_any(lower, _EDGE_KEYWORDS):
            edge_cases.append(sentence)
        if _contains_any(lower, _SECURITY_KEYWORDS):
            security_focus.append(sentence)
        if _contains_any(lower, _ACTION_VERBS):
            clauses = _flow_steps(sentence)
            action_clauses = [clause for clause in clauses if _contains_any(clause, _ACTION_VERBS)]
            if len(action_clauses) <= 1:
                flows.append(
                    CriticalFlow(
                        name=_flow_name(len(flows) + 1, sentence),
                        description=sentence,
                        steps=clauses,
                    )
                )
            else:
                for clause in action_clauses:
                    flows.append(
                        CriticalFlow(
                            name=_flow_name(len(flows) + 1, clause),
                            description=clause,
                            steps=[clause],
                        )
                    )

    if not flows:
        flows.append(
            CriticalFlow(
                name="scenario_objective",
                description=cleaned,
                steps=[cleaned],
            )
        )

    persona = _extract_persona(cleaned, sentences) or _default_persona()
    summary = sentences[0][:400] if sentences else cleaned[:400]
    return TestingStrategy(
        app_domain=_clean(name) or "user_scenario",
        primary_personas=[persona],
        critical_flows=flows,
        edge_cases_to_test=_unique(edge_cases),
        security_focus=_unique(security_focus),
        strategy_summary=summary,
    )


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value]
    return [value]


def _personas_from_mapping(raw: Any) -> List[PersonaGoal]:
    personas: List[PersonaGoal] = []
    for item in _as_list(raw):
        if isinstance(item, str):
            name = _clean(item)
            personas.append(
                PersonaGoal(
                    name=name or "QA engineer",
                    description=f"User described by the scenario: {name or 'QA engineer'}.",
                    behaviors=[],
                )
            )
            continue
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name")) or "QA engineer"
        description = _clean(item.get("description")) or f"User described by the scenario: {name}."
        behaviors = [str(behavior) for behavior in _as_list(item.get("behaviors")) if _clean(behavior)]
        personas.append(PersonaGoal(name=name, description=description, behaviors=behaviors))
    return personas


def _flows_from_mapping(raw: Any) -> List[CriticalFlow]:
    flows: List[CriticalFlow] = []
    for index, item in enumerate(_as_list(raw), start=1):
        if isinstance(item, str):
            flows.append(
                CriticalFlow(
                    name=_flow_name(index, item),
                    description=_clean(item),
                    steps=[_clean(item)],
                )
            )
            continue
        if not isinstance(item, dict):
            continue
        description = _clean(item.get("description")) or _clean(item.get("name"))
        if not description:
            continue
        steps = [str(step) for step in _as_list(item.get("steps")) if _clean(step)]
        name = _clean(item.get("name")) or _flow_name(index, description)
        flows.append(CriticalFlow(name=name, description=description, steps=steps or [description]))
    return flows


def scenario_from_mapping(data: Dict[str, Any], name: str = "") -> TestingStrategy:
    """Build a strategy from the JSON scenario format or a TestingStrategy dump."""
    if not isinstance(data, dict):
        raise ValueError("Scenario JSON must be an object.")
    summary = _clean(data.get("strategy_summary") or data.get("summary") or data.get("objective"))
    flows = _flows_from_mapping(data.get("critical_flows") or data.get("flows"))
    if summary and not flows:
        flows = _flows_from_mapping([summary])
    if not flows:
        raise ValueError("Scenario must define at least one flow or a summary/objective.")
    personas = _personas_from_mapping(data.get("primary_personas") or data.get("personas"))
    if not personas:
        personas = [_default_persona()]
    return TestingStrategy(
        app_domain=_clean(name) or _clean(data.get("app_domain") or data.get("name")) or "user_scenario",
        primary_personas=personas,
        critical_flows=flows,
        edge_cases_to_test=_unique(str(item) for item in _as_list(data.get("edge_cases_to_test") or data.get("edge_cases"))),
        security_focus=_unique(str(item) for item in _as_list(data.get("security_focus"))),
        strategy_summary=summary or flows[0].description,
    )


def load_scenario_file(path: str, name: str = "") -> TestingStrategy:
    scenario_path = Path(path).expanduser()
    if not scenario_path.is_file():
        raise ValueError(f"Scenario file not found: {path}")
    raw = scenario_path.read_text(encoding="utf-8")
    if scenario_path.suffix.lower() == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Scenario JSON is invalid: {exc}") from exc
        return scenario_from_mapping(data, name=name or scenario_path.stem)
    return parse_scenario_text(raw, name=name or scenario_path.stem)


def load_scenario_from_settings(settings: Settings) -> Optional[TestingStrategy]:
    text = (settings.scenario_text or "").strip()
    file_path = (settings.scenario_file or "").strip()
    if text and file_path:
        raise ValueError("Set only one of SCENARIO_TEXT/--scenario or SCENARIO_FILE/--scenario-file.")
    if file_path:
        return load_scenario_file(file_path, name=settings.scenario_name)
    if text:
        return parse_scenario_text(text, name=settings.scenario_name)
    return None


def scenario_to_dict(strategy: TestingStrategy) -> Dict[str, Any]:
    return {
        "name": strategy.app_domain,
        "summary": strategy.strategy_summary,
        "personas": [
            {
                "name": persona.name,
                "description": persona.description,
                "behaviors": list(persona.behaviors),
            }
            for persona in strategy.primary_personas
        ],
        "flows": [
            {
                "name": flow.name,
                "description": flow.description,
                "steps": list(flow.steps),
            }
            for flow in strategy.critical_flows
        ],
        "edge_cases": list(strategy.edge_cases_to_test),
        "security_focus": list(strategy.security_focus),
    }


def write_scenario_artifact(strategy: TestingStrategy, output_dir: str) -> str:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "scenario.json"
    path.write_text(
        json.dumps(scenario_to_dict(strategy), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return str(path)
