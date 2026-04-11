import os
import asyncio
import traceback
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import AsyncGenerator, Optional, Dict, Literal, List, Any
import uuid
import json
from copy import deepcopy
from contextlib import nullcontext
from pydantic import BaseModel, Field, create_model
from api.config import openai_plan_to_model_name
from api.models import (
    AIChatRequest,
    AssessmentTopicsChatRequest,
    ChatResponseType,
    TaskType,
    QuestionType,
)
from api.llm import (
    run_llm_with_openai,
    stream_llm_with_openai,
)
from api.settings import settings
from api.db.task import (
    get_task_metadata,
    get_question,
    get_task,
    get_scorecard,
)
from api.db.chat import (
    get_question_chat_history_for_user,
    get_task_chat_history_for_user,
)
from api.db.utils import construct_description_from_blocks
from api.utils.s3 import (
    download_file_from_s3_as_bytes,
    get_media_upload_s3_key_from_uuid,
)
from api.utils.audio import prepare_audio_input_for_ai
from api.utils.file_analysis import extract_submission_file
from api.db.user import get_user_first_name
from langfuse import get_client, observe

router = APIRouter()

langfuse = get_client()

LANGFUSE_PROMPT_LABEL = settings.langfuse_tracing_environment
LANGFUSE_ENABLED = bool(
    settings.langfuse_public_key
    and settings.langfuse_secret_key
    and settings.langfuse_host
    and settings.langfuse_tracing_environment
)


def _prompt_metadata(prompt: Any) -> dict[str, Any]:
    if prompt is None:
        return {}

    return {
        "prompt_version": prompt.version,
        "prompt_name": prompt.name,
    }


def _get_langfuse_prompt(prompt_name: str, **compile_kwargs):
    try:
        prompt = langfuse.get_prompt(
            prompt_name, type="chat", label=LANGFUSE_PROMPT_LABEL
        )
        messages = prompt.compile(**compile_kwargs)
        return prompt, messages
    except Exception as e:
        print(f"Failed to get prompt from Langfuse ({prompt_name}): {e}")
        return None, None


class _NoopLangfuseContext:
    def update(self, *args, **kwargs):
        return None

    def update_trace(self, *args, **kwargs):
        return None


def _start_span(name: str):
    if LANGFUSE_ENABLED:
        return langfuse.start_as_current_span(name=name)
    return nullcontext(_NoopLangfuseContext())


def _start_observation(**kwargs):
    if LANGFUSE_ENABLED:
        return langfuse.start_as_current_observation(**kwargs)
    return nullcontext(_NoopLangfuseContext())


def convert_chat_history_to_prompt(chat_history: list[dict]) -> str:
    role_to_label = {
        "user": "Student",
        "assistant": "AI",
    }
    return "\n".join(
        [
            f"<{role_to_label[message['role']]}>\n{message['content']}\n</{role_to_label[message['role']]}>"
            for message in chat_history
        ]
    )


def get_latest_file_uuid_from_chat_history(chat_history: list[dict]) -> Optional[str]:
    """
    Extract the latest file_uuid from chat history.
    """
    if not chat_history:
        return None

    # Iterate through chat history in reverse to find the latest file submission
    for message in reversed(chat_history):
        if message.get("role") == "user":
            content = message.get("content", "")
            if isinstance(content, str):
                try:
                    # Try to parse as JSON to check if it's a file submission
                    file_data = json.loads(content)
                    if isinstance(file_data, dict) and "file_uuid" in file_data:
                        return file_data["file_uuid"]
                except (json.JSONDecodeError, TypeError):
                    # Not a JSON string, continue
                    continue
    return None


def format_chat_history_with_audio(chat_history: list[dict]) -> str:
    chat_history = deepcopy(chat_history)

    role_to_label = {
        "user": "Student",
        "assistant": "AI",
    }

    parts = []

    for message in chat_history:
        label = role_to_label[message["role"]]

        if isinstance(message["content"], list):
            for item in message["content"]:
                if item["type"] == "input_audio":
                    item.pop("input_audio")
                    item["content"] = "<audio_message>"

        if message["role"] == "user":
            content = message["content"]
            parts.append(f"**{label}**\n\n```\n{content}\n```\n\n")
        else:
            # Wherever there is a single \n followed by content before and either nothing after or non \n after, replace that \n with 2 \n\n
            import re

            # Replace a single newline between content with double newlines, except when already double or more
            def single_newline_to_double(text):
                # This regex matches single \n (not preceded nor followed by \n) with non-\n after, or end of string
                #  - positive lookbehind: previous char is not \n
                #  - match \n
                #  - negative lookahead: next char is not \n
                #  - next char is not \n or is end of string
                return re.sub(r"(?<!\n)\n(?!\n)", "\n\n", text)

            content_str = single_newline_to_double(
                message["content"].replace("```", "\n")
            )
            parts.append(f"**{label}**\n\n{content_str}\n\n")

    return "\n\n---\n\n".join(parts)


@observe(name="rewrite_query")
async def rewrite_query(
    chat_history: list[dict],
    question_details: str,
    user_id: str = None,
    is_root_trace: bool = False,
):
    # rewrite query
    prompt, messages = _get_langfuse_prompt(
        "rewrite-query",
        chat_history=convert_chat_history_to_prompt(chat_history),
        reference_material=question_details,
    )
    if messages is None:
        messages = [
            {
                "role": "system",
                "content": (
                    "Rewrite the student's latest message to be clear, concise, and "
                    "well-structured while preserving original meaning and intent."
                ),
            },
            {
                "role": "system",
                "content": (
                    "Reference material for grounding (do not invent beyond this):\n\n"
                    f"{question_details}"
                ),
            },
        ]

    model = openai_plan_to_model_name["text-mini"]

    class Output(BaseModel):
        rewritten_query: str = Field(
            description="The rewritten query/message of the student"
        )

    messages += chat_history

    pred = await run_llm_with_openai(
        model=model,
        messages=messages,
        response_model=Output,
        max_output_tokens=8192,
        langfuse_prompt=prompt,
    )

    llm_input = f"# Chat History\n\n{convert_chat_history_to_prompt(chat_history)}\n\n# Reference Material\n\n{question_details}"

    if is_root_trace:
        langfuse_update_fn = langfuse.update_current_trace
    else:
        langfuse_update_fn = langfuse.update_current_generation

    output = pred.rewritten_query
    if LANGFUSE_ENABLED:
        langfuse_update_fn(
            input=llm_input,
            output=output,
            metadata={
                **_prompt_metadata(prompt),
                "input": llm_input,
                "output": output,
            },
        )

    if LANGFUSE_ENABLED and user_id is not None and is_root_trace:
        langfuse.update_current_trace(
            user_id=user_id,
        )

    return output


