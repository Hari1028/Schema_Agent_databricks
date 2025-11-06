import json
import os
from datetime import datetime

# --- Configuration ---
INPUT_FILE = "Validation_report.json"
OUTPUT_FILE = "Validation_report.md"

def format_timestamp(ts_string):
    """Helper to format ISO timestamps nicely."""
    try:
        dt = datetime.fromisoformat(ts_string)
        return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except (ValueError, TypeError):
        return ts_string

def build_markdown_report(data):
    """Generates the markdown content from the loaded JSON data."""
    md = []

    # --- 1. Main Header ---
    md.append(f"# 📊 Validation Report for `{data.get('source_file', 'N/A')}`")
    run_at = format_timestamp(data.get('validation_run_at'))
    md.append(f"**Validation Run At:** {run_at}\n")
    md.append("---")

    if not data.get('sheet_reports'):
        md.append("\n**No sheet reports found in the JSON file.**")
        return "\n".join(md)

    # --- 2. Loop Through Each Sheet Report ---
    for sheet_key, report in data.get('sheet_reports', {}).items():
        # Use the key as the name if sheet_name is null (for CSVs)
        sheet_name = report.get('sheet_name') or sheet_key 
        
        md.append(f"\n## 📄 Sheet Report: `{sheet_name}`")
        
        # Handle cases where the entire sheet validation failed
        if "error" in report and "validation_summary" not in report:
            md.append(f"\n> **🚨 An error occurred during validation for this sheet:**")
            md.append(f"> {report['error']}")
            md.append("\n---")
            continue  # Skip to the next sheet

        # --- 3. Schema Mismatch Analysis (MOVED TO TOP) ---
        md.append("\n### 🧬 Schema Mismatch Analysis")
        schema = report.get('schema_mismatch', {})
        md.append(f"**Target Table:** `{schema.get('target_table', 'N/A')}`")
        
        md.append(f"\n**Naming Mismatches (File -> DB)**")
        mappings = schema.get('naming_mismatches', {})
        if mappings:
            for file_col, db_col in mappings.items():
                md.append(f"* `{file_col}` → `{db_col}`")
        else:
            md.append("*No semantic naming mismatches found.*")

        md.append(f"\n**Columns Missing From File (in DB)**")
        missing = schema.get('columns_missing_from_file', [])
        if missing:
            for col in missing:
                md.append(f"* `{col}`")
        else:
            md.append("*No missing columns.*")

        md.append(f"\n**Extra Columns in File (not in DB)**")
        extra = schema.get('columns_extra_in_file', [])
        if extra:
            for col in extra:
                md.append(f"* `{col}`")
        else:
            md.append("*No extra columns.*")
        
        md.append("\n> **LLM Recommendation:**")
        recs = schema.get('analysis', {}).get('recommendation', [])
        if recs:
            for rec in recs:
                md.append(f"> * {rec}")
        else:
            md.append("> *No recommendations provided.*")

        # --- 4. Problem Details ---
        md.append("\n### 🕵️ Problem Details")

        # Data Quality Issues
        md.append("\n**1. Data Quality Issues (High Severity First)**")
        dq_issues = report.get('data_quality_issues', [])
        if dq_issues:
            # Sort by severity (high > medium > low)
            sev_map = {'high': 1, 'medium': 2, 'low': 3}
            dq_issues_sorted = sorted(dq_issues, key=lambda x: sev_map.get(x.get('severity', 'low'), 3))
            
            md.append("| Severity | Column | Check | Count | Details |")
            md.append("|:---|:---|:---|:---|:---|")
            for issue in dq_issues_sorted:
                md.append(f"| {issue.get('severity', 'N/A')} | `{issue.get('column', 'N/A')}` | {issue.get('check', 'N/A')} | {issue.get('count', 'N/A')} | {issue.get('details', 'N/A')} |")
        else:
            md.append("✅ *No data quality issues found.*")

        # Data Type Mismatches
        md.append("\n**2. Data Type Mismatches**")
        type_issues = report.get('data_type_mismatch', [])
        if type_issues:
            md.append("| Column | Expected DB Type | Found File Type | Sample Invalid Values |")
            md.append("|:---|:---|:---|:---|")
            for issue in type_issues:
                samples = ", ".join([f"`{s}`" for s in issue.get('sample_invalid_values', [])])
                md.append(f"| `{issue.get('column', 'N/A')}` | {issue.get('expected_db_type', 'N/A')} | {issue.get('found_file_type', 'N/A')} | {samples} |")
        else:
            md.append("✅ *No data type mismatches found.*")

        # Inferred Dynamic Rules
        md.append("\n**3. Inferred Dynamic Rules**")
        dynamic_rules = report.get('dynamic_validation_rules', [])
        
        if not dynamic_rules:
            md.append("✅ *No dynamic rules were inferred.*")
        elif isinstance(dynamic_rules, list) and dynamic_rules[0].get('error'):
            md.append(f"❌ *Could not generate rules: {dynamic_rules[0]['error']}*")
        else:
            md.append("| Column | Rule Type | Inferred from Samples | Details |")
            md.append("|:---|:---|:---|:---|")
            for rule in dynamic_rules:
                # Format samples list
                samples_list = rule.get('inferred_from_samples', [])
                samples_str = ", ".join([f"`{str(s)}`" for s in samples_list]) # Use str(s) to be safe
                
                md.append(f"| `{rule.get('column', 'N/A')}` | {rule.get('rule_type', 'N/A')} | {samples_str} | {rule.get('rule_details', 'N/A')} |")

        # --- 5. Executive Summary (MOVED TO END) ---
        md.append("\n### Executive Summary")

        # Overall Analysis
        summary = report.get('overall_analysis', {}).get('narrative_summary', 'No analysis provided.')
        md.append(f"\n> **Overall Analysis:** {summary}")

        # Data Quality Score
        score = report.get('data_quality_score', {})
        md.append(f"\n**Data Quality Score: {score.get('grade', 'N/A')} ({score.get('score', 'N/A')}/100)**")
        md.append(f"> *{score.get('reasoning', 'No reasoning provided.')}*")

        # Triage Plan
        md.append(f"\n**📋 Triage Plan**")
        plan = report.get('triage_plan', [])
        if plan:
            for item in plan:
                md.append(f"1.  **{item.get('action', 'N/A')}** (Priority: {item.get('priority', 'N/A')})")
                md.append(f"    *Reasoning: {item.get('reasoning', 'N/A')}*")
        else:
            md.append("✅ *No triage plan provided.*")

        # --- 6. Advanced Analysis ---
        md.append("\n### 💡 Advanced Analysis")

        # Load Strategy
        load = report.get('append_upsert_suggestion', {})
        md.append(f"\n**Suggested Load Strategy: {load.get('strategy', 'N/A')}** (Key: `{load.get('key_column', 'N/A')}`)")
        md.append(f"> *{load.get('reasoning', 'N/A')}*")

        # Root Cause
        root = report.get('root_cause_analysis', {})
        md.append(f"\n**Root Cause Hypothesis**")
        md.append(f"> *{root.get('hypothesis', 'N/A')}*")

        # Schema Drift
        drift = report.get('schema_drift', {})
        md.append(f"\n**Schema Drift Detected: {drift.get('detected', 'N/A')}**")
        md.append(f"> *{drift.get('analysis', 'N/A')}*")
        
        md.append("\n---") # End of the sheet loop

    return "\n".join(md)

def main():
    """
    Main function to read the JSON file and write the markdown report.
    """
    if not os.path.exists(INPUT_FILE):
        print(f"Error: Input file not found at {INPUT_FILE}")
        return

    print(f"Reading {INPUT_FILE}...")
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse {INPUT_FILE}. Invalid JSON. {e}")
        return
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    print("Building markdown report...")
    markdown_content = build_markdown_report(data)
    
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
        print(f"✅ Successfully generated report at {OUTPUT_FILE}")
    except Exception as e:
        print(f"Error writing markdown file: {e}")

if __name__ == "__main__":
    main()