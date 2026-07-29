"""Application discovery — infer domain, personas, flows from page state."""

from __future__ import annotations

import re
from typing import Any, Optional

from monkeylm.config import Settings, _local_service_log
from monkeylm.models.ollama import _sanitize_prompt_input, _safe_json_parse, _ollama_chat_with_retry
from monkeylm.types import TestingStrategy, PersonaGoal, CriticalFlow


def _build_discovery_prompt(page_state: str) -> str:
    page_state = _sanitize_prompt_input(page_state)
    return """You are a QA analyst doing app reconnaissance.

Analyze the page state below and return ONLY valid JSON with this schema:
{
  "app_domain": "short description",
  "strategy_summary": "one-sentence plan",
  "primary_personas": [{"name": "", "description": "", "behaviors": [""]}],
  "critical_flows": [{"name": "", "description": "", "steps": [""]}],
  "edge_cases_to_test": [""],
  "security_focus": [""]
}

Important: do not add prose. Do not wrap in markdown. Return JSON only.

Page state:
""" + page_state


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_complete_discovery_payload(data: Any) -> bool:
    if not isinstance(data, dict):
        return False

    required_keys = {
        "app_domain",
        "strategy_summary",
        "primary_personas",
        "critical_flows",
        "edge_cases_to_test",
        "security_focus",
    }
    if any(key not in data for key in required_keys):
        return False
    if not _is_non_empty_string(data.get("app_domain")):
        return False
    if not _is_non_empty_string(data.get("strategy_summary")):
        return False

    personas = data.get("primary_personas")
    if not isinstance(personas, list) or not personas:
        return False
    for persona in personas:
        if not isinstance(persona, dict):
            return False
        if not _is_non_empty_string(persona.get("name")):
            return False
        if not _is_non_empty_string(persona.get("description")):
            return False
        behaviors = persona.get("behaviors")
        if not isinstance(behaviors, list) or not behaviors or not all(_is_non_empty_string(b) for b in behaviors):
            return False

    flows = data.get("critical_flows")
    if not isinstance(flows, list) or not flows:
        return False
    for flow in flows:
        if not isinstance(flow, dict):
            return False
        if not _is_non_empty_string(flow.get("name")):
            return False
        if not _is_non_empty_string(flow.get("description")):
            return False
        steps = flow.get("steps")
        if not isinstance(steps, list) or not steps or not all(_is_non_empty_string(step) for step in steps):
            return False

    edge_cases = data.get("edge_cases_to_test")
    if not isinstance(edge_cases, list) or not edge_cases or not all(_is_non_empty_string(item) for item in edge_cases):
        return False

    security_focus = data.get("security_focus")
    if not isinstance(security_focus, list) or not security_focus or not all(_is_non_empty_string(item) for item in security_focus):
        return False

    return True


def _parse_testing_strategy(raw_content: Any) -> Optional[TestingStrategy]:
    data = raw_content if isinstance(raw_content, dict) else _safe_json_parse(raw_content)
    if not _has_complete_discovery_payload(data):
        return None
    try:
        personas = [
            PersonaGoal(
                name=str(p.get("name", "Unknown")),
                description=str(p.get("description", "")),
                behaviors=[str(b) for b in p.get("behaviors", [])],
            )
            for p in data.get("primary_personas", [])
        ]
        flows = [
            CriticalFlow(
                name=str(f.get("name", "unknown_flow")),
                description=str(f.get("description", "")),
                steps=[str(s) for s in f.get("steps", [])],
            )
            for f in data.get("critical_flows", [])
        ]
        return TestingStrategy(
            app_domain=str(data.get("app_domain", "unknown")),
            primary_personas=personas,
            critical_flows=flows,
            edge_cases_to_test=[str(e) for e in data.get("edge_cases_to_test", [])],
            security_focus=[str(s) for s in data.get("security_focus", [])],
            strategy_summary=str(data.get("strategy_summary", "")),
        )
    except Exception:
        return None


