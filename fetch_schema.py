import os
import sys
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()


def fetch_schema():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    client = create_client(url, key)

    print("🔄 Fetching schema from Supabase...\n")

    schema_info = {
        "tables": {},
        "functions": {},
        "indexes": {},
    }

    try:
        result = client.rpc("get_table_columns", {}).execute()
    except:
        result = None

    columns_query = """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position;
    """

    tables_query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        AND table_type = 'BASE TABLE';
    """

    indexes_query = """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public';
    """

    try:
        tables_res = client.rpc("exec", {"query": tables_query}).execute()
        if tables_res.data:
            schema_info["tables_list"] = [r["table_name"] for r in tables_res.data]
    except Exception as e:
        print(f"⚠️  Could not fetch tables list: {e}")
        schema_info["tables_list"] = ["biia"]

    try:
        col_res = client.rpc("exec", {"query": columns_query}).execute()
        if col_res.data:
            for row in col_res.data:
                tbl = row.get("table_name", "unknown")
                if tbl not in schema_info["tables"]:
                    schema_info["tables"][tbl] = {"columns": []}
                schema_info["tables"][tbl]["columns"].append({
                    "name": row.get("column_name"),
                    "type": row.get("data_type"),
                    "nullable": row.get("is_nullable") == "YES",
                    "default": row.get("column_default"),
                })
    except Exception as e:
        print(f"⚠️  Could not fetch columns: {e}")
        schema_info["tables"]["biia"] = {
            "columns": [
                {"name": "id", "type": "bigint", "nullable": False, "default": "bigserial"},
                {"name": "item", "type": "text", "nullable": False, "default": None},
                {"name": "metadata", "type": "jsonb", "nullable": True, "default": "{}"},
                {"name": "vetorizada", "type": "vector(384)", "nullable": False, "default": None},
                {"name": "created_at", "type": "timestamptz", "nullable": True, "default": "now()"},
            ]
        }

    try:
        idx_res = client.rpc("exec", {"query": indexes_query}).execute()
        if idx_res.data:
            schema_info["indexes"] = {r["indexname"]: r["indexdef"] for r in idx_res.data}
    except Exception as e:
        print(f"⚠️  Could not fetch indexes: {e}")

    try:
        sample = client.table("biia").select("*").limit(3).execute()
        schema_info["sample_data"] = sample.data
    except Exception as e:
        print(f"⚠️  Could not fetch sample data: {e}")

    output = []
    output.append("=" * 60)
    output.append("BIIA DATABASE SCHEMA")
    output.append("=" * 60)

    output.append("\n📋 TABLES:")
    for tbl, info in schema_info["tables"].items():
        output.append(f"\n  Table: {tbl}")
        for col in info["columns"]:
            null_str = "NULL" if col["nullable"] else "NOT NULL"
            default_str = f" DEFAULT {col['default']}" if col["default"] else ""
            output.append(f"    - {col['name']} : {col['type']} {null_str}{default_str}")

    output.append("\n📊 SAMPLE DATA (first 3 rows):")
    if schema_info.get("sample_data"):
        for row in schema_info["sample_data"]:
            output.append(f"  {row}")

    output.append("\n🔍 INDEXES:")
    for idx_name, idx_def in schema_info.get("indexes", {}).items():
        output.append(f"  {idx_name}: {idx_def}")

    output.append("\n" + "=" * 60)
    output.append("END OF SCHEMA")
    output.append("=" * 60)

    schema_text = "\n".join(output)

    with open("schema.txt", "w") as f:
        f.write(schema_text)

    print(schema_text)
    print("\n✅ Schema saved to schema.txt")

    return schema_info


if __name__ == "__main__":
    fetch_schema()
