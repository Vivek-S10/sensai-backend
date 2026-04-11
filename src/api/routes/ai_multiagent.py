import logging
import json
import asyncio
from typing import List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.models.user import User
from api.dependencies import get_current_user
from api.services.llm_service import run_llm_with_openai, openai_plan_to_model_name
from langfuse.decorators import observe, langfuse_context

logger = logging.getLogger(__name__)

router = APIRouter()

class AssessmentTopicsChatRequest(BaseModel):
    task_id: int
    user_id: int
    new_message: str
    chat_history: List[dict]

class LLMGeneratedQuestion(BaseModel):
    title: str = Field(description="A short descriptive title for the question")
    topic: str
    difficulty: str = Field(description="E.g. Easy, Medium, Hard")
    points: int
    question_type: Literal["mcq", "coding"]
    question_text: str = Field(description="The full question properly formatted. Include options if MCQ.")
    answer_text: str = Field(description="The correct answer or reference solution properly formatted.")
    coding_languages: Optional[List[str]] = Field(None, description="For coding questions, a list of languages like ['python', 'javascript']. NULL for MCQ.")
    scorecard_criteria: Optional[List[str]] = Field(None, description="For coding questions, a list of testing criteria like ['Handles edge cases', 'Optimal']. NULL for MCQ.")

class Output(BaseModel):
    questions: List[LLMGeneratedQuestion]

@router.post("/assessment/generate-questions-multiagent")
async def generate_questions_multiagent(request: AssessmentTopicsChatRequest):
    """
    Step 1 of Multi-Agent Pipeline: Streamed Agent Progress.
    First we stream the state of each agent sequentially to the UI.
    For now, it mocks the intermediate steps and ultimately runs the Refinement Agent.
    """
    async def event_generator():
        # Agent 1: Question Generator
        yield json.dumps({"status": "Question Generator: Drafting initial question structure..."}) + "\n"
        await asyncio.sleep(1.5)

        # Agent 2: Critic Agents (Parallel)
        yield json.dumps({"status": "Critic Agents: Reviewing Technical Depth & Clarity..."}) + "\n"
        await asyncio.sleep(2)

        # Agent 3: Consensus Engine
        yield json.dumps({"status": "Consensus Engine: resolving conflicting critic feedback..."}) + "\n"
        await asyncio.sleep(1.5)

        # Agent 4: Refinement Engine (Actual single LLM generation for now, later we'll pipe the real strings in)
        yield json.dumps({"status": "Refinement Engine: Finalizing output JSON format..."}) + "\n"
        
        system_prompt = """You are an expert curriculum designer and exam creator. 
The user will provide a structured final chat history of a curriculum breakdown.
Your task is to generate EXACTLY the requested questions based on the final agreed upon table in the chat history.
Each question MUST adhere to the requested point value, difficulty, and topic.
For MCQs, provide the full question text including the options natively inside the text block, and clearly indicate the correct option in the answer block.
For Coding questions, provide the problem statement in the question block, the expected correct logic/code in the answer block, the applicable coding languages, and a few succinct grading criteria (e.g. 'Handles edge cases', 'Optimal time complexity').
"""
        messages = [{"role": "system", "content": system_prompt}]
        for message in request.chat_history:
             messages.append({"role": message["role"], "content": message["content"]})
        messages.append({"role": "user", "content": "Proceed to generate all the questions now as structured JSON."})

        try:
            model = openai_plan_to_model_name["text"]
            response = await run_llm_with_openai(
                model=model,
                messages=messages,
                response_format=Output
            )
            # Final output to frontend
            final_data = response.model_dump()
            yield json.dumps({"final_output": final_data["questions"]}) + "\n"
        except Exception as e:
            logger.error(f"Error in multi-agent pipeline: {str(e)}")
            yield json.dumps({"status": f"Agent Pipeline failed: {str(e)}"}) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")
