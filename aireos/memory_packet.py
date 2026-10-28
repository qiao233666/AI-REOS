"""给模型的有界检索片段；原文、字节和正式版本仍保存在权威记录。"""
from .errors import FrameworkError
from .storage import canonical


def body_text(obj):
    data = obj["data"]
    for field in ("text", "search_text", "summary", "reason", "notes", "purpose"):
        if isinstance(data.get(field), str):
            return data[field], field
    if isinstance(data.get("manifest"), dict):
        manifest = data["manifest"]
        return "\n".join(str(manifest.get(key, "")) for key in ("purpose", "inputs", "outputs", "limitations", "verification")), "manifest"
    if data.get("method"):
        return data["method"] + "\n" + "\n".join(data.get("checks", [])), "method+checks"
    return canonical(data), "data"


def window(text, query, maximum):
    if len(text) <= maximum:
        return text, 0, len(text)
    from .memory_index import phrases
    tokens, short = phrases(query)
    lowered = text.lower()
    candidates = [query.lower(), *sorted(tokens + short, key=len, reverse=True)]
    position = next((lowered.find(token) for token in candidates if token and lowered.find(token) >= 0), 0)
    start = min(max(0, position - maximum // 4), max(0, len(text) - maximum))
    end = min(len(text), start + maximum)
    return text[start:end], start, end


def present(tx, current, historical, query, max_chars, view):
    if view not in {"brief", "full"}:
        raise FrameworkError("显示模式须为brief或full；原始对象可按id/rev用get取得")
    from .project import Project
    remaining, truncated = max_chars, False
    statuses, visible = {}, [[], []]
    status_cache = {}
    catalog_entries = None
    for source, destination in zip((current, historical), visible):
        for obj in source:
            data = obj["data"]
            status = None
            if obj["kind"] == "item":
                if obj["id"] not in status_cache:
                    status_cache[obj["id"]] = Project._status(tx, obj["id"])
                status = status_cache[obj["id"]]
            if obj['kind']=='memory_note':
                from .memory_catalog import MemoryCatalog, snapshot
                if catalog_entries is None:
                    _,catalog_entries=snapshot(tx.store)
                status=MemoryCatalog(tx.store).note_state(obj,catalog_entries)
            text, field = body_text(obj)
            title = str(data.get("title", data.get("manifest", {}).get("name", obj["kind"])))
            card = {"id": obj["id"], "rev": obj["rev"], "kind": obj["kind"],
                "title": title[:120],
                "created": obj["created"], "body": "", "source": {"field": field, "start": 0, "end": 0}, "partial": False}
            fields_omitted = ["title"] if len(title) > 120 else []
            for key in ("scope", "classification", "origin", "verdict", "action", "status", "authority"):
                if key in data:
                    card[key] = data[key][:240] if isinstance(data[key], str) else data[key]
                    if card[key] != data[key]:
                        fields_omitted.append(key)
            if fields_omitted:
                card["fields_omitted"] = fields_omitted
            if "search_coverage" in data:
                card["search_coverage"] = data["search_coverage"]
            from .memory_index import relations
            links = relations(data)
            if links:
                card["links"] = [{"id": identifier, "relation": relation} for identifier, relation in links[:12]]
                card["links_omitted"] = len(links) > 12
            state_cost = len(canonical({obj["id"]: status})) if status else 0
            allowance = remaining - len(canonical(card)) - state_cost - 32
            if view == "full":
                fragment, start, end = text, 0, len(text)
            else:
                if allowance < 40:
                    truncated = True
                    continue
                fragment, start, end = window(text, query, min(600, allowance))
            card.update(body=fragment, source=dict(field=field, start=start, end=end), partial=bool(fields_omitted) or start > 0 or end < len(text))
            cost = len(canonical(card)) + state_cost
            if cost > remaining:
                truncated = True
                continue
            remaining -= cost
            destination.append(card)
            if status:
                statuses[obj["id"]] = status
            truncated = truncated or card["partial"] or card.get("links_omitted", False)
    return dict(current=visible[0], historical=visible[1], reuse_status=statuses, view=view,
        display_truncated=truncated, display_chars=max_chars - remaining)
