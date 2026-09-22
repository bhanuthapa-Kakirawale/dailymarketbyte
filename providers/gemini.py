"""Gemini's numeric answers, wrapped as AI observations.

Gemini is a DISCOVERY source. Grounded Google Search does not promote it to a primary one -
it is still an LLM reporting what it read. Everything here is SourceType.AI in independence
group GEMINI, which is what stops it verifying a critical number on its own, and equally
what lets it corroborate a Yahoo or NSE reading when it independently agrees.
"""
from __future__ import annotations

from adapters.news_adapter import NewsAdapter

from .base import Provider, ProviderResult


class GeminiProvider(Provider):
    name = "gemini"

    def facts(self, ai_facts: dict) -> ProviderResult:
        """AI observations for the numeric facts ai_pass validated by date and range.

        `nifty_close` is the valuable one: it is the independent second opinion that turns
        main.py's old print-and-discard cross-check into a recorded ValidationResult.
        """
        adapter = NewsAdapter(self.session_date, self.report_date, self.retrieved_at,
                              demo=self.demo)
        return ProviderResult(observations=adapter.observe_facts(ai_facts or {}),
                              payload={"ai_facts": ai_facts or {}})


__all__ = ["GeminiProvider"]
