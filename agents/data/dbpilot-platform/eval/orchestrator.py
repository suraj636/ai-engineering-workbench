import httpx
import asyncio
import pandas as pd
from eval.llm_judge import evaluate_response

API_URL = "http://localhost:8000/api/query"

async def evaluate_question(question: str, expected: str, db_path: str):
    """
    Submits a question to the API, gets the response, and scores it.
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(API_URL, json={
                "question": question,
                "db_path": db_path
            }, timeout=30.0)
            
            if response.status_code != 200:
                return {"question": question, "error": f"API Error {response.status_code}"}
                
            data = response.json()
            actual_answer = data.get("answer", "")
            sql_query = data.get("generated_sql", "")
            
            # 1. Deterministic Exact Match (very simple loose string match for prototype)
            exact_match = expected.lower().strip() in actual_answer.lower()
            
            # 2. LLM-as-Judge
            judge_score = evaluate_response(question, expected, actual_answer, sql_query)
            
            return {
                "question": question,
                "expected": expected,
                "actual": actual_answer,
                "sql": sql_query,
                "exact_match": exact_match,
                "judge_score": judge_score
            }
        except Exception as e:
            return {"question": question, "error": str(e)}

async def run_domain_evaluation(domain_csv_path: str, db_path: str):
    """Runs the full evaluation for a specific domain's Q&A CSV."""
    df = pd.read_csv(domain_csv_path)
    
    results = []
    for _, row in df.iterrows():
        question = row['natural_language_question']
        expected = row['expected_answer']
        
        print(f"Evaluating: {question}")
        res = await evaluate_question(question, expected, db_path)
        results.append(res)
        
    results_df = pd.DataFrame(results)
    output_path = domain_csv_path.replace(".csv", "_results.csv")
    results_df.to_csv(output_path, index=False)
    print(f"Evaluation complete. Saved to {output_path}")

if __name__ == "__main__":
    # Example usage
    # asyncio.run(run_domain_evaluation("datasets/domain_1_device_mgmt/qa_pairs.csv", "datasets/domain_1_device_mgmt/db.duckdb"))
    pass
