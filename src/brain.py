"""Model call. Thin: delegates to src.llm, which knows what is reachable."""

from src import llm

BrainError = llm.ProviderError
NoProvider = llm.NoProvider


def complete(system: str, user: str, model: str, temperature: float = 0.9) -> str:
    try:
        return llm.complete(system, user, model, temperature=temperature)
    except llm.NoProvider:
        raise
    except llm.ProviderError as e:
        raise BrainError(str(e))
