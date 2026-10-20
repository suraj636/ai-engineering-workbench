import duckdb
from typing import Dict, List, Any

class SchemaExtractor:
    def __init__(self, db_path: str):
        """Initialize with a DuckDB path (or memory for CSV testing)"""
        self.db_path = db_path
        self.conn = duckdb.connect(self.db_path)

    def extract_schema(self) -> str:
        """
        Extracts the schema from the DuckDB instance and returns it as a formatted string.
        """
        # Query DuckDB's internal schema table for all tables and columns
        query = """
        SELECT table_name, column_name, data_type 
        FROM information_schema.columns 
        WHERE table_schema = 'main'
        ORDER BY table_name, ordinal_position;
        """
        try:
            results = self.conn.execute(query).fetchall()
        except Exception as e:
            return f"Error extracting schema: {str(e)}"

        if not results:
            return "No tables found in the database."

        schema_dict: Dict[str, List[Dict[str, str]]] = {}
        for row in results:
            table_name = row[0]
            col_name = row[1]
            col_type = row[2]
            if table_name not in schema_dict:
                schema_dict[table_name] = []
            schema_dict[table_name].append({"name": col_name, "type": col_type})

        # Format into a readable string for the LLM
        schema_text = "Database Schema:\n"
        for table, cols in schema_dict.items():
            schema_text += f"- Table: {table}\n"
            for col in cols:
                schema_text += f"    - {col['name']} ({col['type']})\n"
            schema_text += "\n"

        return schema_text

    def close(self):
        self.conn.close()

# Quick test if run directly
if __name__ == "__main__":
    extractor = SchemaExtractor(":memory:")
    extractor.conn.execute("CREATE TABLE users (id INTEGER, name VARCHAR, email VARCHAR);")
    extractor.conn.execute("CREATE TABLE events (id INTEGER, user_id INTEGER, event_type VARCHAR);")
    print(extractor.extract_schema())
    extractor.close()
