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
from .evaluation import (
    CaseEvaluation,
    EvaluationCase,
    EvaluationCorpus,
    EvaluationInputError,
    EvaluationSuite,
    evaluate_report,
    evaluate_reports,
    load_evaluation_corpus,
)
from .tools import ReadOnlyToolset, ToolInputError

__all__ = [
    "Citation",
    "CopilotError",
    "CopilotProvider",
    "CopilotProviderError",
    "CopilotAnswer",
    "CaseEvaluation",
    "EvaluationCase",
    "EvaluationCorpus",
    "EvaluationInputError",
    "EvaluationSuite",
    "OpenAICompatibleProvider",
    "ProviderResponse",
    "ProviderToolCall",
    "ReadOnlyToolset",
    "ResearchCopilot",
    "ToolDefinition",
    "ToolInputError",
    "ToolResult",
    "ToolTrace",
    "evaluate_report",
    "evaluate_reports",
    "load_evaluation_corpus",
    "normalize_provider_endpoint",
]
