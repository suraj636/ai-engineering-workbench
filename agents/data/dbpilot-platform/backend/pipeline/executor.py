import duckdb
from typing import Dict, Any, List

class SQLExecutor:
    def __init__(self, db_path: str):
        """Initialize with a DuckDB path"""
        self.db_path = db_path
        self.conn = duckdb.connect(self.db_path)

    def execute_query(self, sql_query: str) -> Dict[str, Any]:
        """
        Executes a SQL query against the database and returns the result.
        Returns a dictionary with 'columns', 'data', and 'error' (if any).
        """
        try:
            # We use a cursor to safely execute
            cursor = self.conn.cursor()
            cursor.execute(sql_query)
            
            # Fetch all rows
            rows = cursor.fetchall()
            
            # Get column names
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            
            return {
                "success": True,
                "columns": columns,
                "data": rows,
                "error": None
            }
        except Exception as e:
            return {
                "success": False,
                "columns": [],
                "data": [],
                "error": str(e)
            }
        finally:
            if 'cursor' in locals():
                cursor.close()

    def close(self):
        self.conn.close()
