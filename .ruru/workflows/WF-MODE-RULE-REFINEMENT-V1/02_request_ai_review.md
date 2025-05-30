+++
# --- Step Metadata ---
step_id = "WF-MODE-RULE-REFINEMENT-V1-02-REQUEST_AI_REVIEW" # (String, Required) Unique ID for this step.
title = "Step 02: Request AI Review" # (String, Required) Title of this specific step.
description = """
(String, Required) Delegates to the Vertex AI MCP server to review the combined rule content 
based on the defined objective and save the analysis to a file.
"""

# --- Flow Control ---
depends_on = ["WF-MODE-RULE-REFINEMENT-V1-01-READ_FILES"] # (Array of Strings, Required) step_ids this step needs completed first.
next_step = "03_analyze_review_plan_updates.md" # (String, Optional) Filename of the next step on successful completion.
error_step = "EE_handle_error.md" # (String, Optional) Filename to jump to if this step fails.

# --- Execution ---
delegate_to = "vertex-ai-mcp-server" # (String, Optional) Mode responsible for executing the core logic of this step.

# --- Interface ---
inputs = [ # (Array of Strings, Optional) Data/artifacts needed.
    "Output from step WF-MODE-RULE-REFINEMENT-V1-00-START: review_objective",
    "Output from step WF-MODE-RULE-REFINEMENT-V1-01-READ_FILES: combined_file_content",
    "Output from step WF-MODE-RULE-REFINEMENT-V1-00-START: source_material_path (Optional)",
]
outputs = [ # (Array of Strings, Optional) Data/artifacts produced by this step.
    "review_output_path: Path to the saved AI review document.",
]

# --- Housekeeping ---
last_updated = "{{DATE}}" # (String, Required) Date of last modification. Use placeholder.
template_schema_doc = ".ruru/templates/toml-md/25_workflow_step_standard.md" # (String, Required) Link to this template definition.
+++

# Step 02: Request AI Review

## Actions

1.  **Delegate to Vertex AI:** The coordinator uses `use_mcp_tool` targeting `vertex-ai-mcp-server`.
2.  **Specify Tool:** Use `save_answer_query_websearch` (as per workflow README and Rule `RULE-VERTEX-MCP-USAGE-V1` preference for saving).
3.  **Provide Input:** The `arguments` JSON object contains:
    *   `query`: A detailed prompt constructed by the coordinator, including the `review_objective`, the `combined_file_content`, instructions to analyze/suggest improvements, and potentially referencing `source_material_path`.
    *   `output_path`: A path generated by the coordinator following the convention `.ruru/docs/vertex/answers-web/[YYYYMMDDHHMMSS]-[sanitized_query].md`.

    ```json
    {
      "query": "Review the following rule content:\n---\n[combined_file_content]\n---\nObjective: [review_objective]. Please check for accuracy against [source_material_path, if provided], suggest improvements for clarity and completeness, and identify any potential issues or ambiguities. Format your response clearly.",
      "output_path": ".ruru/docs/vertex/answers-web/YYYYMMDDHHMMSS-rule_review.md" 
    }
    ```
    *(Note: Coordinator must replace placeholders like `[combined_file_content]`, `[review_objective]`, `[source_material_path]`, and the timestamp/filename in `output_path` before delegation).*

## Acceptance Criteria

*   The `vertex-ai-mcp-server` successfully processes the query.
*   The AI review is saved to the specified `output_path`.
*   The `review_output_path` is returned to the coordinator.

## Error Handling

*   If the `vertex-ai-mcp-server` fails to process the query or save the file, the error is reported, and the workflow proceeds to `{{error_step}}`.