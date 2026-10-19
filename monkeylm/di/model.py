"""Model client DI adapter."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from monkeylm.config import Settings, load_settings
from monkeylm.interfaces import IModelClient
from monkeylm.types import PageSnapshot


def create_model_client(settings: Optional[Settings] = None) -> IModelClient:
    """Factory function to create model client instance.

    Args:
        settings: Runtime configuration. Defaults to load_settings() if None.

    Returns:
        IModelClient implementation (currently ModelClientAdapter).

    Example:
        >>> from monkeylm import create_model_client
        >>> model = create_model_client()
        >>> result = await model.infer("Hello!", model="minimax-m3:cloud")

    Note:
        Currently returns ModelClientAdapter which wraps models.py functions.
        Will be replaced with proper ModelClient class in Phase 2 refactoring.
        infer(), vision_infer(), and stream() work. analyze_testing_strategy()
        and decide_next_action() raise NotImplementedError - the real
        run_application_discovery()/decide_next_action() are async, need a
        memory_store this Protocol never provides, and don't take a "goal"
        parameter. See IMPROVEMENT_LOG.md Cycle 5.
    """
    import asyncio

    from monkeylm.models.openai_client import (
        build_vision_message,
        chat_completion,
        extract_content,
        stream_chat_completion,
    )

    if settings is None:
        settings = load_settings()

    class ModelClientAdapter:
        """Adapter that wraps models.py functions to implement IModelClient."""

        def __init__(self, settings: Settings):
            self._settings = settings

        async def infer(
            self,
            prompt: str,
            model: str,
            temperature: float = 0.2,
            top_p: float = 0.9,
            max_tokens: Optional[int] = None,
        ) -> Dict[str, Any]:
            response = await asyncio.to_thread(
                chat_completion,
                self._settings,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )
            import json
            content = extract_content(response)
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"raw_response": content}

        async def vision_infer(
            self,
            prompt: str,
            image_path: str,
            model: str,
        ) -> Dict[str, Any]:
            message = build_vision_message(prompt, image_path)
            response = await asyncio.to_thread(
                chat_completion,
                self._settings,
                model=model,
                messages=[message],
            )
            import json
            content = extract_content(response)
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"raw_response": content}

        async def stream(
            self,
            prompt: str,
            model: str,
            temperature: float = 0.2,
        ) -> Any:
            stream = stream_chat_completion(
                self._settings,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            for chunk in stream:
                yield chunk

        def analyze_testing_strategy(
            self,
            page_snapshot: PageSnapshot,
        ) -> Dict[str, Any]:
            # IModelClient declares this as a sync method returning Dict[str, Any],
            # but the real run_application_discovery(settings, page_state: str) is
            # async and returns Optional[TestingStrategy] (a dataclass, not a
            # dict), and needs page_state text from state_to_prompt(), not the raw
            # PageSnapshot. The Protocol shape doesn't match the real capability -
            # see IMPROVEMENT_LOG.md Cycle 5. Failing loud rather than silently
            # calling the wrong thing.
            raise NotImplementedError(
                "ModelClientAdapter.analyze_testing_strategy: IModelClient's sync "
                "Dict[str, Any] contract does not match run_application_discovery's "
                "real async signature (settings, page_state: str) -> "
                "Optional[TestingStrategy]. Call "
                "monkeylm.models.run_application_discovery(settings, state_to_prompt(page_snapshot)) "
                "directly instead."
            )

        def decide_next_action(
            self,
            page_snapshot: PageSnapshot,
            goal: str,
            history: List[Dict[str, Any]],
        ) -> Dict[str, Any]:
            # Same category of Protocol/reality mismatch: the real
            # decide_next_action(settings, page_state, memory_store, ...) is async,
            # requires a memory_store (raises ValueError without one), and has no
            # "goal" parameter at all - it isn't goal-directed, it drives from
            # page state + memory + testing strategy. There is no honest way to
            # implement this signature without inventing behavior. See
            # IMPROVEMENT_LOG.md Cycle 5.
            raise NotImplementedError(
                "ModelClientAdapter.decide_next_action: IModelClient's "
                "(page_snapshot, goal, history) contract has no correspondence to "
                "the real decide_next_action(settings, page_state, memory_store, ...), "
                "which is async and requires a memory_store. Call "
                "monkeylm.models.decide_next_action(settings, page_state, memory_store=...) "
                "directly instead."
            )

    return ModelClientAdapter(settings)