@observe(name="router")
async def get_model_for_task(
    chat_history: list[dict],
    question_details: str,
    user_id: str = None,
    is_root_trace: bool = False,
):
    class Output(BaseModel):
        chain_of_thought: str = Field(
            description="The chain of thought process for the decision to use a reasoning model or a general-purpose model"
        )
        use_reasoning_model: bool = Field(
            description="Whether to use a reasoning model to evaluate the student's response"
        )

    prompt, messages = _get_langfuse_prompt(
        "router",
        task_details=question_details,
    )
    if messages is None:
        messages = [
            {
                "role": "system",
                "content": f"You are a router that decides whether to use a reasoning model (o3-mini) or a general-purpose model (gpt-4o-mini) for the following task:\n\n{question_details}",
            }
        ]

    messages += chat_history

    router_output = await run_llm_with_openai(
        model=openai_plan_to_model_name["router"],
        messages=messages,
        response_model=Output,
        max_output_tokens=4096,
        langfuse_prompt=prompt,
    )

    use_reasoning_model = router_output.use_reasoning_model

    if use_reasoning_model:
        model = openai_plan_to_model_name["reasoning"]
    else:
        model = openai_plan_to_model_name["text"]

    llm_input = f"# Chat History\n\n{convert_chat_history_to_prompt(chat_history)}\n\n# Task Details\n\n{question_details}"

    if is_root_trace:
        langfuse_update_fn = langfuse.update_current_trace
    else:
        langfuse_update_fn = langfuse.update_current_generation

    if LANGFUSE_ENABLED:
        langfuse_update_fn(
            input=llm_input,
            output=use_reasoning_model,
            metadata={
                **_prompt_metadata(prompt),
                "input": llm_input,
                "output": use_reasoning_model,
            },
        )

    if LANGFUSE_ENABLED and user_id is not None and is_root_trace:
        langfuse.update_current_trace(
            user_id=user_id,
        )

    return model


def get_user_audio_message_for_chat_history(uuid: str) -> list[dict]:
    if settings.s3_folder_name:
        audio_data = download_file_from_s3_as_bytes(
            get_media_upload_s3_key_from_uuid(uuid, "wav")
        )
    else:
        with open(os.path.join(settings.local_upload_folder, f"{uuid}.wav"), "rb") as f:
            audio_data = f.read()

    return [
        {
            "type": "input_audio",
            "input_audio": {
                "data": prepare_audio_input_for_ai(audio_data),
                "format": "wav",
            },
        },
    ]


def format_ai_scorecard_report(scorecard: list[dict]) -> str:
    scorecard_as_prompt = []
    for criterion in scorecard:
        row_as_prompt = []
        row_as_prompt.append(f"""**{criterion['category']}**: {criterion['score']}""")

        if criterion["feedback"].get("correct"):
            row_as_prompt.append(
                f"""What worked well: {criterion['feedback']['correct']}"""
            )
        if criterion["feedback"].get("wrong"):
            row_as_prompt.append(
                f"""What needs improvement: {criterion['feedback']['wrong']}"""
            )

        row_as_prompt = "\n".join(row_as_prompt)
        scorecard_as_prompt.append(row_as_prompt)

    return "\n\n".join(scorecard_as_prompt)


def convert_scorecard_to_prompt(scorecard: list[dict]) -> str:
    scoring_criteria_as_prompt = []

    for index, criterion in enumerate(scorecard["criteria"]):
        criterion_name = criterion["name"].replace('"', "")
        scoring_criteria_as_prompt.append(
            f"""Criterion {index + 1}:\n**Name**: **{criterion_name}** [min_score: {criterion['min_score']}, max_score: {criterion['max_score']}, pass_score: {criterion.get('pass_score', criterion['max_score'])}]\n\n{criterion['description']}"""
        )

    return "\n\n".join(scoring_criteria_as_prompt)


def build_evaluation_context(evaluation_criteria: dict) -> str:
    """
    Build evaluation context string with overall scoring info.
    """
    evaluation_context = "**Overall Assignment Scoring:**\n"
    evaluation_context += (
        f"- Minimum Score: {evaluation_criteria.get('min_score', 0)}\n"
    )
    evaluation_context += (
        f"- Maximum Score: {evaluation_criteria.get('max_score', 100)}\n"
    )
    evaluation_context += f"- Pass Score: {evaluation_criteria.get('pass_score', 60)}\n"

    return evaluation_context


async def build_knowledge_base_from_context(context: dict) -> str:
    """
    Build knowledge base description from a context dict that may contain
    blocks and linked material IDs.
    """
    if not context or not context.get("blocks"):
        return ""

    knowledge_blocks = context["blocks"]

    # Add linked learning materials
    linked_ids = context.get("linkedMaterialIds") or []
    for material_id in linked_ids:
        material_task = await get_task(int(material_id))
        if material_task:
            knowledge_blocks += material_task["blocks"]

    return construct_description_from_blocks(knowledge_blocks)


def get_ai_message_for_chat_history(ai_message: dict) -> str:
    message = json.loads(ai_message)

    if "scorecard" not in message or not message["scorecard"]:
        return message["feedback"]

    scorecard_as_prompt = format_ai_scorecard_report(message["scorecard"])

    return f"""Feedback:\n```\n{message['feedback']}\n```\n\nScorecard:\n```\n{scorecard_as_prompt}\n```"""


async def get_user_details_for_prompt(user_id: str) -> str:
    user_first_name = await get_user_first_name(user_id)

    if not user_first_name:
        return ""

    return f"Name: {user_first_name}"


