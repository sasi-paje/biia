from db import get_client


def get_items_count():
    try:
        client = get_client()
        response = client.table("biia").select("id", count="exact").execute()
        return response.count if response and hasattr(response, 'count') and response.count is not None else 0
    except Exception as e:
        print(f"Error in get_items_count: {e}")
        return 0


def get_aggregated_stats():
    try:
        client = get_client()
        response = client.table("biia").select("id, metadata").execute()
        if response and response.data:
            valores = [i.get("metadata", {}).get("valor") for i in response.data if i.get("metadata", {}).get("valor") is not None]
            if valores:
                return {
                    "total_items": len(valores),
                    "avg_valor": sum(valores) / len(valores),
                    "min_valor": min(valores),
                    "max_valor": max(valores),
                    "sum_valor": sum(valores),
                }
        return {"total_items": 0, "avg_valor": 0, "min_valor": 0, "max_valor": 0, "sum_valor": 0}
    except Exception as e:
        print(f"Error in get_aggregated_stats: {e}")
        return {"total_items": 0, "avg_valor": 0, "min_valor": 0, "max_valor": 0, "sum_valor": 0}


def get_top_values(limit=10):
    try:
        client = get_client()
        response = client.table("biia").select("id, item, metadata").execute()
        if response and response.data:
            filtered = [i for i in response.data if i.get("metadata", {}).get("valor") is not None]
            return sorted(filtered, key=lambda x: x.get("metadata", {}).get("valor", 0), reverse=True)[:limit]
        return []
    except Exception as e:
        print(f"Error in get_top_values: {e}")
        return []