"""Optional, read-only, evidence-grounded research copilot."""

from .agent import (
    CopilotError,
    CopilotProvider,
    CopilotProviderError,
    OpenAICompatibleProvider,
    ProviderResponse,
    ProviderToolCall,
    ResearchCopilot,
    normalize_provider_endpoint,
)
from .contracts import Citation, CopilotAnswer, ToolDefinition, ToolResult, ToolTrace
from .tools import ReadOnlyToolset, ToolInputError

__all__ = [
    "Citation",
    "CopilotError",
    "CopilotProvider",
    "CopilotProviderError",
    "CopilotAnswer",
    "OpenAICompatibleProvider",
    "ProviderResponse",
    "ProviderToolCall",
    "ReadOnlyToolset",
    "ResearchCopilot",
    "ToolDefinition",
    "ToolInputError",
    "ToolResult",
    "ToolTrace",
    "normalize_provider_endpoint",
]
