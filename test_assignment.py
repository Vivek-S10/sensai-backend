import asyncio
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum

class TaskInputType(str, Enum):
    CODE = "code"
    TEXT = "text"
    AUDIO = "audio"

class TaskAIResponseType(str, Enum):
    CHAT = "chat"
    EXAM = "exam"

class Block(BaseModel):
    id: Optional[str] = None
    type: str
    props: Optional[Dict] = {}
    content: Optional[List] = []
    children: Optional[List] = []
    position: Optional[int] = None

class AssignmentEvaluationCriteria(BaseModel):
    scorecard_id: Optional[int] = None
    min_score: float
    max_score: float
    pass_score: float

class Assignment(BaseModel):
    blocks: List[Block]
    context: Optional[Dict] = None
    evaluation_criteria: AssignmentEvaluationCriteria | None = None
    input_type: TaskInputType
    response_type: TaskAIResponseType
    max_attempts: Optional[int] = None
    settings: Optional[Any] = None

class AssignmentRequest(BaseModel):
    id: Optional[int] = None
    title: str
    type: Optional[str] = None
    status: Optional[str] = None
    scheduled_publish_at: Optional[str] = None
    assignment: Assignment

try:
    payload = {
        "title": "Test Assignment",
        "assignment": {
            "title": "Test Assignment",
            "blocks": [],
            "context": null,
            "evaluation_criteria": {
                "scorecard_id": null,
                "min_score": 1,
                "max_score": 4,
                "pass_score": 3
            },
            "input_type": "text",
            "response_type": "chat",
            "max_attempts": null,
            "settings": {
                "allowCopyPaste": true
            }
        },
        "scheduled_publish_at": null,
        "status": "draft"
    }
    
    # Python null -> None handling
    import json
    data = json.loads(json.dumps(payload).replace('null', 'None'))
    # Validating
    result = AssignmentRequest.model_validate(data)
    print("ALL GOOD!")
except Exception as e:
    import json
    print(f"ERROR: {e}")
