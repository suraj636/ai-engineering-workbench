import os
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from backend.pipeline.schema_extractor import SchemaExtractor
from backend.pipeline.executor import SQLExecutor

# Initialize the LLM with the requested gpt-5.1 model
# Note: You must have OPENAI_API_KEY set in your environment
llm = ChatOpenAI(model="gpt-5.1", temperature=0)

# Prompt 1: SQL Generation
sql_prompt = ChatPromptTemplate.from_messages([
    ("system", "You are an expert DuckDB SQL data analyst. Your job is to convert the user's natural language question into a syntactically correct DuckDB SQL query.\n\nHere is the database schema:\n{schema}\n\nReturn ONLY the SQL query, without any markdown formatting like ```sql or explanations."),
    ("user", "{question}")
])

sql_generator = sql_prompt | llm | StrOutputParser()

# Prompt 2: Natural Language Synthesis
synthesis_prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful data assistant. You are provided with a user's original question, the SQL query used to fetch data, and the raw data result.\nYour job is to synthesize this raw data into a natural language answer that directly answers the user's question.\nDo not mention the SQL query in your final answer unless specifically asked."),
    ("user", "Question: {question}\nSQL Query: {sql_query}\nData Result: {data_result}")
])

synthesizer = synthesis_prompt | llm | StrOutputParser()

def run_dbpilot_pipeline(question: str, db_path: str) -> dict:
    """
    Runs the full end-to-end DBPilot Pipeline:
    1. Extract Schema
    2. Generate SQL
    3. Execute SQL
    4. Synthesize Answer
    """
    # 1. Schema Extraction
    schema_ext = SchemaExtractor(db_path)
    schema = schema_ext.extract_schema()
    schema_ext.close()
    
    if schema.startswith("Error") or "No tables found" in schema:
        return {
            "status": "error",
            "message": "Failed to extract schema from database.",
            "details": schema
        }

    # 2. SQL Generation
    try:
        generated_sql = sql_generator.invoke({
            "schema": schema,
            "question": question
        })
        # Clean up any potential markdown if the model hallucinated it
        generated_sql = generated_sql.replace("```sql", "").replace("```", "").strip()
    except Exception as e:
         return {
            "status": "error",
            "message": "Failed during SQL Generation.",
            "details": str(e)
        }

    # 3. SQL Execution
    executor = SQLExecutor(db_path)
    exec_result = executor.execute_query(generated_sql)
    executor.close()

    if not exec_result["success"]:
        return {
            "status": "error",
            "message": "Failed to execute generated SQL.",
            "sql": generated_sql,
            "details": exec_result["error"]
        }

    # 4. Natural Language Synthesis
    data_str = f"Columns: {exec_result['columns']}\nRows: {exec_result['data']}"
    try:
        final_answer = synthesizer.invoke({
            "question": question,
            "sql_query": generated_sql,
            "data_result": data_str
        })
    except Exception as e:
         return {
            "status": "error",
            "message": "Failed during Natural Language Synthesis.",
            "details": str(e)
        }

    # Return the complete payload
    return {
        "status": "success",
        "question": question,
        "generated_sql": generated_sql,
        "raw_data": {
            "columns": exec_result["columns"],
            "data": exec_result["data"]
        },
        "answer": final_answer
    }
