"""MCP tool definitions.

Tools stay thin: validate input, call a service, shape the result, and turn any
failure into a clean, user-safe payload.  All real logic lives in ``services/``.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import ValidationError

from config.settings import Settings, get_settings
from services import ServiceError, utc_now_iso
from services.amazon_service import AmazonService
from services.pricing_service import PricingService
from services.supplier_service import SupplierService
from services.trends_service import TrendsService

if TYPE_CHECKING:  # pragma: no cover - typing only; tools import fine without the MCP SDK
    # mcp >= 2.0 renamed FastMCP to MCPServer; both expose .tool() and .run().
    from mcp.server.mcpserver import MCPServer as MCPServerType

logger = logging.getLogger(__name__)

__all__ = ["ServiceBundle", "tool_handler", "error_payload", "MCPServerType"]


@dataclass(slots=True)
class ServiceBundle:
    """The service instances shared by every tool."""

    settings: Settings
    amazon: AmazonService
    trends: TrendsService
    supplier: SupplierService
    pricing: PricingService

    @classmethod
    def create(cls, settings: Settings | None = None) -> "ServiceBundle":
        settings = settings or get_settings()
        return cls(
            settings=settings,
            amazon=AmazonService(settings),
            trends=TrendsService(settings),
            supplier=SupplierService(settings),
            pricing=PricingService(settings),
        )


def error_payload(code: str, message: str, remediation: str | None = None) -> dict[str, Any]:
    """A structured, user-safe error result."""
    return {
        "ok": False,
        "error": {"code": code, "message": message, "remediation": remediation},
        "generated_at": utc_now_iso(),
    }


def tool_handler(func: Callable[..., Awaitable[dict[str, Any]]]) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Wrap a tool handler so it never leaks a stack trace to the MCP client.

    ``ServiceError`` messages are written for end users and are passed through;
    anything unexpected is logged in full and replaced by a generic message.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            result = await func(*args, **kwargs)
        except ServiceError as exc:
            logger.info("Tool %s rejected input: %s", func.__name__, exc.message)
            return error_payload(exc.code, exc.message, exc.remediation)
        except ValidationError as exc:
            logger.info("Tool %s received invalid input: %s", func.__name__, exc)
            return error_payload(
                "invalid_input",
                "Invalid input: " + "; ".join(_format_validation_errors(exc)),
                "Correct the listed fields and call the tool again.",
            )
        except ValueError as exc:
            logger.info("Tool %s received invalid input: %s", func.__name__, exc)
            return error_payload("invalid_input", f"Invalid input: {exc}", "Check the input values and try again.")
        except Exception:  # noqa: BLE001 - the MCP client must never see a traceback
            logger.exception("Unhandled error in tool %s", func.__name__)
            return error_payload(
                "internal_error",
                "The tool failed unexpectedly. The full error has been logged on the server.",
                "Retry the call; if it keeps failing, check the server logs (stderr).",
            )
        result.setdefault("ok", True)
        result.setdefault("generated_at", utc_now_iso())
        return result

    return wrapper


def _format_validation_errors(exc: ValidationError) -> list[str]:
    """Turn a pydantic ValidationError into short 'field: reason' strings."""
    messages: list[str] = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"]) or "input"
        messages.append(f"{field}: {error['msg']}")
    return messages