@router.post("/chat")
async def ai_response_for_question(request: AIChatRequest):
    # Define an async generator for streaming
    async def stream_response() -> AsyncGenerator[str, None]:
        with _start_span(
            name="ai_chat",
        ) as trace:
            metadata = {
                "task_id": request.task_id,
                "user_id": request.user_id,
                "user_email": request.user_email,
            }

            user_details = await get_user_details_for_prompt(request.user_id)

            if request.task_type == TaskType.QUIZ:
                if request.question_id is None and request.question is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Question ID or question is required for {request.task_type} tasks",
                    )

                if request.question_id is not None and request.user_id is None:
                    raise HTTPException(
                        status_code=400,
                        detail="User ID is required when question ID is provided",
                    )

                if request.question and request.chat_history is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Chat history is required when question is provided",
                    )
                if request.question_id is None:
                    session_id = f"quiz_{request.task_id}_preview_{request.user_id}"
                else:
                    session_id = f"quiz_{request.task_id}_{request.question_id}_{request.user_id}"
            else:
                if request.task_id is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Task ID is required for learning material tasks",
                    )

                if request.chat_history is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Chat history is required for learning material tasks",
                    )
                session_id = f"lm_{request.task_id}_{request.user_id}"

            task = await get_task(request.task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            metadata["task_title"] = task["title"]

            new_user_message = [
                {
                    "role": "user",
                    "content": (
                        get_user_audio_message_for_chat_history(request.user_response)
                        if request.response_type == ChatResponseType.AUDIO
                        else request.user_response
                    ),
                }
            ]

            if request.task_type == TaskType.LEARNING_MATERIAL:
                if request.response_type == ChatResponseType.AUDIO:
                    raise HTTPException(
                        status_code=400,
                        detail="Audio response is not supported for learning material tasks",
                    )

                metadata["type"] = "learning_material"

                chat_history = request.chat_history

                chat_history = [
                    {"role": message["role"], "content": message["content"]}
                    for message in chat_history
                ]

                reference_material = construct_description_from_blocks(task["blocks"])

                rewritten_query = await rewrite_query(
                    chat_history + new_user_message, reference_material
                )

                # update the last user message with the rewritten query
                new_user_message[0]["content"] = rewritten_query

                question_details = f"**Reference Material**\n\n{reference_material}\n\n"
            else:
                metadata["type"] = "quiz"

                if request.question_id:
                    question = await get_question(request.question_id)
                    if not question:
                        raise HTTPException(
                            status_code=404, detail="Question not found"
                        )

                    metadata["question_id"] = request.question_id

                    chat_history = await get_question_chat_history_for_user(
                        request.question_id, request.user_id
                    )

                else:
                    question = request.question.model_dump()
                    chat_history = request.chat_history

                    question["scorecard"] = await get_scorecard(
                        question["scorecard_id"]
                    )
                    metadata["question_id"] = None

                chat_history = [
                    {"role": message["role"], "content": message["content"]}
                    for message in chat_history
                ]

                metadata["question_title"] = question["title"]
                metadata["question_type"] = question["type"]
                metadata["question_purpose"] = (
                    "practice" if question["response_type"] == "chat" else "exam"
                )
                metadata["question_input_type"] = question["input_type"]
                metadata["question_has_context"] = bool(question["context"])

                question_description = construct_description_from_blocks(
                    question["blocks"]
                )
                question_details = f"**Task**\n\n{question_description}\n\n"
                objective_reference = None

            task_metadata = await get_task_metadata(request.task_id)
            if task_metadata:
                metadata.update(task_metadata)

            for message in chat_history:
                if message["role"] == "user":
                    if (
                        request.response_type == ChatResponseType.AUDIO
                        and message.get("response_type") == ChatResponseType.AUDIO
                    ):
                        message["content"] = get_user_audio_message_for_chat_history(
                            message["content"]
                        )
                else:
                    if request.task_type == TaskType.LEARNING_MATERIAL:
                        message["content"] = json.dumps(
                            {"feedback": message["content"]}
                        )

                    message["content"] = get_ai_message_for_chat_history(
                        message["content"]
                    )

            if request.task_type == TaskType.QUIZ:
                if question["type"] == QuestionType.OBJECTIVE:
                    answer_as_prompt = construct_description_from_blocks(
                        question["answer"]
                    )
                    question_details += f"---\n\n**Reference Solution (never to be shared with the learner)**\n\n{answer_as_prompt}\n\n"
                    objective_reference = answer_as_prompt
                else:
                    scorecard_as_prompt = convert_scorecard_to_prompt(
                        question["scorecard"]
                    )
                    question_details += (
                        f"---\n\n**Scoring Criteria**\n\n{scorecard_as_prompt}\n\n"
                    )

            chat_history = chat_history + new_user_message

            # router
            if request.response_type == ChatResponseType.AUDIO:
                model = openai_plan_to_model_name["audio"]
                openai_api_mode = "chat_completions"
            else:
                model = await get_model_for_task(chat_history, question_details)
                openai_api_mode = "responses"

            # response
            llm_input = f"""# Chat History\n\n{format_chat_history_with_audio(chat_history)}\n\n# Task Details\n\n{question_details}"""
            response_metadata = {
                "input": llm_input,
            }

            metadata.update(response_metadata)

            llm_output = ""
            if (
                request.task_type == TaskType.QUIZ
                and question["type"] == QuestionType.OBJECTIVE
                and not LANGFUSE_ENABLED
            ):
                latest_submission = (
                    new_user_message[0]["content"] if new_user_message else ""
                )
                def normalize(text: str) -> str:
                    return text.strip().casefold()

                is_correct = (
                    normalize(latest_submission)
                    == normalize(objective_reference or "")
                    if objective_reference
                    else False
                )
                feedback = (
                    "Your answer is correct."
                    if is_correct
                    else "Your answer is incorrect. Please try again."
                )
                analysis = (
                    "Answer judged correct."
                    if is_correct
                    else "Answer judged incorrect."
                )
                llm_output = {
                    "analysis": analysis,
                    "feedback": feedback,
                    "is_correct": is_correct,
                }
                metadata["output"] = llm_output
                session_id = f"quiz_{request.task_id}_{request.question_id}_{request.user_id}"
                trace.update_trace(
                    user_id=str(request.user_id),
                    session_id=session_id,
                    metadata=metadata,
                    input=llm_input,
                    output=llm_output,
                )
                yield json.dumps(llm_output) + "\n"
                return
            if request.task_type == TaskType.QUIZ:
                if question["type"] == QuestionType.OBJECTIVE:
                    is_assessment_mode = question.get("response_type") != "chat"

                    class ComponentSkill(BaseModel):
                        sub_topic: str = Field(description="The specific granular skill being tested (e.g., 'Array Iteration', 'Pointer Arithmetic')")
                        competency_score: float = Field(description="Score out of 100 representing the user's mastery of this specific sub-topic")
                        analysis: str = Field(description="1-2 sentences explaining why they got this score, pinpointing exactly where they showed strength or weakness.")

                    class Output(BaseModel):
                        analysis: str = Field(
                            description="A detailed analysis of the student's response"
                        )
                        feedback: str = Field(
                            description="Feedback on the student's response; add newline characters to the feedback to make it more readable where necessary; address the student by name if their name has been provided."
                        )
                        is_correct: bool = Field(
                            description="Whether the student's response correctly solves the original task that the student is supposed to solve. For this to be true, the original task needs to be completely solved and not just partially solved. Giving the right answer to one step of the task does not count as solving the entire task."
                        )
                        competency_map: Optional[List[ComponentSkill]] = Field(
                            description="A granular breakdown of the specific micro-skills demonstrated in this test.",
                            default=[]
                        )

                else:

                    class Feedback(BaseModel):
                        correct: Optional[str] = Field(
                            description="What worked well in the student's response for this category based on the scoring criteria"
                        )
                        wrong: Optional[str] = Field(
                            description="What needs improvement in the student's response for this category based on the scoring criteria"
                        )

                    class Row(BaseModel):
                        feedback: Feedback = Field(
                            description="Detailed feedback for the student's response for this category"
                        )
                        score: float = Field(
                            description="Score given within the min/max range for this category based on the student's response - the score given should be in alignment with the feedback provided"
                        )
                        max_score: float = Field(
                            description="Maximum score possible for this category as per the scoring criteria"
                        )
                        pass_score: float = Field(
                            description="Pass score possible for this category as per the scoring criteria"
                        )

                    def make_scorecard_model(fields: list[str]) -> type[BaseModel]:
                        """
                        Dynamically create a Pydantic model with fields from a list of strings.
                        Each field defaults to `str`, but you can change that if needed.
                        """
                        # build dictionary for create_model
                        field_definitions: dict[str, tuple[type, any]] = {
                            field: (Row, ...) for field in fields
                        }
                        # ... means "required"
                        return create_model("Scorecard", **field_definitions)

                    Scorecard = make_scorecard_model(
                        [
                            criterion["name"].replace('"', "")
                            for criterion in question["scorecard"]["criteria"]
                        ]
                    )

                    class ComponentSkillSubjective(BaseModel):
                        sub_topic: str = Field(description="The specific granular skill being tested")
                        competency_score: float = Field(description="Score out of 100")
                        analysis: str = Field(description="Brief analysis for this skill.")

                    class Output(BaseModel):
                        chain_of_thought: str = Field(
                            description="Concise analysis of the student's response and what the scorecard should be."
                        )
                        feedback: str = Field(
                            description="A single, comprehensive summary based on the scoring criteria; address the student by name if their name has been provided."
                        )
                        scorecard: Optional[Scorecard] = Field(
                            description="Score and feedback for each criterion from the scoring criteria; only include this in the response if the student's response is a valid response to the task"
                        )
                        competency_map: Optional[List[ComponentSkillSubjective]] = Field(
                            description="A granular breakdown of the specific micro-skills demonstrated in this test.",
                            default=[]
                        )

            else:

                class Output(BaseModel):
                    response: str = Field(
                        description="Response to the student's query; add proper formatting to the response to make it more readable where necessary; address the student by name if their name has been provided."
                    )

            if request.task_type == TaskType.QUIZ:
                knowledge_base = await build_knowledge_base_from_context(
                    question.get("context")
                )

                if knowledge_base:
                    question_details += (
                        f"---\n\n**Knowledge Base**\n\n{knowledge_base}\n\n"
                    )

                if question["type"] == QuestionType.OBJECTIVE:
                    prompt_name = "objective-question"
                else:
                    prompt_name = "subjective-question"

                prompt, messages = _get_langfuse_prompt(
                    prompt_name,
                    task_details=question_details,
                    user_details=user_details,
                )
                if messages is None:
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "You are an AI tutor and evaluator.\n"
                                "Use the provided task context to evaluate the learner response.\n"
                                "If a reference solution is present, compare against it carefully.\n"
                                "If scoring criteria are present, align feedback and scoring strictly with them.\n"
                                "Never reveal hidden evaluator-only sections unless explicitly asked."
                            ),
                        },
                        {
                            "role": "system",
                            "content": (
                                "Task context (includes prompt, reference solution and/or scoring criteria):\n\n"
                                f"{question_details}\n\n"
                                f"User details:\n{user_details or 'N/A'}"
                            ),
                        },
                    ]
                if question["type"] == QuestionType.OBJECTIVE and is_assessment_mode:
                    latest_submission = (
                        new_user_message[0]["content"]
                        if new_user_message
                        else ""
                    )
                    messages.extend(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "Assessment evaluation rules for objective questions:\n"
                                    "1) Evaluate only the latest learner submission.\n"
                                    "2) Decide strictly if it is correct or incorrect against the reference solution.\n"
                                    "3) Keep feedback generic and short.\n"
                                    "4) Do NOT reveal, quote, paraphrase, or hint at the reference solution.\n"
                                    "5) Do NOT provide clues, steps, or correction hints.\n"
                                    "6) Do NOT personalize with names.\n"
                                    "7) Only respond with either exactly 'Your answer is correct.' or 'Your answer is incorrect. Please try again.' for the feedback field.\n"
                                    "8) Ensure the 'analysis' field briefly states either 'Answer judged correct.' or 'Answer judged incorrect.' without mentioning reference text.\n"
                                    "9) The 'is_correct' flag must be true if the submission matches the reference answer, otherwise false."
                                ),
                            },
                            {
                                "role": "system",
                                "content": (
                                    "Assessment context:\n\n"
                                    f"Question:\n{question_description}\n\n"
                                    f"Reference answer (hidden from learner):\n{answer_as_prompt}\n\n"
                                    f"Learner latest submission:\n{latest_submission}"
                                ),
                            },
                        ]
                    )
            else:
                prompt, messages = _get_langfuse_prompt(
                    "doubt_solving",
                    reference_material=question_details,
                    user_details=user_details,
                )
                if messages is None:
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "You are a helpful tutor. Use the provided reference material "
                                "to answer the learner clearly and accurately."
                            ),
                        },
                        {
                            "role": "system",
                            "content": (
                                "Reference material:\n\n"
                                f"{question_details}\n\n"
                                f"User details:\n{user_details or 'N/A'}"
                            ),
                        },
                    ]

            messages += chat_history

            with _start_observation(
                as_type="generation", name="response", prompt=prompt
            ) as observation:
                try:
                    async for chunk in stream_llm_with_openai(
                        model=model,
                        messages=messages,
                        response_model=Output,
                        max_output_tokens=8192,
                        api_mode=openai_api_mode,
                    ):
                        content = json.dumps(chunk.model_dump()) + "\n"
                        llm_output = chunk.model_dump()
                        yield content
                except Exception as e:
                    # Check if it's the specific AsyncStream aclose error
                    if str(e) == "'AsyncStream' object has no attribute 'aclose'":
                        # Silently end partial stream on this specific error
                        pass
                    else:
                        # Re-raise other exceptions
                        raise
                finally:
                    observation.update(
                        input=llm_input,
                        output=llm_output,
                        prompt=prompt,
                        metadata={
                            **_prompt_metadata(prompt),
                            **response_metadata,
                        },
                    )

            metadata["output"] = llm_output
            trace.update_trace(
                user_id=str(request.user_id),
                session_id=session_id,
                metadata=metadata,
                input=llm_input,
                output=llm_output,
            )

    async def stream_response_safe() -> AsyncGenerator[str, None]:
        try:
            async for chunk in stream_response():
                yield chunk
        except Exception:
            traceback.print_exc()
            if request.task_type == TaskType.QUIZ:
                yield (
                    json.dumps(
                        {
                            "analysis": "Unable to evaluate the response.",
                            "feedback": "There was an error while processing your answer. Please try again.",
                            "is_correct": False,
                        }
                    )
                    + "\n"
                )
            elif request.task_type == TaskType.LEARNING_MATERIAL:
                yield (
                    json.dumps(
                        {
                            "response": "There was an error while processing your answer. Please try again.",
                        }
                    )
                    + "\n"
                )
            else:
                yield (
                    json.dumps(
                        {
                            "response": "There was an error while processing your answer. Please try again.",
                        }
                    )
                    + "\n"
                )

    # Return a streaming response
    return StreamingResponse(
        stream_response_safe(),
        media_type="application/x-ndjson",
    )


