from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

# Using the requested model
judge_llm = ChatOpenAI(model="gpt-5.1", temperature=0)

class JudgeScore(BaseModel):
    factual_correctness: int = Field(description="Score 1-5")
    completeness: int = Field(description="Score 1-5")
    format_adherence: int = Field(description="Score 1-5")
    sql_plausibility: int = Field(description="Score 1-5")
    rationale: str = Field(description="Explanation of the scores")

judge_prompt = ChatPromptTemplate.from_messages([
    ("system", """You are an expert AI judge evaluating a Natural Language Query system.
You will be provided with:
1. The User's Question
2. The Expected Answer (Ground Truth)
3. The Platform's Actual Answer
4. The Platform's Generated SQL

Evaluate the platform's response on 4 dimensions on a scale of 1-5:
- Factual Correctness (Does it match the ground truth substance?)
- Completeness (Are all required values present?)
- Format Adherence (Is it in the expected format?)
- SQL Plausibility (Is the SQL logical for the question?)

Return your evaluation as a JSON object with keys:
factual_correctness, completeness, format_adherence, sql_plausibility, rationale.
"""),
    ("user", "Question: {question}\nExpected: {expected}\nActual: {actual}\nSQL: {sql}")
])

parser = JsonOutputParser(pydantic_object=JudgeScore)

judge_chain = judge_prompt | judge_llm | parser

def evaluate_response(question: str, expected: str, actual: str, sql: str) -> dict:
    """Runs the LLM-as-Judge to score the response."""
    try:
        return judge_chain.invoke({
            "question": question,
            "expected": expected,
            "actual": actual,
            "sql": sql
        })
    except Exception as e:
        return {
            "factual_correctness": 1,
            "completeness": 1,
            "format_adherence": 1,
            "sql_plausibility": 1,
            "rationale": f"Judge failed to evaluate: {str(e)}"
        }
