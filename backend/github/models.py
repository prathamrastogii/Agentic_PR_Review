from pydantic import BaseModel, Field


class ParsedPRUrl(BaseModel):
    owner: str
    repo: str
    pr_number: int


class PRMetadata(BaseModel):
    owner: str
    repo: str
    pr_number: int
    title: str
    body: str | None = None
    base_ref: str
    head_ref: str
    head_sha: str
    html_url: str


class FileDiff(BaseModel):
    filename: str
    status: str
    patch: str | None = None
    previous_filename: str | None = None
    additions: int = 0
    deletions: int = 0
    changes: int = 0