@router.post("/assignment")
async def ai_response_for_assignment(request: AIChatRequest):
    # Define an async generator for streaming
    async def stream_response() -> AsyncGenerator[str, None]:
        with _start_span(
            name="assignment_evaluation",
        ) as trace:
            metadata = {
                "task_id": request.task_id,
                "user_id": request.user_id,
                "user_email": request.user_email,
            }

            user_details = await get_user_details_for_prompt(request.user_id)

            # Validate required fields for assignment
            if request.task_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="Task ID is required for assignment tasks",
                )

            # Get assignment data
            task = await get_task(request.task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            if task["type"] != TaskType.ASSIGNMENT:
                raise HTTPException(status_code=400, detail="Task is not an assignment")

            metadata["task_title"] = task["title"]

            assignment = task["assignment"]
            problem_blocks = assignment["blocks"]
            evaluation_criteria = assignment["evaluation_criteria"]

            if not evaluation_criteria:
                raise HTTPException(
                    status_code=400,
                    detail="Assignment is missing evaluation criteria",
                )

            if not evaluation_criteria.get("scorecard_id"):
                raise HTTPException(
                    status_code=400,
                    detail="Assignment evaluation criteria is missing scorecard_id",
                )

            context = assignment.get("context")

            # Get scorecard for evaluation
            scorecard = await get_scorecard(evaluation_criteria["scorecard_id"])

            if not scorecard:
                raise HTTPException(
                    status_code=400,
                    detail="Scorecard not found for assignment evaluation criteria",
                )

            # Get chat history for this assignment
            # Use request.chat_history if provided (for preview mode), otherwise fetch from database
            if request.chat_history:
                chat_history = request.chat_history

                if chat_history is None:
                    # For first-time submissions (file uploads), chat_history might be empty
                    # We'll initialize it as empty if not provided
                    chat_history = []
            else:
                chat_history = await get_task_chat_history_for_user(
                    request.task_id, request.user_id
                )

            # Convert chat history to the format expected by AI
            formatted_chat_history = [
                {"role": message["role"], "content": message["content"]}
                for message in chat_history
            ]

            # Add new user message
            new_user_message = [
                {
                    "role": "user",
                    "content": (
                        get_user_audio_message_for_chat_history(request.user_response)
                        if request.response_type == ChatResponseType.AUDIO
                        else request.user_response
                    ),
                }
            ]

            # Build problem statement from blocks
            problem_statement = construct_description_from_blocks(problem_blocks)

            # Build full chat history
            if request.response_type == ChatResponseType.FILE:
                # For file uploads, include only the new user message with file_uuid
                # This branch is triggered when a learner submits an assignment as a file
                full_chat_history = new_user_message
            else:
                # This branch is triggered when a learner answers questions about the assignment with text or audio
                full_chat_history = formatted_chat_history + new_user_message

            # Handle file submission - extract code
            submission_data = None

            if request.response_type == ChatResponseType.FILE:
                submission_data = extract_submission_file(request.user_response)
            else:
                # Not a file upload, check chat history for latest file submission
                latest_file_uuid = get_latest_file_uuid_from_chat_history(
                    full_chat_history
                )

                if latest_file_uuid:
                    submission_data = extract_submission_file(latest_file_uuid)

            # Build evaluation context
            evaluation_context = build_evaluation_context(evaluation_criteria)

            # Build Key Areas section from scorecard
            key_areas_section = f"\n\n<Key Areas>\n\n{convert_scorecard_to_prompt(scorecard)}\n\n</Key Areas>"

            # Build context with linked materials if available
            knowledge_base = await build_knowledge_base_from_context(context)

            # Build the complete assignment context
            assignment_details = (
                f"<Problem Statement>\n\n{problem_statement}\n\n</Problem Statement>"
            )

            # Add Key Areas from scorecard
            if key_areas_section:
                assignment_details += key_areas_section

            if evaluation_context:
                assignment_details += f"\n\n<Evaluation Criteria>\n\n{evaluation_context}\n\n</Evaluation Criteria>"

            if knowledge_base:
                assignment_details += (
                    f"\n\n<Knowledge Base>\n\n{knowledge_base}\n\n</Knowledge Base>"
                )

            # Add submission data for file uploads
            if submission_data:
                assignment_details += f"\n\n<Student Submission Data>"
                assignment_details += f"\n\n**File Contents:**\n"
                for filename, content in submission_data["file_contents"].items():
                    assignment_details += (
                        f"\n--- {filename} ---\n{content}\n--- End of {filename} ---\n"
                    )
                assignment_details += f"\n\n</Student Submission Data>"

            # Process chat history for audio content if needed
            if request.response_type == ChatResponseType.AUDIO:
                for message in full_chat_history:
                    if (
                        message["role"] == "user"
                        and message.get("response_type") == ChatResponseType.AUDIO
                    ):
                        message["content"] = get_user_audio_message_for_chat_history(
                            message["content"]
                        )

            # Determine model based on input type
            if request.response_type == ChatResponseType.AUDIO:
                model = openai_plan_to_model_name["audio"]
                openai_api_mode = "chat_completions"
            else:
                # For assignments, use reasoning model for better evaluation
                model = openai_plan_to_model_name["reasoning"]
                openai_api_mode = "responses"

            # Enhanced feedback structure for key area scores
            class Feedback(BaseModel):
                correct: Optional[str] = Field(
                    description="What worked well in the student's response for this category based on the scoring criteria"
                )
                wrong: Optional[str] = Field(
                    description="What needs improvement in the student's response for this category based on the scoring criteria"
                )

            class KeyAreaScore(BaseModel):
                feedback: Feedback = Field(
                    description="Detailed feedback for the student's response for this category"
                )
                score: float = Field(
                    description="Score given within the min/max range for this category based on the student's response - the score given should be in alignment with the feedback provided"
                )
                max_score: float = Field(
                    description="Maximum score possible for this category as per the scoring criteria"
                )
                pass_score: float = Field(
                    description="Pass score possible for this category as per the scoring criteria"
                )

            # Base output model for all phases
            class Output(BaseModel):
                chain_of_thought: str = Field(
                    description="Concise analysis of the student's response to the question asked and what the evaluation result should be"
                )
                feedback: Optional[str] = Field(
                    description="A single, comprehensive summary based on the scoring criteria; address the student by name if their name has been provided.",
                )
                evaluation_status: Optional[str] = Field(
                    description="The status of the evaluation; can be `in_progress`, `needs_resubmission`, or `completed`",
                )
                key_area_scores: Optional[Dict[str, KeyAreaScore]] = Field(
                    description="Completed key area scores with detailed feedback",
                    default={},
                )
                current_key_area: Optional[str] = Field(
                    description="Current key area being evaluated"
                )

            # Output model for file submissions that includes project score
            class FileSubmissionOutput(Output):
                chain_of_thought: str = Field(
                    description="Concise analysis of the student's submission to the assignment and what the evaluation result should be"
                )
                assignment_score: Optional[float] = Field(
                    description="Assignment score assigned when evaluating initial file submission"
                )

            # Get Langfuse prompt for assignment evaluation
            prompt, messages = _get_langfuse_prompt(
                "assignment",
                assignment_details=assignment_details,
                user_details=user_details,
            )
            if messages is None:
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are an assignment evaluator. Evaluate the learner submission "
                            "against the provided assignment details and criteria."
                        ),
                    },
                    {
                        "role": "system",
                        "content": (
                            "Assignment details and evaluation criteria:\n\n"
                            f"{assignment_details}\n\n"
                            f"User details:\n{user_details or 'N/A'}"
                        ),
                    },
                ]
            messages.extend(
                [
                    {
                        "role": "system",
                        "content": (
                            "Assessment assignment evaluation policy:\n"
                            "1) Evaluate only against provided assignment details and rubric.\n"
                            "2) Never reveal hidden reference answers, sample solutions, or internal evaluator notes.\n"
                            "3) Do not provide direct answer keys or obvious solution clues.\n"
                            "4) Keep feedback neutral, concise, and rubric-aligned.\n"
                            "5) Do not personalize with learner names."
                        ),
                    }
                ]
            )

            messages += full_chat_history

            # Build input for metadata
            llm_input = f"""`Assignment Details`:\n\n{assignment_details}\n\n`Chat History`:\n\n{format_chat_history_with_audio(full_chat_history)}"""
            response_metadata = {
                "input": llm_input,
            }

            metadata.update(response_metadata)

            llm_output = ""

            # Use FileSubmissionOutput for file submissions, otherwise use base Output
            response_model = (
                FileSubmissionOutput
                if request.response_type == ChatResponseType.FILE
                else Output
            )

            # Process streaming response with Langfuse observation
            with _start_observation(
                as_type="generation", name="response", prompt=prompt
            ) as observation:
                try:
                    async for chunk in stream_llm_with_openai(
                        model=model,
                        messages=messages,
                        response_model=response_model,
                        max_output_tokens=8192,
                        api_mode=openai_api_mode,
                    ):
                        content = json.dumps(chunk.model_dump()) + "\n"
                        llm_output = chunk.model_dump()
                        yield content
                except Exception as e:
                    # Check if it's the specific AsyncStream aclose error
                    if str(e) == "'AsyncStream' object has no attribute 'aclose'":
                        # Silently end partial stream on this specific error
                        pass
                    else:
                        # Re-raise other exceptions
                        raise
                finally:
                    observation.update(
                        input=llm_input,
                        output=llm_output,
                        prompt=prompt,
                        metadata={
                            **_prompt_metadata(prompt),
                            **response_metadata,
                        },
                    )

            session_id = f"assignment_{request.task_id}_{request.user_id}"
            metadata["output"] = llm_output
            trace.update_trace(
                user_id=str(request.user_id),
                session_id=session_id,
                metadata=metadata,
                input=llm_input,
                output=llm_output,
            )

    # Return a streaming response
    return StreamingResponse(
        stream_response(),
        media_type="application/x-ndjson",
    )

