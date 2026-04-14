from db import get_client

_schema_cache = None


def get_all_tables_info():
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache
    
    try:
        client = get_client()
        tables_query = """
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """
        columns_query = """
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position;
        """
        
        tables_res = client.rpc("exec", {"query": tables_query}).execute()
        col_res = client.rpc("exec", {"query": columns_query}).execute()
        
        tables_info = {}
        if tables_res.data:
            for row in tables_res.data:
                tables_info[row["table_name"]] = {
                    "columns": [],
                    "data": []
                }
        
        if col_res.data:
            for row in col_res.data:
                tbl = row.get("table_name")
                if tbl in tables_info:
                    tables_info[tbl]["columns"].append({
                        "name": row.get("column_name"),
                        "type": row.get("data_type"),
                        "nullable": row.get("is_nullable") == "YES"
                    })
        
        for tbl in tables_info:
            try:
                data_res = client.table(tbl).select("*").limit(5).execute()
                tables_info[tbl]["data"] = data_res.data if data_res.data else []
            except:
                pass
        
        _schema_cache = tables_info
        return tables_info
    except Exception as e:
        print(f"Error fetching tables info: {e}")
        return {"biia": {"columns": [{"name": "id"}, {"name": "item"}, {"name": "metadata"}], "data": []}}


def find_best_matching_table(query, tables_info):
    query_lower = query.lower()
    query_words = [w for w in query_lower.split() if len(w) > 2]
    
    best_table = None
    best_score = 0
    
    for table_name, info in tables_info.items():
        score = 0
        table_words = table_name.lower().split()
        
        for qw in query_words:
            for tw in table_words:
                if qw in tw or tw in qw:
                    score += 2
        
        for col in info.get("columns", []):
            col_name = col.get("name", "").lower()
            for qw in query_words:
                if qw in col_name or col_name in qw:
                    score += 1
                
        if score > best_score:
            best_score = score
            best_table = table_name
    
    return best_table, best_score


def detect_table_and_query(user_message):
    tables_info = get_all_tables_info()
    if not tables_info:
        return None
    
    table_name, score = find_best_matching_table(user_message, tables_info)
    
    if not table_name:
        return None
    
    message_lower = user_message.lower()
    
    if any(word in message_lower for word in ["quantas", "quantos", "quantidade", "contar", "total", "soma", "somar"]):
        numeric_cols = []
        for col in tables_info[table_name].get("columns", []):
            col_type = col.get("type", "").lower()
            if any(t in col_type for t in ["integer", "bigint", "numeric", "decimal", "double"]):
                numeric_cols.append(col["name"])
        
        if numeric_cols:
            col = numeric_cols[0]
            return {
                "type": "aggregate",
                "table": table_name,
                "column": col,
                "operation": "sum",
                "query": f"SELECT SUM({col}) as result FROM {table_name}"
            }
        
        return {
            "type": "count",
            "table": table_name,
            "query": f"SELECT COUNT(*) as result FROM {table_name}"
        }
    
    if any(word in message_lower for word in ["média", "media", "average"]):
        numeric_cols = []
        for col in tables_info[table_name].get("columns", []):
            col_type = col.get("type", "").lower()
            if any(t in col_type for t in ["integer", "bigint", "numeric", "decimal", "double"]):
                numeric_cols.append(col["name"])
        
        if numeric_cols:
            return {
                "type": "aggregate",
                "table": table_name,
                "column": numeric_cols[0],
                "operation": "avg",
                "query": f"SELECT AVG({numeric_cols[0]}) as result FROM {table_name}"
            }
    
    return {
        "type": "select",
        "table": table_name,
        "query": f"SELECT * FROM {table_name} LIMIT 10"
    }


def search_value_in_all_tables(search_value):
    import re
    tables_info = get_all_tables_info()
    if not tables_info:
        return None
    
    clean_value = re.sub(r'[^\w]', '', search_value.lower())
    
    for table_name, info in tables_info.items():
        for col in info.get("columns", []):
            col_name = col.get("name", "").lower()
            if any(skip in col_name for skip in ["id", "created_at", "updated_at", "vetorizada"]):
                continue
            
            col_type = col.get("type", "").lower()
            if "text" in col_type or "varchar" in col_type or "char" in col_type or "integer" in col_type or "bigint" in col_type:
                try:
                    query = f"SELECT * FROM {table_name} WHERE CAST({col['name']} AS TEXT) ILIKE '%{clean_value}%' LIMIT 5"
                    result = execute_custom_query(query)
                    if result and len(result) > 0:
                        return {
                            "type": "search_by_value",
                            "table": table_name,
                            "column": col["name"],
                            "value": search_value,
                            "result": result
                        }
                except Exception as e:
                    print(f"Error searching {table_name}.{col['name']}: {e}")
                    continue
    
    return None


def detect_search_intent(user_message):
    import re
    message_lower = user_message.lower()
    
    cpf_pattern = r'\d{3}\.\d{3}\.\d{3}-\d{2}'
    cpf_match = re.search(cpf_pattern, user_message)
    if cpf_match:
        return {"type": "cpf", "value": cpf_match.group()}
    
    cnpj_pattern = r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}'
    cnpj_match = re.search(cnpj_pattern, user_message)
    if cnpj_match:
        return {"type": "cnpj", "value": cnpj_match.group()}
    
    if any(phrase in message_lower for phrase in ["quem é", "quem são", "titular", "dono", "pessoa", "nome completo"]):
        return {"type": "person_search", "value": user_message}
    
    return None


def execute_custom_query(query):
    try:
        client = get_client()
        result = client.rpc("exec", {"query": query}).execute()
        return result.data if result.data else []
    except Exception as e:
        print(f"Error executing query: {e}")
        return None


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

    search_intent = detect_search_intent(user_message)
    if search_intent:
        if search_intent["type"] in ["cpf", "cnpj"]:
            result = search_value_in_all_tables(search_intent["value"])
            if result:
                return result
        elif search_intent["type"] == "person_search":
            result = search_value_in_all_tables(search_intent["value"])
            if result:
                return result

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

    table_query = detect_table_and_query(user_message)
    if table_query:
        return table_query

    return None
