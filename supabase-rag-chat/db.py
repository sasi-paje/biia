from supabase import create_client
import os

_supabase = None


def get_client():
    global _supabase
    if _supabase is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        _supabase = create_client(url, key)
    return _supabase


def get_table_schema(table_name):
    try:
        client = get_client()
        response = client.table(table_name).select("*").limit(1).execute()
        return response.data if response and response.data else []
    except Exception as e:
        print(f"Error in get_table_schema: {e}")
        return []