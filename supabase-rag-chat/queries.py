from db import get_client


def get_items_by_valor_range(min_valor, max_valor, limit=10):
    try:
        client = get_client()
        response = client.table("biia").select("id, item, metadata").execute()
        if response and response.data:
            filtered = [
                i for i in response.data
                if i.get("metadata", {}).get("valor") is not None
                and min_valor <= i["metadata"]["valor"] <= max_valor
            ]
            return sorted(filtered, key=lambda x: x.get("metadata", {}).get("valor", 0), reverse=True)[:limit]
        return []
    except Exception as e:
        print(f"Error in get_items_by_valor_range: {e}")
        return []


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


def get_items_count():
    try:
        client = get_client()
        response = client.table("biia").select("id", count="exact").execute()
        return response.count if response and hasattr(response, 'count') and response.count is not None else 0
    except Exception as e:
        print(f"Error in get_items_count: {e}")
        return 0


def search_by_keyword(keyword, limit=10):
    try:
        client = get_client()
        response = client.table("biia").select("id, item, metadata").ilike("item", f"%{keyword}%").limit(limit).execute()
        return response.data if response and response.data else []
    except Exception as e:
        print(f"Error in search_by_keyword: {e}")
        return []


def search_by_metadata_keyword(field_key, keyword, limit=10):
    try:
        client = get_client()
        response = client.table("biia").select("id, item, metadata").execute()
        if response and response.data:
            filtered = [
                i for i in response.data
                if i.get("metadata", {}).get(field_key) is not None
                and keyword.lower() in str(i.get("metadata", {}).get(field_key, "")).lower()
            ]
            return filtered[:limit]
        return []
    except Exception as e:
        print(f"Error in search_by_metadata_keyword: {e}")
        return []


def search_hybrid(query, limit=20):
    try:
        client = get_client()
        query_lower = query.lower().strip()

        response = client.table("biia").select("id, item, metadata").execute()
        if not response or not response.data:
            return []

        all_items = response.data
        matched_ids = set()
        results = []

        for item in all_items:
            item_text = item.get("item", "") or ""
            metadata = item.get("metadata") or {}
            categoria = metadata.get("categoria", "") or ""

            item_match = query_lower in item_text.lower()
            cat_match = query_lower in categoria.lower()

            if item_match or cat_match:
                if item["id"] not in matched_ids:
                    matched_ids.add(item["id"])
                    results.append({
                        "id": item["id"],
                        "item": item["item"],
                        "metadata": item["metadata"],
                        "similarity": 1.0
                    })
                    if len(results) >= limit:
                        return results

        return results
    except Exception as e:
        print(f"Error in search_hybrid: {e}")
        return []


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


def get_all_items(limit=100, offset=0):
    try:
        client = get_client()
        response = client.table("biia").select("id, item, metadata").range(offset, offset + limit - 1).execute()
        return response.data if response and response.data else []
    except Exception as e:
        print(f"Error in get_all_items: {e}")
        return []


def get_items_by_category(category_pattern, limit=50):
    try:
        client = get_client()
        pattern_lower = category_pattern.lower().strip()
        response = client.table("biia").select("id, item, metadata").execute()
        if not response or not response.data:
            return []
        matched = []
        for row in response.data:
            item_text = (row.get("item") or "").lower()
            if pattern_lower in item_text:
                matched.append(row)
                if len(matched) >= limit:
                    break
        return matched
    except Exception as e:
        print(f"Error in get_items_by_category: {e}")
        return []


def find_best_matching_item(user_query, items):
    user_lower = user_query.lower()
    best_match = None
    best_score = 0
    for item in items:
        item_text = (item.get("item") or "").lower()
        words = user_lower.split()
        matches = sum(1 for w in words if len(w) > 2 and w in item_text)
        if matches > best_score:
            best_score = matches
            best_match = item.get("item")
    return best_match


def detect_intent_and_query(user_message):
    message_lower = user_message.lower()

    if any(phrase in message_lower for phrase in ["quantidade total", "total de inscric", "total inscric", "soma total", "somar todas"]):
        stats = get_aggregated_stats()
        return {"type": "sum", "result": stats.get("sum_valor", 0)}

    if any(word in message_lower for word in ["quantas", "quantos", "quantidade", "contar", "total de", "quantas são", "quantos são", "qual a quantidade", "qual o total"]):
        if any(word in message_lower for word in ["inscric", "registro", "cadastro", "item"]):
            stats = get_aggregated_stats()
            return {"type": "sum", "result": stats.get("sum_valor", 0)}
        count = get_items_count()
        return {"type": "count", "result": count}

    if any(word in message_lower for word in ["somar", "soma", "adição"]):
        stats = get_aggregated_stats()
        return {"type": "sum", "result": stats.get("sum_valor", 0)}

    if any(word in message_lower for word in ["média", "medio", "average"]):
        stats = get_aggregated_stats()
        return {"type": "average", "result": stats.get("avg_valor", 0)}

    if any(word in message_lower for word in ["máximo", "maior", "max", "top", "melhor"]):
        top = get_top_values(limit=5)
        return {"type": "top_values", "result": top}

    if any(word in message_lower for word in ["mínimo", "menor", "min", "pior", "menores"]):
        items = get_client().table("biia").select("id, item, metadata").execute()
        if items and items.data:
            filtered = [i for i in items.data if i.get("metadata", {}).get("valor") is not None]
            sorted_items = sorted(filtered, key=lambda x: x.get("metadata", {}).get("valor", 0))[:5]
            return {"type": "min_values", "result": sorted_items}
        return {"type": "min_values", "result": []}

    if any(word in message_lower for word in ["faixa", "entre", "range", "valor entre"]):
        import re
        numbers = re.findall(r'\d+', message_lower)
        if len(numbers) >= 2:
            min_val, max_val = int(numbers[0]), int(numbers[1])
            items = get_items_by_valor_range(min_val, max_val)
            return {"type": "range", "result": items}

    all_items = get_all_items()
    if all_items:
        matched_item = find_best_matching_item(user_message, all_items)
        if matched_item:
            items = get_items_by_category(matched_item)
            if items:
                return {"type": "category", "result": items, "matched_item": matched_item}

    return None