@router.post("/assessment/topics-chat")
async def ai_response_for_assessment_topics(request: AssessmentTopicsChatRequest):
    # Define an async generator for streaming
    async def stream_response() -> AsyncGenerator[str, None]:
        with _start_span(
            name="assessment_topics_chat",
        ) as trace:
            metadata = {
                "task_id": request.task_id,
                "user_id": request.user_id,
                "type": "assessment_curriculum",
            }
            
            user_details = await get_user_details_for_prompt(str(request.user_id))
            
            # Construct the system message
            system_prompt = """You are an expert curriculum designer. The user will provide a curriculum, topics, or Job Description (JD). 
Your task is to extract the main topics and skills. Assign a weightage (%) to each topic based on its importance, ensuring the total is exactly 100%. 
You must also specify how many MCQs and Coding questions will be generated per topic, and explicitly state the assigned points for each question. 
IMPORTANT LIMIT: At this stage, restrict the cumulative sum of all questions across all topics to exactly 10 questions maximum to ensure reliable generation later.
If the user requests modifications (e.g., 'reduce weight of X', 'add Y', 'I want 5 more coding questions'), adjust the topics, weights, question counts, and points accordingly while maintaining the 100% total and the 10 question limit.
Format your response cleanly using Markdown, with a visible table including the following columns: Topic, Weightage %, # of MCQs, # of Coding Questions, Points per MCQ, Points per Coding Question.
Do NOT generate the actual questions. ONLY discuss and refine the curriculum structure."""

            if user_details:
                system_prompt += f"\n\nUser details:\n{user_details}"

            messages = [{"role": "system", "content": system_prompt}]
            
            # Format chat history
            chat_history = []
            for message in request.chat_history:
                chat_history.append({"role": message["role"], "content": message["content"]})
                
            messages.extend(chat_history)
            
            # Add the new message
            messages.append({"role": "user", "content": request.new_message})

            model = openai_plan_to_model_name["text"]
            
            class Output(BaseModel):
                response: str = Field(
                    description="Response to the user's input, formatted in Markdown, including a table of topics and weightages (adding up to 100%)."
                )
                
            llm_input = json.dumps(messages)
            llm_output = ""

            with _start_observation(
                as_type="generation", name="response",
            ) as observation:
                try:
                    async for chunk in stream_llm_with_openai(
                        model=model,
                        messages=messages,
                        response_model=Output,
                        max_output_tokens=4096,
                        api_mode="responses",
                    ):
                        content = json.dumps(chunk.model_dump()) + "\n"
                        llm_output = chunk.model_dump()
                        yield content
                except Exception as e:
                    if str(e) == "'AsyncStream' object has no attribute 'aclose'":
                        pass
                    else:
                        raise
                finally:
                    observation.update(
                        input=llm_input,
                        output=llm_output,
                        metadata=metadata,
                    )

            trace.update_trace(
                user_id=str(request.user_id),
                session_id=f"assessment_topics_{request.task_id}_{request.user_id}",
                metadata=metadata,
                input=llm_input,
                output=llm_output,
            )

    return StreamingResponse(
        stream_response(),
        media_type="application/x-ndjson",
    )

