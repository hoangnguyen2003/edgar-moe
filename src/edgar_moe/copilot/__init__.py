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
from .contracts import (
    COPILOT_DISCLAIMER,
    Citation,
    CopilotAnswer,
    ToolDefinition,
    ToolResult,
    ToolTrace,
)
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
from .review import (
    ReviewInputError,
    append_copilot_reviews,
    benchmark_sha256,
    verify_copilot_review_history,
    write_copilot_review_history,
)
from .tools import ReadOnlyToolset, ToolInputError
from .verification import CopilotVerificationError, verify_copilot_answer_report

__all__ = [
    "Citation",
    "COPILOT_DISCLAIMER",
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
    "ReviewInputError",
    "OpenAICompatibleProvider",
    "ProviderResponse",
    "ProviderToolCall",
    "ReadOnlyToolset",
    "ResearchCopilot",
    "ToolDefinition",
    "ToolInputError",
    "ToolResult",
    "ToolTrace",
    "CopilotVerificationError",
    "answer_report",
    "evaluate_report",
    "evaluate_reports",
    "load_evaluation_corpus",
    "normalize_provider_endpoint",
    "run_benchmark",
    "write_benchmark_report",
    "append_copilot_reviews",
    "benchmark_sha256",
    "verify_copilot_review_history",
    "write_copilot_review_history",
    "verify_copilot_answer_report",
]
