from __future__ import annotations

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult


class QuestionParams(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)


class QuestionTool(Tool):
    name = "question"
    Params = QuestionParams

    async def execute(self, params: QuestionParams, ctx: ToolContext) -> ToolResult:
        if ctx.ask_question is None:
            return ToolResult("error: no question handler available", is_error=True)
        answer = await ctx.ask_question(params.question, params.options)
        return ToolResult(f"user answered: {answer}", title=params.question)
