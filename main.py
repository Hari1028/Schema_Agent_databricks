import os
import json
import logging
from datetime import datetime, timezone
import databricks_tools
# Import the modules we just created
import tools
import validation_module

# We MUST import config at least once at the start
# This initializes the logging and the Azure client
try:
    import config
except Exception as e:
    logging.critical(f"Failed to import config.py. Environment variables might be missing. Error: {e}")
    exit(1)


def main():
    """
    Main function to run the interactive validation process.
    """
    print("--- Schema Validation POC ---")
    
    
    engine, all_table_schemas = databricks_tools.get_db_objects()
    if engine is None:
        logging.critical("Fatal: Failed to connect to Databricks. Exiting.")
        return
    
    print(f"Connection successful. Found {len(all_table_schemas)} tables.")
    # --- 1. GET FILE PATH ---
    file_path = input("Enter the path to your CSV or Excel file: ").strip().strip('"')
    if not os.path.exists(file_path):
        logging.error(f"Error: File not found at path: {file_path}")
        return

    # --- 2. GET SHEETS ---
    logging.info(f"\nScanning file: {file_path}...")
    try:
        # We call the get_sheet_names function from tools.py
        sheet_names = tools.get_sheet_names(file_path)
        
        if not sheet_names:
            logging.warning(f"Error: Could not find any sheets, or the file type is unsupported.")
            logging.warning("Please use a valid .csv, .xls, or .xlsx file.")
            return
            
    except Exception as e:
        logging.error(f"An error occurred while reading the file: {e}", exc_info=True)
        return

    logging.info(f"Found {len(sheet_names)} sheet(s) to process: {sheet_names}")

    # --- 3. PREPARE FULL FILE REPORT ---
    # We create the main JSON structure *before* the loop
    full_file_report = {
        "source_file": os.path.basename(file_path),
        "validation_run_at": datetime.now(timezone.utc).isoformat(),
        "sheet_reports": {}  # This will hold the report for each sheet
    }

    # --- 4. PROCESS EACH SHEET ---
    for sheet in sheet_names:
        print(f"\n" + "="*80)
        print(f"--- Processing Sheet: '{sheet}' ---")
        print("="*80)

        try:
            # --- 5. GET RECOMMENDATIONS ---
            print(f"\n[Step 1/3] Getting table recommendations for '{sheet}'...")
            
            # Call the function from validation_module.py
            recommendations = validation_module.get_recommendations_for_sheet(file_path=file_path, 
                sheet_name=sheet,
                all_db_schemas=all_table_schemas)
            
            if "error" in recommendations:
                logging.error(f"Error getting recommendations: {recommendations['error']}")
                continue # Skip to the next sheet

            top_recs = recommendations.get("recommendations", [])
            if not top_recs:
                logging.error("Error: No table recommendations were returned by the LLM.")
                continue

            # --- 6. GET USER CHOICE ---
            print("\n[Step 2/3] Select the target table:")
            
            valid_table_names = [rec['table_name'] for rec in top_recs]
            
            for rec in top_recs:
                print(f"  > {rec['table_name']} (Confidence: {rec['confidence_score']}%)")
                print(f"      Reason: {rec['reasoning']}")
            
            target_table_name = None
            while True:
                choice = input(f"\nType the exact table name you want to use: ").strip()
                
                if choice in valid_table_names:
                    target_table_name = choice
                    logging.info(f"Selected table: {target_table_name}")
                    break
                else:
                    print(f"Error: '{choice}' is not one of the recommended tables. Please try again.")
            
            # --- 7. RUN FULL VALIDATION ---
            print(f"\n[Step 3/3] Running full validation for '{sheet}' against '{target_table_name}'...")
            
            final_report = validation_module.run_validation_for_single_sheet(
                file_path=file_path,
                sheet_name=sheet,
                table_name=target_table_name,
                engine=engine
            )

            # --- 8. ADD TO MAIN REPORT (CHANGED) ---
            # This section no longer prints the JSON to the terminal
            print("\n" + "-"*30 + f" COMPLETED: '{sheet}' " + "-"*30)

            if "error" in final_report:
                logging.error(f"Validation failed for sheet: {sheet}")
            else:
                logging.info(f"Validation summary for sheet: {sheet}")
                # We still print the summary status (e.g., "Passed", "Failed")
                print(f"Status: {final_report.get('validation_summary', {}).get('status', 'Unknown')}")
            
            # Add this sheet's report to our main dictionary
            full_file_report["sheet_reports"][sheet] = final_report
            
            print("-" * (62 + len(sheet) + 4))

        except Exception as e:
            logging.error(f"A critical error occurred while processing sheet '{sheet}': {e}", exc_info=True)
            full_file_report["sheet_reports"][sheet] = {
                "error": f"A critical error occurred: {str(e)}",
                "validation_summary": {"status": "Error", "details": str(e)}
            }
            continue

    # --- 9. SAVE FULL FILE REPORT (CHANGED) ---
    # This section now saves to your specific filename
    try:
        # CHANGED: Hardcoded filename as requested
        output_filename = "Validation_report.json"
        
        logging.info(f"\nSaving full validation report to: {output_filename}")
        with open(output_filename, 'w') as f:
            json.dump(full_file_report, f, indent=4)
        logging.info(f"Report saved successfully to {output_filename}.")
        
    except Exception as e:
        logging.error(f"Failed to save full report: {e}")

    print("\n--- All sheets processed. ---")


if __name__ == "__main__":
    main()