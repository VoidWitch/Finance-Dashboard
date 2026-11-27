# Finance Dashboard

A local-first personal finance and expense tracking dashboard built with **Streamlit**, **SQLite**, and **Plotly**. Designed for tracking daily expenses in SGD, modeling monthly income with CPF deductions and bonuses, planning and finalizing monthly allocations, and monitoring cumulative balances over time.

## Features

### 1. Expense Trends
* **Flexible Timeframes:** Switch between **Default** (full 12-month yearly overview) and **Custom** (daily breakdown for any selected month).
* **Interactive Visuals:** Filter by spending type, hover for category/item breakdowns, and click data points to inspect period summaries.
* **Raw Entry Management:** View, edit, or delete entries with sorting by **Expense Date** or **Time of Entry**.
* **Date Guardrails:** Logging and editing expenses are strictly restricted to dates on or before today.

### 2. Monthly Income & Cashflow
* **Income & CPF Tracking:** Log monthly gross income, bonuses, and CPF deduction percentages.
* **Cashflow Waterfall:** Visualizes the flow from Gross Income → CPF Deduction → Net After CPF → Bonus → Total Expenses → Net After Expenses.
* **Planned Allocation Blueprint:** Dynamically add, rename, or delete allocation buckets (e.g., Investment, Savings, Allowance, Personal) and preview percentage-based splits against 1-month net income.

### 3. Planned vs. Actual & Finalize
* **Spending Split:** Automatically separates expenses into **Personal** and **Necessary** spendings and computes the surplus/deficit against your Planned Personal Allowance.
* **Allocation Finalization:** Auto-syncs suggested final amounts (factoring in bonuses and personal allowance variance) while letting you adjust values before confirming.
* **Cap Validation:** Prevents confirmed allocations from exceeding `Gross Income - CPF - Expenses + Bonuses` (with full support for negative balances).

### 4. Overall Balance Summary
* **Cumulative Metrics:** Tracks lifetime Gross Income, CPF, Net Income, Expenses, and Overall Balance Left.
* **Remaining Balance Distribution:** Donut chart comparing confirmed allocations across buckets against any unallocated balance, with a month-by-month unallocated inspector.
* **Yearly Breakdown:** Multi-series line chart comparing monthly Gross Income, CPF, and Net Income (+ Bonus) across any selected year.

### Sidebar & Data Management
* **Quick Expense Logger & Category Manager:** Add, edit, or delete custom expense categories without losing historical records.
* **Full Backup & Restore:** Export a complete SQLite `.db` snapshot containing all data across all four tabs, and restore it anytime with one click. Also supports CSV exports for expenses and monthly income.

## Tech Stack
* **Frontend:** Streamlit
* **Database:** SQLite (`expenses.db`)
* **Data Processing:** Pandas
* **Visualizations:** Plotly

## Getting Started

1. **Clone the repository:**
   ```bash
   git clone https://github.com/<your-username>/finance-dashboard.git
   cd finance-dashboard
   ```

2. **Install dependencies:**
   ```bash
   pip install streamlit pandas plotly
   ```

3. **Run the dashboard:**
   ```bash
   streamlit run exp_tracker.py
   ```

## Data Privacy & Backups
All data is stored locally in `expenses.db`, which is automatically created on first launch. To avoid uploading personal financial data to GitHub, add `expenses.db` to your `.gitignore` file.
