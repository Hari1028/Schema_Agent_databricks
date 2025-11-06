import logging
import os
import glob
import pandas as pd
import json
import sqlalchemy
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# --- Our Local Module Imports ---
# These imports fix all the "yellow line" warnings
import tools
import databricks_tools
import prompts
from llm_service import get_llm_streaming_response, get_llm_json_response
from prompts import SYSTEM_PROMPT_INSIGHT

SCHEMA_HISTORY_DIR = "schema_history"
NUM_HISTORICAL_SCHEMAS_TO_LOAD = 3

# =========================================================================
# (NEW) AGENT TOOL 1: GET RECOMMENDATIONS FOR A SHEET
# =========================================================================
def get_recommendations_for_sheet(file_path: str, sheet_name: str, all_db_schemas: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Analyzes a *single sheet* from a file and compares its schema against
    all Databricks tables to provide intelligent recommendations.
    """
    logging.info(f"--- Starting Table Recommendation for: {file_path} (Sheet: {sheet_name}) ---")
    
    # NOTE: All engine creation code has been removed here, as it's passed in main.py
    
    try:
        # --- Step 2: Read *Specific* Sheet and Extract Schema ---
        df = None
        # Use sheet_name unless it's the CSV placeholder
        read_sheet_name = sheet_name if sheet_name != "csv_data" else None
        
        if file_path.endswith(('.xls', '.xlsx')):
            df = pd.read_excel(file_path, sheet_name=read_sheet_name)
            logging.info(f"Reading sheet ('{read_sheet_name}') from Excel file.")
        elif file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
            logging.info("Reading CSV file.")
        else:
            raise ValueError(f"Unsupported file type: {file_path}. Only .csv, .xls, and .xlsx are supported.")

        # We call the function from tools.py
        file_schema = tools.extract_schema_from_df(df, file_path, read_sheet_name)
        if "error" in file_schema or not file_schema.get("columns"):
            raise ValueError("Schema extraction failed for the file.")
        
        file_schema_cols = list(file_schema.get("columns", {}).keys())

        
        # --- Step 4: Call LLM for Analysis ---
        logging.info("Calling LLM for smart table matching analysis...")
        # We call the function from prompts.py
        prompt = prompts.get_table_matching_prompt(
            file_schema=file_schema_cols,
            all_db_schemas=all_db_schemas
        )
        
        # We call the function from llm_service.py
        # We call the new, safe JSON function from llm_service.py
        recommendations_json = get_llm_json_response(SYSTEM_PROMPT_INSIGHT, prompt)
    
        if "error" in recommendations_json:
            logging.error(f"Failed to get LLM recommendations: {recommendations_json['error']}")
            # Raise an error to be caught by the outer try/except
            raise ValueError(f"Failed to get LLM recommendations: {recommendations_json.get('raw_response', 'No response')}")
    
        logging.info("Successfully received and parsed table recommendations from LLM.")
    
        recommendations_json["source_file_schema"] = file_schema_cols
        return recommendations_json

    except Exception as e:
        logging.error(f"Error in get_recommendations_for_sheet: {e}", exc_info=True)
        return {"error": str(e)}
    
    


# =========================================================================
# (INTERNAL) Core Validation Logic for one sheet
# =========================================================================
def _run_validation_for_sheet_internal(
    df: pd.DataFrame,
    file_path: str,
    sheet_name: Optional[str],
    engine: sqlalchemy.engine.Engine, 
    target_table_name: str # CHANGED: No longer optional
) -> (Dict[str, Any], Dict[str, Any]):
    """
    Runs the validation process for a *single DataFrame* against a *single table*.
    This is the internal logic.
    """
    sheet_report = {}
    schema_analysis_json = {}
    
    # This function *assumes* target_table_name is provided.
    if target_table_name is None:
         raise ValueError("Internal Error: _run_validation_for_sheet_internal called without a target_table_name.")

    try:
        sheet_display_name = sheet_name if sheet_name is not None else "CSV Data"
        logging.info(f"---  Starting Validation for Sheet: '{sheet_display_name}' ---")

        # --- Step 1 (Sheet): Extract Schema ---
        # Call from tools.py
        file_schema = tools.extract_schema_from_df(df, file_path, sheet_name)
        if "error" in file_schema or not file_schema.get("columns"):
            raise ValueError(f"Schema extraction failed for sheet '{sheet_display_name}'")

        # --- Step 2 (Sheet): LLM Schema Analysis ---
        logging.info(f"--- [Sheet '{sheet_display_name}'] Step 2: LLM Schema Analysis ---")
        
        # Call from databricks_tools.py
        db_schema = databricks_tools.get_db_schema(engine, target_table_name)
        if db_schema is None:
            raise ValueError(f"Database table '{target_table_name}' does not exist.")

        # Call from tools.py
        raw_comparison = tools.compare_schemas(file_schema, db_schema)
        # Call from prompts.py
        schema_prompt = prompts.get_schema_analysis_prompt(
            db_schema=db_schema, file_schema=file_schema, raw_comparison=raw_comparison,
            target_table_name=target_table_name, source_file_name=os.path.basename(file_path)
        )
        
        # Call from llm_service.py
        # Call from llm_service.py using the new, safe function
        schema_analysis_json = get_llm_json_response(SYSTEM_PROMPT_INSIGHT, schema_prompt)
    
        if "error" in schema_analysis_json:
            logging.error(f"Failed to parse JSON from schema analysis: {schema_analysis_json['error']}\nRaw response: {schema_analysis_json.get('raw_response')}")
            raise ValueError("LLM did not return valid JSON for schema analysis.")
            
        logging.info(f"LLM Schema Analysis: Complete")

        # --- Step 3 (Sheet): Deep Validation ---
        logging.info(f"--- [Sheet '{sheet_display_name}'] Step 3: Deep Validation ---")
        naming_mismatches = schema_analysis_json.get("naming_mismatches", {})
        mapped_df = df.rename(columns=naming_mismatches)
        
        # Call from tools.py
        type_violations = tools.validate_data_types(mapped_df, db_schema)
        # Call from tools.py
        dq_violations = tools.run_data_quality_checks(mapped_df, db_schema, engine, target_table_name)
        logging.info(f"Deep validation: Complete")

        # --- Step 4 (Sheet): Infer Dynamic Rules ---
        logging.info(f"--- [Sheet '{sheet_display_name}'] Step 4.5: Inferring Dynamic Rules ---")
        dynamic_rules = []
        try:
            # Call from prompts.py
            dynamic_rules_prompt = prompts.get_dynamic_rules_prompt(file_schema)
            # Call from llm_service.py using the new, safe function
            dynamic_rules = get_llm_json_response(SYSTEM_PROMPT_INSIGHT, dynamic_rules_prompt)
        
            if "error" in dynamic_rules:
                logging.warning(f"Could not generate dynamic rules: {dynamic_rules['error']}")
                dynamic_rules = [{"error": "Failed to generate dynamic rules"}]
            else:
                logging.info(f"LLM Dynamic Rules: Complete")
        except Exception as e:
            # This is a catch-all in case the function call itself fails
            logging.warning(f"Could not generate dynamic rules: {e}")
            dynamic_rules = [{"error": "Failed to generate dynamic rules"}]

        # --- Step 5: Assembling Violation Summary ---
        logging.info(f"--- [Sheet '{sheet_display_name}'] Step 5: Assembling Violation Summary ---")
        def _create_violation_summary(types, dq):
            summary = {
                "type_mismatch_summary": [
                    {"column": v["column"], "expected": v["expected_db_type"], "found": v["found_file_type"]}
                        for v in types
                    ],
                "data_quality_issue_summary": [
                    {"column": v["column"], "check": v["check"], "count": v["count"], "severity": v.get("severity", "medium")}
                        for v in dq
                    ]
            }
            return summary

        violations_summary = _create_violation_summary(type_violations, dq_violations)

        # --- Step 6: Build Base Report ---
        logging.info(f"--- [Sheet '{sheet_display_name}'] Step 6: Building Base Report ---")
        file_metadata = {"file_name": os.path.basename(file_path), "sheet_name": sheet_name, "total_rows": file_schema.get("total_rows")}

        base_report = {
            "file_name": file_metadata.get("file_name"),
            "sheet_name": file_metadata.get("sheet_name"),
            "total_rows_checked": file_metadata.get("total_rows"),
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "data_type_mismatch": type_violations,
            "data_quality_issues": dq_violations,
            "dynamic_validation_rules": dynamic_rules,
            "validation_summary": {},
            "data_quality_score": {},
            "triage_plan": [],
            "append_upsert_suggestion": {},
            "schema_drift": {},
            "root_cause_analysis": {},
            "overall_analysis": {}
        }

        # --- Step 7: LLM Final Analysis ---
        logging.info(f"--- [Sheet '{sheet_display_name}'] Step 7: Calling LLM for Final Analysis ---")
        # Call from tools.py
        historical_schemas = tools.load_historical_schemas(target_table_name, NUM_HISTORICAL_SCHEMAS_TO_LOAD)
        
        # Call from prompts.py
        analysis_prompt = prompts.get_analysis_prompt(
            schema_analysis=schema_analysis_json,
            violations_summary=violations_summary,
            historical_schemas=historical_schemas
        )

        # Call from llm_service.py
        # Call from lll_service.py using the new, safe function
        llm_analysis_json = get_llm_json_response(SYSTEM_PROMPT_INSIGHT, analysis_prompt)

        if "error" in llm_analysis_json:
            logging.error(f"Failed to parse JSON from final analysis: {llm_analysis_json['error']}\nRaw response: {llm_analysis_json.get('raw_response')}")
            base_report["validation_summary"] = {"status": "Error", "details": "LLM analysis parsing failed."}
        else:
            base_report.update(llm_analysis_json)

        # Save schema history
        if target_table_name:
            # Call from tools.py
            tools.save_schema_to_history(target_table_name, file_schema)

        logging.info(f"--- Sheet '{sheet_display_name}' Validation Complete ---")

        sheet_report = base_report
        
    except Exception as e:
        logging.error(f"---  ERROR during validation for Sheet '{sheet_display_name}': {e} ---", exc_info=True)
        sheet_report = {
            "file_name": file_path, "sheet_name": sheet_name,
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "validation_summary": { "status": "Error", "details": str(e) },
            "error": str(e)
        }
    
    # Return the full report and the schema analysis (for agent)
    return sheet_report, schema_analysis_json


# =========================================================================
# (NEW) AGENT TOOL 2: RUN VALIDATION FOR A SINGLE SHEET
# =========================================================================
def run_validation_for_single_sheet(file_path: str, sheet_name: str, table_name: str, engine: sqlalchemy.engine.Engine) -> Dict[str, Any]:
    """
    Agent-facing tool to run the full validation pipeline for one sheet.
    This creates the engine, reads the data, and calls the internal logic.
    """
    logging.info(f"---  STARTING SINGLE SHEET VALIDATION: {file_path} (Sheet: {sheet_name}) -> (Table: {table_name}) ---")
    
    
    try:
        # --- Read the specific sheet data ---
        read_sheet_name = sheet_name if sheet_name != "csv_data" else None
        current_df = None
        
        if file_path.endswith(('.xls', '.xlsx')):
            current_df = pd.read_excel(file_path, sheet_name=read_sheet_name)
        elif file_path.endswith('.csv'):
            current_df = pd.read_csv(file_path)
        else:
            raise ValueError(f"Unsupported file type: {file_path}. Only .csv, .xls, and .xlsx are supported.")
        
        logging.info(f"--- Loaded data for sheet: '{sheet_name}' ---")
        
        # --- Call the internal core logic ---
        sheet_report, schema_analysis_json = _run_validation_for_sheet_internal(
            df=current_df, 
            file_path=file_path, 
            sheet_name=read_sheet_name,
            engine=engine,
            target_table_name=table_name
        )
        
        # We need to add the schema_mismatch to the top level for the agent
        # The agent's markdown tool expects it
        final_report = {}
        if "file_name" in sheet_report:
            final_report["file_name"] = sheet_report.pop("file_name")
        if "sheet_name" in sheet_report:
            final_report["sheet_name"] = sheet_report.pop("sheet_name")
        
        final_report["schema_mismatch"] = schema_analysis_json
        final_report.update(sheet_report)

        return final_report

    except Exception as e:
        logging.error(f"A critical error occurred: {e}", exc_info=True)
        return {"error": f"A critical error occurred: {e}"}