def _build_heuristic_strategy(page_state: str) -> TestingStrategy:
    state_text = _sanitize_prompt_input(page_state)
    state_lower = state_text.lower()

    app_domain = "web application"
    if "quran" in state_lower:
        app_domain = "Quran app"
    elif "auth" in state_lower or "login" in state_lower:
        app_domain = "authentication portal"
    elif "checkout" in state_lower or "cart" in state_lower:
        app_domain = "e-commerce checkout experience"

    labels = re.findall(r"[A-Za-z][A-Za-z0-9/\- ]{1,40}", state_text)
    label_terms = [label.strip() for label in labels if len(label.strip()) > 1]
    inferred_actions = []
    for label in label_terms:
        lower_label = label.lower()
        if any(keyword in lower_label for keyword in ["browse", "search", "bookmark", "home", "menu", "open", "view", "read"]):
            inferred_actions.append(label)

    personas = [
        PersonaGoal(
            name="Primary reader",
            description="A regular visitor who wants to reach the main content quickly and with minimal friction.",
            behaviors=["uses the main navigation", "opens the most relevant content", "reports confusion or slow loading"],
        )
    ]

    if any(keyword in state_lower for keyword in ["browse", "search", "bookmark"]):
        personas.append(
            PersonaGoal(
                name="Power user",
                description="A frequent user who relies on search, bookmarking, and quick navigation to move around efficiently.",
                behaviors=["uses search and filtering", "jumps between sections", "returns to bookmarked content"],
            )
        )

    flows = [
        CriticalFlow(
            name="primary_navigation",
            description="Reach the main content or task from the current landing view.",
            steps=["locate the primary entry point", "open the main content", "confirm the result is visible"],
        )
    ]

    if any(keyword in state_lower for keyword in ["browse", "search", "bookmark"]):
        flows.append(
            CriticalFlow(
                name="browse_and_search",
                description="Use available navigation or search controls to find content or features.",
                steps=["open navigation or browse controls", "apply a search or browse action", "open a resulting item"],
            )
        )

    if inferred_actions:
        flows.append(
            CriticalFlow(
                name="label_driven_navigation",
                description="Verify that visible labels and controls map to the expected actions.",
                steps=[f"inspect {action}" for action in inferred_actions[:3]],
            )
        )

    edge_cases = ["empty state", "slow loading", "unexpected validation error"]
    if any(keyword in state_lower for keyword in ["browse", "search", "bookmark"]):
        edge_cases.extend(["empty search result", "bookmark persistence issue"])

    security_focus = ["input validation", "session handling", "access control"]

    return TestingStrategy(
        app_domain=app_domain,
        primary_personas=personas,
        critical_flows=flows,
        edge_cases_to_test=edge_cases,
        security_focus=security_focus,
        strategy_summary="Start with the primary navigation and then verify the discovery-oriented controls that are visible on the page.",
    )


def refresh_testing_strategy(strategy: Optional[TestingStrategy], page_state: str) -> Optional[TestingStrategy]:
    if strategy is None:
        return None

    state_text = _sanitize_prompt_input(page_state)
    state_lower = state_text.lower()
    refreshed = TestingStrategy(
        app_domain=strategy.app_domain,
        primary_personas=[PersonaGoal(
            name=p.name,
            description=p.description,
            behaviors=list(p.behaviors),
        ) for p in strategy.primary_personas],
        critical_flows=[CriticalFlow(
            name=f.name,
            description=f.description,
            steps=list(f.steps),
        ) for f in strategy.critical_flows],
        edge_cases_to_test=list(strategy.edge_cases_to_test),
        security_focus=list(strategy.security_focus),
        strategy_summary=strategy.strategy_summary,
    )

    if "search" in state_lower and not any("search" in flow.name.lower() for flow in refreshed.critical_flows):
        refreshed.critical_flows.append(CriticalFlow(
            name="search_focus",
            description="Check whether search and discovery controls behave correctly on the current page.",
            steps=["locate search control", "enter a probe query", "verify results appear"],
        ))

    if "bookmark" in state_lower and not any("bookmark" in flow.name.lower() for flow in refreshed.critical_flows):
        refreshed.critical_flows.append(CriticalFlow(
            name="bookmark_management",
            description="Verify bookmark persistence and visibility for the current content.",
            steps=["locate bookmark control", "toggle bookmark state", "confirm persistence"],
        ))

    if refreshed.strategy_summary and "search" in state_lower:
        refreshed.strategy_summary = f"{refreshed.strategy_summary} Focus on search/discovery interactions on this page."

    return refreshed


async def run_application_discovery(
    settings: Settings,
    page_state: str,
) -> Optional[TestingStrategy]:
    print("   🔍 Running application discovery...")
    prompt = _build_discovery_prompt(page_state)

    candidate_models = [settings.ollama_model]
    if settings.ollama_model != "llama3.2:latest":
        candidate_models.append("llama3.2:latest")
    if settings.ollama_model != "mistral:latest":
        candidate_models.append("mistral:latest")

    last_response = None
    for model_name in candidate_models:
        try:
            response = await _ollama_chat_with_retry(
                settings=settings,
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                timeout_seconds=settings.ollama_timeout_seconds,
                max_retries=2,
            )
        except Exception as exc:
            _local_service_log(f"Application Discovery: request failed for {model_name}: {exc}", settings.output_dir)
            response = None

        last_response = response
        if response is None:
            continue

        try:
            content = response["message"]["content"]
            strategy = _parse_testing_strategy(content)
            if strategy is not None:
                print(f"   ✅ Discovery ready — domain: {strategy.app_domain}")
                _local_service_log(f"Application Discovery: {strategy.strategy_summary}", settings.output_dir)
                return strategy
        except Exception as exc:
            _local_service_log(f"Application Discovery: failed to parse strategy from {model_name}: {exc}", settings.output_dir)

    if last_response is None:
        _local_service_log("Application Discovery: LLM returned no response; using heuristic fallback strategy.", settings.output_dir)
    else:
        _local_service_log("Application Discovery: could not parse strategy; using heuristic fallback strategy.", settings.output_dir)

    strategy = _build_heuristic_strategy(page_state)
    print(f"   ⚠️ Discovery fallback — domain: {strategy.app_domain}")
    return strategy