def text_to_blocks(text: str) -> List[Dict]:
    return [
        {
            "id": str(uuid.uuid4()),
            "type": "paragraph",
            "props": {"textColor": "default", "backgroundColor": "default", "textAlignment": "left"},
            "content": [{"type": "text", "text": line, "styles": {}}]
        }
        for line in text.split('\n') if line.strip()
    ]

@router.post("/assessment/generate-questions")
async def generate_questions(request: AssessmentTopicsChatRequest):
    # Prepare prompt
    system_prompt = """You are an expert curriculum designer and exam creator. 
The user will provide a structured final chat history of a curriculum breakdown, which includes topics, weightages, points, and exactly how many MCQs and Coding questions must be generated for each topic.
Your task is to generate EXACTLY the requested questions based on the final agreed upon table in the chat history.
Each question MUST adhere to the requested point value, difficulty, and topic.
For MCQs, provide the full question text including the options natively inside the text block, and clearly indicate the correct option in the answer block.
For Coding questions, provide the problem statement in the question block, the expected correct logic/code in the answer block, the applicable coding languages, and a few succinct grading criteria (e.g. 'Handles edge cases', 'Optimal time complexity').
"""
    
    messages = [{"role": "system", "content": system_prompt}]
    for message in request.chat_history:
         messages.append({"role": message["role"], "content": message["content"]})
    messages.append({"role": "user", "content": "Proceed to generate all the questions now as structured JSON."})

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

    model = openai_plan_to_model_name["text"]
    
    with _start_observation(as_type="generation", name="generate_questions") as observation:
        response = await run_llm_with_openai(
            model=model,
            messages=messages,
            response_model=Output,
            max_output_tokens=16383,
            api_mode="responses"
        )
        observation.update(
            input=json.dumps(messages),
            output=response.model_dump(),
            metadata={"task_id": request.task_id}
        )

    # Convert LLM model to Frontend QuizQuestion structure
    frontend_questions = []
    for q in response.questions:
        q_id = str(uuid.uuid4())
        
        # Base config
        config = {
            "title": q.title,
            "responseType": "exam",
            "settings": {"topic": q.topic, "difficulty": q.difficulty, "points": q.points, "allowCopyPaste": False},
            "knowledgeBaseBlocks": [],
            "linkedMaterialIds": [],
        }
        
        if q.question_type == "mcq":
            config["questionType"] = "objective"
            config["inputType"] = "text"
            config["correctAnswer"] = text_to_blocks(q.answer_text)
        else: # coding
            config["questionType"] = "subjective"
            config["inputType"] = "code"
            config["codingLanguages"] = q.coding_languages or ["python"]
            config["correctAnswer"] = text_to_blocks(q.answer_text)
            
            # create scorecard criteria
            criteria_list = q.scorecard_criteria or ["Correct Logic", "Efficient Solution"]
            config["scorecardData"] = {
                "title": f"{q.title} Scorecard",
                "criteria": [
                    {
                        "name": crit,
                        "description": crit,
                        "min_score": 0,
                        "max_score": 10,
                        "pass_score": 5
                    } for crit in criteria_list
                ]
            }

        frontend_questions.append({
            "id": q_id,
            "content": text_to_blocks(q.question_text),
            "config": config
        })
        
    return frontend_questions

