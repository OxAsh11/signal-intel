"""Builds the production gateway from settings. A missing key means that side is "not set up"."""

from typing import TYPE_CHECKING

from psycopg_pool import AsyncConnectionPool

from signal_core.ai_gateway.gateway import AiGateway, PgExtractorVersions
from signal_core.ai_gateway.providers.anthropic import AnthropicProvider
from signal_core.ai_gateway.providers.openrouter import OpenRouterChatProvider
from signal_core.ai_gateway.providers.voyage import VoyageProvider
from signal_core.config import Settings
from signal_core.metering import PgBudgetGuard, PgPriceBook

if TYPE_CHECKING:
    from signal_core.ai_gateway.providers.sarvam import SarvamChatProvider


def build_gateway(settings: Settings, pool: AsyncConnectionPool) -> AiGateway:
    chat: AnthropicProvider | SarvamChatProvider | OpenRouterChatProvider | None = None
    if settings.ai_chat_provider == "sarvam" and settings.sarvam_api_key:
        # Imported only when Sarvam is switched on, so the worker starts without the sarvamai
        # package otherwise (it is an optional, experimental provider).
        from signal_core.ai_gateway.providers import sarvam

        chat = sarvam.SarvamChatProvider(settings.sarvam_api_key)
    elif settings.ai_chat_provider == "openrouter":
        # No fallback to Anthropic: with the key missing, chat stays None ("not set up").
        if settings.openrouter_api_key:
            chat = OpenRouterChatProvider(settings.openrouter_api_key)
    elif settings.anthropic_api_key:
        chat = AnthropicProvider(settings.anthropic_api_key)
    embedder = VoyageProvider(settings.voyage_api_key) if settings.voyage_api_key else None
    return AiGateway(
        settings=settings,
        chat=chat,
        embedder=embedder,
        prices=PgPriceBook(pool),
        budget=PgBudgetGuard(pool),
        versions=PgExtractorVersions(pool),
    )
