"""Gemini function declarations for MongoDBAgent tools."""
from google.genai import types


def build_tools() -> types.Tool:
    obj = types.Schema(type=types.Type.OBJECT)
    return types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="query_collection",
            description=(
                "Read documents from a MongoDB collection. Use for questions, lookups, "
                "listings, and counts. If 0 results are returned, retry with a broader "
                "filter before reporting nothing was found."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "collection": types.Schema(type=types.Type.STRING),
                    "filter":     types.Schema(type=types.Type.OBJECT,
                                               description="MongoDB filter — use {} for all docs"),
                    "limit":      types.Schema(type=types.Type.INTEGER,
                                               description="Max docs to return (0 = no limit)"),
                    "aggregation_pipeline": types.Schema(
                        type=types.Type.ARRAY, items=obj,
                        description="Pipeline stages for grouping, sorting, computed fields",
                    ),
                },
                required=["collection"],
            ),
        ),
        types.FunctionDeclaration(
            name="confirm_action",
            description=(
                "REQUIRED before any insert_document, update_document, or delete_document call. "
                "Presents the full action details to the user for confirmation. "
                "Do NOT call any write tool in the same turn — wait for the user's reply."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "summary":     types.Schema(type=types.Type.STRING,
                                                description="First-person confirmation question spoken to the user. "
                                                            "Example: 'I'd like to book the Main Gym for Oliver on Monday at 5 PM. Shall I go ahead?'"),
                    "action_type": types.Schema(type=types.Type.STRING,
                                                description="insert | update | delete"),
                    "collection":  types.Schema(type=types.Type.STRING),
                    "document":    types.Schema(type=types.Type.OBJECT,
                                                description="Document to insert (action_type=insert)"),
                    "filter":      types.Schema(type=types.Type.OBJECT,
                                                description="Filter to find the record (action_type=update|delete)"),
                    "updates":     types.Schema(type=types.Type.OBJECT,
                                                description="Fields to change (action_type=update)"),
                },
                required=["summary", "action_type", "collection"],
            ),
        ),
        types.FunctionDeclaration(
            name="insert_document",
            description=(
                "Insert a new document. Call ONLY after confirm_action was accepted by the user. "
                "NEVER include system fields: _id, status, created_at, source, price/fee/amount. "
                "For same-day group/family bookings, include num_spots=N (integer) to reserve N seats at once — "
                "the system creates N booking records automatically. Do NOT call this tool N times for same-day group bookings."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "collection": types.Schema(type=types.Type.STRING),
                    "document":   types.Schema(type=types.Type.OBJECT,
                                               description="User-facing fields only"),
                },
                required=["collection", "document"],
            ),
        ),
        types.FunctionDeclaration(
            name="update_document",
            description=(
                "Update an existing document. Call ONLY after confirm_action was accepted. "
                "Uses $set — only the specified fields are changed."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "collection": types.Schema(type=types.Type.STRING),
                    "filter":     types.Schema(type=types.Type.OBJECT,
                                               description="Filter to identify the document to update"),
                    "updates":    types.Schema(type=types.Type.OBJECT,
                                               description="Fields and new values to set"),
                },
                required=["collection", "filter", "updates"],
            ),
        ),
        types.FunctionDeclaration(
            name="delete_document",
            description=(
                "Delete a document. Call ONLY after confirm_action was accepted. "
                "Requires a non-empty filter — will never delete all documents."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "collection": types.Schema(type=types.Type.STRING),
                    "filter":     types.Schema(type=types.Type.OBJECT,
                                               description="Filter to identify the document to delete"),
                },
                required=["collection", "filter"],
            ),
        ),
        types.FunctionDeclaration(
            name="ask_user",
            description=(
                "Ask the user for missing information needed to complete the task "
                "(missing field, ambiguous name). Do NOT use this for confirmation — use confirm_action instead."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "question":         types.Schema(type=types.Type.STRING),
                    "collection":       types.Schema(type=types.Type.STRING,
                                                     description="Target collection if an insert is in progress"),
                    "partial_document": types.Schema(type=types.Type.OBJECT,
                                                     description="Fields collected so far"),
                },
                required=["question"],
            ),
        ),
    ])