class AssessmentEditQuestionRequest(BaseModel):
    task_id: int
    user_prompt: str
    original_question_text: str
    original_answer_text: str
    metadata: Dict
    question_type: str

@router.post("/assessment/edit-question")
async def edit_question(request: AssessmentEditQuestionRequest):
    system_prompt = f"""You are an expert curriculum designer and exam creator. 
The user previously generated an assessment question but wants to specifically edit it.
You must address their requested changes precisely.

Here is the original configuration:
Topic: {request.metadata.get('topic')}
Difficulty: {request.metadata.get('difficulty')}
Points: {request.metadata.get('points')}
Format requested: {request.question_type}

Original Question:
{request.original_question_text}

Original Reference Answer:
{request.original_answer_text}

The user's specific edit command is: "{request.user_prompt}"
Rewrite the question and answer to fulfill this command. Ensure you map everything exactly as required to a valid {request.question_type} question.
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Return the updated question."}
    ]

    class LLMEditedQuestion(BaseModel):
        title: str = Field(description="A short descriptive title for the question")
        question_text: str = Field(description="The full edited question. Include options natively in text if MCQ.")
        answer_text: str = Field(description="The correct edited answer or reference solution.")
        coding_languages: Optional[List[str]] = Field(None, description="For coding questions only.")
        scorecard_criteria: Optional[List[str]] = Field(None, description="For coding questions only.")

    model = openai_plan_to_model_name["text"]
    response = await run_llm_with_openai(
        model=model,
        messages=messages,
        response_model=LLMEditedQuestion,
        max_output_tokens=4096,
        api_mode="responses"
    )

    q = response
    config = {
        "title": q.title,
        "responseType": "exam",
        "settings": request.metadata, # Preserve original
        "knowledgeBaseBlocks": [],
        "linkedMaterialIds": [],
    }
    
    if request.question_type == 'mcq' or request.question_type == 'objective':
        config["questionType"] = "objective"
        config["inputType"] = "text"
        config["correctAnswer"] = text_to_blocks(q.answer_text)
    else:
        config["questionType"] = "subjective"
        config["inputType"] = "code"
        config["codingLanguages"] = q.coding_languages or ["python"]
        config["correctAnswer"] = text_to_blocks(q.answer_text)
        criteria_list = q.scorecard_criteria or ["Correct Logic"]
        config["scorecardData"] = {
            "title": f"{q.title} Scorecard",
            "criteria": [
                {"name": c, "description": c, "min_score": 0, "max_score": 10, "pass_score": 5}
                for c in criteria_list
            ]
        }

    return {
        "content": text_to_blocks(q.question_text),
        "config": config
    }

@router.post("/assessment/generate-questions-multiagent")
async def generate_questions_multiagent(request: AssessmentTopicsChatRequest):
    """
    Full Multi-Agent Pipeline for Assessment Generation.
    """
    async def event_generator():
        try:
            model = openai_plan_to_model_name["text"]
            
            # ---------------------------------------------------------
            # 1. Agent 1: Question Generator (Draft)
            # ---------------------------------------------------------
            yield json.dumps({"status": "Agent [1/4]: Generating initial draft questions..."}) + "\n"
            await asyncio.sleep(0.1) # Force socket flush
            
            draft_system_prompt = """You are the Question Generator. 
