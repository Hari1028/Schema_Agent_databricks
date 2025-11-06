# 📊 Validation Report for `new_orders.csv`
**Validation Run At:** 2025-11-06 02:41:41 UTC

---

## 📄 Sheet Report: `csv_data`

### 🧬 Schema Mismatch Analysis
**Target Table:** `customer_orders`

**Naming Mismatches (File -> DB)**
* `cust` → `CustomerID`
* `qty` → `Quantity`

**Columns Missing From File (in DB)**
* `DiscountCode`
* `CustomerID`
* `Quantity`

**Extra Columns in File (not in DB)**
* `ShippingMethod`
* `cust`
* `qty`
* `CustomerEmail`

> **LLM Recommendation:**
> * Map 'cust' to 'CustomerID' and 'qty' to 'Quantity' to ensure proper data alignment.
> * Add 'DiscountCode' to the source file or set a default value if missing.
> * Review and decide whether 'ShippingMethod' and 'CustomerEmail' should be incorporated into the database schema or excluded.
> * Update data ingestion scripts to handle semantic mappings and validate data types accordingly.
> * Implement schema validation rules to catch such mismatches before data loading.

### 🕵️ Problem Details

**1. Data Quality Issues (High Severity First)**
| Severity | Column | Check | Count | Details |
|:---|:---|:---|:---|:---|
| high | `OrderID` | not_null_violation | 1 | Column is non-nullable but contains 1 nulls (or empty strings treated as nulls for non-string columns). |

**2. Data Type Mismatches**
| Column | Expected DB Type | Found File Type | Sample Invalid Values |
|:---|:---|:---|:---|
| `Quantity` | INTEGER | object | `one` |

**3. Inferred Dynamic Rules**
| Column | Rule Type | Inferred from Samples | Details |
|:---|:---|:---|:---|
| `OrderID` | format_check | `ORD1004`, `ORD1005` | Samples follow the pattern 'ORD' followed by 4 digits. A regex like ^ORD\d{4}$ would validate this format. |
| `cust` | enum_check | `CUST003`, `CUST004` | Samples suggest customer IDs follow the pattern 'CUST' followed by 3 digits. An enum list could be generated from existing values or a regex ^CUST\d{3}$ for validation. |
| `OrderDate` | format_check | `2025-10-24`, `2025-10-25` | Samples are in ISO date format YYYY-MM-DD. A regex ^\d{4}-\d{2}-\d{2}$ can validate the date format. |
| `qty` | format_check | `10`, `one` | Samples include numeric strings and a non-numeric string. Validation should check for numeric values only, e.g., using a regex ^\d+$ or numeric type enforcement. |
| `Price` | range_check | `25.0`, `12.5` | Samples are positive float values. A range check could enforce Price > 0 and possibly set an upper limit based on business context. |
| `ShippingMethod` | enum_check | `Standard`, `Express`, `Priority` | Samples suggest a fixed set of shipping methods. Validation should ensure values are within the list: ['Standard', 'Express', 'Priority']. |
| `CustomerEmail` | format_check | `user@example.com`, `bad-email` | Samples include valid and invalid email formats. Validation should enforce proper email regex, e.g., ^[\w.-]+@[\w.-]+\.[A-Za-z]{2,}$. |

### Executive Summary

> **Overall Analysis:** The current dataset is not reliable for production use due to critical data quality issues, especially missing and inconsistent order identifiers. The primary business risk is order processing failures, which could lead to lost revenue and customer dissatisfaction.

**Data Quality Score: D (45/100)**
> *The presence of a high-severity null OrderID violation and a type mismatch in 'Quantity' significantly impair data reliability, risking order processing failures and financial inaccuracies. Additional issues like inconsistent data types and missing critical fields further reduce confidence in the dataset's integrity for operational use.*

**📋 Triage Plan**
1.  **Immediately address the null 'OrderID' entries to prevent order processing failures.** (Priority: 1)
    *Reasoning: Null 'OrderID' values directly block order identification, risking transaction loss and customer dissatisfaction.*
1.  **Correct the 'Quantity' column data type to integer and validate all entries.** (Priority: 2)
    *Reasoning: Type mismatch in 'Quantity' can lead to calculation errors and misinterpretation of order volumes.*
1.  **Implement schema validation to enforce field presence and correct data types before ingestion.** (Priority: 3)
    *Reasoning: Prevents future mismatches and missing data issues, ensuring data consistency.*
1.  **Review and reconcile extra columns ('ShippingMethod', 'CustomerEmail') to determine if they should be incorporated or excluded.** (Priority: 4)
    *Reasoning: Unmapped columns may cause confusion or data inconsistency if not properly handled.*

### 💡 Advanced Analysis

**Suggested Load Strategy: Upsert** (Key: `OrderID`)
> *Using 'OrderID' as the key allows for updating existing records and maintaining data integrity, especially given the nulls and inconsistencies observed.*

**Root Cause Hypothesis**
> *The pattern of null 'OrderID's combined with inconsistent 'Quantity' data types suggests manual data entry errors from spreadsheet uploads or inconsistent source systems, rather than an automated API or system bug.*

**Schema Drift Detected: True**
> *The historical schemas show that 'OrderID' was previously inferred as an object with nulls, and 'qty' was sometimes a string ('one', 'uno') instead of numeric, indicating schema drift likely caused by inconsistent data entry or source changes.*

---