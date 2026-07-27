from typing import Literal

from pydantic import BaseModel, Field


class ReviewRequest(BaseModel):
    pr_url: str = Field(..., min_length=1)
    mode: Literal["agent", "baseline"] = "agent"