Based on the provided curriculum and topics, generate an initial draft of the questions.
Do your best, but know that critic agents will review your work."""

            draft_messages = [{"role": "system", "content": draft_system_prompt}]
            for message in request.chat_history:
                 draft_messages.append({"role": message["role"], "content": message["content"]})
            draft_messages.append({"role": "user", "content": "Generate the initial draft questions as raw text/markdown."})

            class DraftOutput(BaseModel):
                draft: str = Field(description="The full raw text of all drafted questions and answers.")

            draft_response = await run_llm_with_openai(
                model=model,
                messages=draft_messages,
                response_model=DraftOutput,
                max_output_tokens=8192,
                api_mode="responses"
            )
            raw_draft = draft_response.draft

            # ---------------------------------------------------------
            # 2. Agent 2: Critic Agents (Parallel)
            # ---------------------------------------------------------
            yield json.dumps({"status": "Agent [2/4]: Critic Agents (Tech Driller, JD Alignment, UX Guard) reviewing draft..."}) + "\n"
            await asyncio.sleep(0.1)

            curriculum_context = "\n".join([m["content"] for m in request.chat_history])
            
            class CriticOutput(BaseModel):
                critique: str = Field(description="Detailed critique and suggested fixes.")

            async def run_critic(persona_prompt: str) -> str:
                critic_msgs = [
                    {"role": "system", "content": persona_prompt},
                    {"role": "user", "content": f"Curriculum Context:\n{curriculum_context}\n\nDraft Questions:\n{raw_draft}\n\nProvide your critique."}
                ]
                resp = await run_llm_with_openai(
                    model=model,
                    messages=critic_msgs,
                    response_model=CriticOutput,
                    max_output_tokens=4096,
                    api_mode="responses"
                )
                return resp.critique

            critic_1_prompt = "Act as a Senior Backend Engineer (The 'Technical Driller'). Look for logical fallacies, check if the code tests high-level logic rather than just memorization, and ensure technical accuracy, edge cases, and difficulty scaling match the curriculum."
            critic_2_prompt = "Act as a Technical Recruiter (The 'JD Alignment Officer'). Cross-reference the draft questions with the curriculum. Stop generic 'LeetCode' questions if they don't apply. Is the skill actually required for this role?"
            critic_3_prompt = "Act as a Coding Instructor (The 'UX & Clarity Guard'). Focus on readability, prompt clarity, and time constraints. Are the instructions clear enough? Ensure no 'trick' wording and sufficient input/output examples."

            critiques = await asyncio.gather(
                run_critic(critic_1_prompt),
                run_critic(critic_2_prompt),
                run_critic(critic_3_prompt)
            )
            
            # Send critiques to frontend logs
            yield json.dumps({"log": {"title": "Critique: Technical Driller", "text": critiques[0]}}) + "\n"
            await asyncio.sleep(0.1)
            yield json.dumps({"log": {"title": "Critique: JD Alignment Officer", "text": critiques[1]}}) + "\n"
            await asyncio.sleep(0.1)
            yield json.dumps({"log": {"title": "Critique: UX & Clarity Guard", "text": critiques[2]}}) + "\n"
            await asyncio.sleep(0.1)

            # ---------------------------------------------------------
            # 3. Agent 3: Consensus Engine
            # ---------------------------------------------------------
            yield json.dumps({"status": "Agent [3/4]: Consensus Engine merging reviews..."}) + "\n"
            await asyncio.sleep(0.1)

            consensus_prompt = """You are the Consensus Engine.
You will receive the original draft and critiques from 3 different experts:
1. Technical Driller (Accuracy/Logic)
2. JD Alignment Officer (Relevance)
3. UX & Clarity Guard (Readability)

Merge their feedback, resolve any conflicting priorities, and produce a unified action plan for the Refinement Engine."""

            consensus_msgs = [
                {"role": "system", "content": consensus_prompt},
                {"role": "user", "content": f"Original Draft:\n{raw_draft}\n\nCritique 1 (Tech):\n{critiques[0]}\n\nCritique 2 (JD):\n{critiques[1]}\n\nCritique 3 (UX):\n{critiques[2]}\n\nProvide the unified action plan."}
            ]

            class ConsensusOutput(BaseModel):
                unified_plan: str = Field(description="The merged action plan for rewriting the questions.")

            consensus_resp = await run_llm_with_openai(
                model=model,
                messages=consensus_msgs,
                response_model=ConsensusOutput,
                max_output_tokens=4096,
                api_mode="responses"
            )
            unified_plan = consensus_resp.unified_plan
            
            yield json.dumps({"log": {"title": "Consensus Engine: Unified Plan", "text": unified_plan}}) + "\n"
            await asyncio.sleep(0.1)

            # ---------------------------------------------------------
            # 4. Agent 4: Refinement Engine
            # ---------------------------------------------------------
            yield json.dumps({"status": "Agent [4/4]: Refinement Engine applying consensus and generating final payload..."}) + "\n"
            await asyncio.sleep(0.1)

            refine_prompt = """You are the Master Editor (Refinement Engine).
Take the original draft and the unified action plan from the Consensus Engine.
Rewrite the questions fully to incorporate all improvements.
Each question MUST adhere to the requested point value, difficulty, and topic from the curriculum.
For MCQs, provide the full question text including the options natively inside the text block, and clearly indicate the correct option in the answer block.
For Coding questions, provide the problem statement in the question block, the expected correct logic/code in the answer block, the applicable coding languages, and a few succinct grading criteria."""

            refine_msgs = [
                {"role": "system", "content": refine_prompt},
                {"role": "user", "content": f"Original Draft:\n{raw_draft}\n\nUnified Action Plan:\n{unified_plan}\n\nProduce the final questions as structured JSON."}
            ]

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

            final_response = await run_llm_with_openai(
                model=model,
                messages=refine_msgs,
                response_model=Output,
                max_output_tokens=16383,
                api_mode="responses"
            )

            # Convert LLM model to Frontend QuizQuestion structure
            frontend_questions = []
            for q in final_response.questions:
                q_id = str(uuid.uuid4())
                
                config = {
                    "title": q.title,
                    "responseType": "exam",
                    "settings": {"topic": q.topic, "difficulty": q.difficulty, "points": q.points, "allowCopyPaste": False},
                    "knowledgeBaseBlocks": [],
                    "linkedMaterialIds": [],
                }
                
                if q.question_type == "mcq":
                    config["questionType"] = "objective"
                    config["inputType"] = "text"
                    config["correctAnswer"] = text_to_blocks(q.answer_text)
                else:
                    config["questionType"] = "subjective"
                    config["inputType"] = "code"
                    config["codingLanguages"] = q.coding_languages or ["python"]
                    config["correctAnswer"] = text_to_blocks(q.answer_text)
                    
                    criteria_list = q.scorecard_criteria or ["Correct Logic", "Efficient Solution"]
                    config["scorecardData"] = {
                        "title": f"{q.title} Scorecard",
                        "criteria": [
                            {
                                "name": crit,
                                "description": crit,
                                "min_score": 0,
                                "max_score": 10,
                                "pass_score": 5
                            } for crit in criteria_list
                        ]
                    }

                frontend_questions.append({
                    "id": q_id,
                    "content": text_to_blocks(q.question_text),
                    "config": config
                })
                
            yield json.dumps({"final_output": frontend_questions}) + "\n"
        except Exception as e:
            yield json.dumps({"status": f"Agent Pipeline failed: {str(e)}"}) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")
