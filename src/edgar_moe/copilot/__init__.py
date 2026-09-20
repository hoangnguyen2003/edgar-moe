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
from .benchmark import BenchmarkFailure, BenchmarkRun, run_benchmark, write_benchmark_report
from .contracts import Citation, CopilotAnswer, ToolDefinition, ToolResult, ToolTrace
from .evaluation import (
    CaseEvaluation,
    EvaluationCase,
    EvaluationCorpus,
    EvaluationInputError,
    EvaluationSuite,
    answer_report,
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
    "BenchmarkFailure",
    "BenchmarkRun",
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
    "answer_report",
    "evaluate_report",
    "evaluate_reports",
    "load_evaluation_corpus",
    "normalize_provider_endpoint",
    "run_benchmark",
    "write_benchmark_report",
]
