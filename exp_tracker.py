import os
import sqlite3
import tempfile
from datetime import date, timedelta
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ==============================================================================
# CONFIGURATION
# ==============================================================================
DB_FILE = "expenses.db"
LINE_COLOR = "#00C805"
ALLOC_PALETTE = [
    "#636EFA", "#00C805", "#FFA15A", "#AB63FA",
    "#19D3F3", "#FF6692", "#B6E880", "#FECB52",
    "#EF553B", "#00CC96",
]
# ==============================================================================


# --- DATABASE FUNCTIONS ---
def get_conn():
    return sqlite3.connect(DB_FILE)


def init_db():
    with get_conn() as conn:
        # 1. Dynamic expense categories
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY,
                description TEXT DEFAULT ''
            )
            """
        )
        # 2. Expenses table (all amounts in SGD)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                category TEXT NOT NULL,
                item TEXT NOT NULL,
                amount REAL NOT NULL,
                tags TEXT DEFAULT ''
            )
            """
        )
        # 3. Monthly income, bonus & CPF deductions (keyed by YYYY-MM)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_finances (
                month TEXT PRIMARY KEY,
                gross_income REAL DEFAULT 0.0,
                bonus REAL DEFAULT 0.0,
                cpf_deduction REAL DEFAULT 0.0
            )
            """
        )
        mf_cols = [
            row[1] for row in conn.execute("PRAGMA table_info(monthly_finances)").fetchall()
        ]
        if "bonus" not in mf_cols:
            conn.execute("ALTER TABLE monthly_finances ADD COLUMN bonus REAL DEFAULT 0.0")

        # 4. Dynamic allocation types
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS allocation_types (
                name TEXT PRIMARY KEY
            )
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM allocation_types").fetchone()[0]
        if count == 0:
            for default_type in ["Investment", "Savings", "Allowance", "Personal"]:
                conn.execute("INSERT OR IGNORE INTO allocation_types (name) VALUES (?)", (default_type,))

        # 5. Planned monthly allocations from Tab 2 (keyed by month + bucket)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_allocation_items (
                month TEXT NOT NULL,
                bucket TEXT NOT NULL,
                pct REAL DEFAULT 0.0,
                amt REAL DEFAULT 0.0,
                PRIMARY KEY (month, bucket)
            )
            """
        )

        # 6. Finalized / confirmed monthly allocations from Tab 3 (keyed by month + bucket)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finalized_allocation_items (
                month TEXT NOT NULL,
                bucket TEXT NOT NULL,
                amt REAL DEFAULT 0.0,
                PRIMARY KEY (month, bucket)
            )
            """
        )

        # Auto-migrate any data from the old fixed-column monthly_allocations table
        old_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='monthly_allocations'"
        ).fetchone()
        new_count = conn.execute("SELECT COUNT(*) FROM monthly_allocation_items").fetchone()[0]
        if old_exists and new_count == 0:
            old_rows = conn.execute(
                """
                SELECT month, inv_pct, sav_pct, all_pct, per_pct,
                       inv_amt, sav_amt, all_amt, per_amt
                FROM monthly_allocations
                """
            ).fetchall()
            for r in old_rows:
                m = r[0]
                pairs = [
                    ("Investment", r[1], r[5]),
                    ("Savings", r[2], r[6]),
                    ("Allowance", r[3], r[7]),
                    ("Personal", r[4], r[8]),
                ]
                for bucket, pct, amt in pairs:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO monthly_allocation_items (month, bucket, pct, amt)
                        VALUES (?, ?, ?, ?)
                        """,
                        (m, bucket, pct, amt),
                    )


def export_db_bytes():
    """Creates a safe snapshot of the live SQLite database and returns its bytes."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp_path = tmp.name
    tmp.close()
    src = None
    dst = None
    try:
        src = get_conn()
        dst = sqlite3.connect(tmp_path)
        src.backup(dst)
        dst.close()
        dst = None
        src.close()
        src = None
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def restore_db_from_bytes(uploaded_bytes):
    """Validates an uploaded .db backup and safely restores it into the live database."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp_path = tmp.name
    tmp.close()
    src = None
    dst = None
    try:
        with open(tmp_path, "wb") as f:
            f.write(uploaded_bytes)

        src = sqlite3.connect(tmp_path)
        tables = {
            row[0]
            for row in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required_tables = {"expenses", "monthly_finances", "categories"}
        if not required_tables.issubset(tables):
            return False, "Invalid backup file: missing required finance tables."

        dst = get_conn()
        src.backup(dst)
        dst.close()
        dst = None
        src.close()
        src = None

        init_db()
        return True, "Full database restored successfully."
    except sqlite3.DatabaseError:
        return False, "The uploaded file is not a valid SQLite database."
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# --- CATEGORY CRUD ---
def load_categories():
    with get_conn() as conn:
        rows = conn.execute("SELECT name, description FROM categories ORDER BY name ASC").fetchall()
    return {name: desc for name, desc in rows}


def upsert_category(name, description):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO categories (name, description)
            VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET description = excluded.description
            """,
            (name.strip(), description.strip()),
        )


def update_category(old_name, new_name, new_description):
    new_name = new_name.strip()
    new_description = new_description.strip()
    with get_conn() as conn:
        if new_name != old_name:
            exists = conn.execute("SELECT 1 FROM categories WHERE name = ?", (new_name,)).fetchone()
            if exists:
                return False, f"'{new_name}' already exists."
            conn.execute(
                "UPDATE categories SET name = ?, description = ? WHERE name = ?",
                (new_name, new_description, old_name),
            )
            conn.execute(
                "UPDATE expenses SET category = ? WHERE category = ?",
                (new_name, old_name),
            )
        else:
            conn.execute(
                "UPDATE categories SET description = ? WHERE name = ?",
                (new_description, old_name),
            )
    return True, f"Updated '{new_name}'."


def delete_category(name):
    with get_conn() as conn:
        conn.execute("DELETE FROM categories WHERE name = ?", (name,))


# --- EXPENSE CRUD ---
def add_expense(exp_date, category, item, amount, tags):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO expenses (date, category, item, amount, tags)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(exp_date), category, item.strip(), round(float(amount), 2), tags.strip()),
        )


def update_expense(exp_id, exp_date, category, item, amount, tags):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE expenses
            SET date = ?, category = ?, item = ?, amount = ?, tags = ?
            WHERE id = ?
            """,
            (str(exp_date), category, item.strip(), round(float(amount), 2), tags.strip(), int(exp_id)),
        )


def delete_expense(exp_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM expenses WHERE id = ?", (int(exp_id),))


def load_expenses():
    with get_conn() as conn:
        df = pd.read_sql_query("SELECT * FROM expenses ORDER BY date DESC, id DESC", conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


# --- MONTHLY FINANCES CRUD ---
def load_monthly_finances():
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM monthly_finances ORDER BY month DESC", conn)


def upsert_monthly_finance(month_str, gross_income, bonus, cpf_deduction):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO monthly_finances (month, gross_income, bonus, cpf_deduction)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(month) DO UPDATE SET
                gross_income = excluded.gross_income,
                bonus = excluded.bonus,
                cpf_deduction = excluded.cpf_deduction
            """,
            (
                month_str,
                round(float(gross_income), 2),
                round(float(bonus), 2),
                round(float(cpf_deduction), 2),
            ),
        )


# --- DYNAMIC ALLOCATION CRUD (PLANNED IN TAB 2) ---
def load_allocation_types():
    with get_conn() as conn:
        rows = conn.execute("SELECT name FROM allocation_types ORDER BY rowid ASC").fetchall()
    return [r[0] for r in rows]


def add_allocation_type(name):
    name = name.strip()
    with get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM allocation_types WHERE name = ?", (name,)).fetchone()
        if exists:
            return False, f"'{name}' already exists."
        conn.execute("INSERT INTO allocation_types (name) VALUES (?)", (name,))
    return True, f"Added '{name}'."


def rename_allocation_type(old_name, new_name):
    new_name = new_name.strip()
    if new_name == old_name:
        return True, f"No changes made to '{old_name}'."
    with get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM allocation_types WHERE name = ?", (new_name,)).fetchone()
        if exists:
            return False, f"'{new_name}' already exists."
        conn.execute("UPDATE allocation_types SET name = ? WHERE name = ?", (new_name, old_name))
        conn.execute("UPDATE monthly_allocation_items SET bucket = ? WHERE bucket = ?", (new_name, old_name))
        conn.execute("UPDATE finalized_allocation_items SET bucket = ? WHERE bucket = ?", (new_name, old_name))
    return True, f"Renamed '{old_name}' to '{new_name}'."


def delete_allocation_type(name):
    with get_conn() as conn:
        conn.execute("DELETE FROM allocation_types WHERE name = ?", (name,))


def load_monthly_allocation_items(month_str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT bucket, pct, amt FROM monthly_allocation_items WHERE month = ?",
            (month_str,),
        ).fetchall()
    return {bucket: {"pct": pct, "amt": amt} for bucket, pct, amt in rows}


def save_monthly_allocation_items(month_str, pct_map, base_amount):
    with get_conn() as conn:
        conn.execute("DELETE FROM monthly_allocation_items WHERE month = ?", (month_str,))
        for bucket, pct in pct_map.items():
            amt = round(base_amount * (pct / 100.0), 2)
            conn.execute(
                """
                INSERT INTO monthly_allocation_items (month, bucket, pct, amt)
                VALUES (?, ?, ?, ?)
                """,
                (month_str, bucket, round(float(pct), 2), amt),
            )


# --- FINALIZED ALLOCATION CRUD (CONFIRMED IN TAB 3) ---
def load_finalized_allocation_items(month_str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT bucket, amt FROM finalized_allocation_items WHERE month = ?",
            (month_str,),
        ).fetchall()
    return {bucket: amt for bucket, amt in rows}


def load_all_finalized_items():
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM finalized_allocation_items ORDER BY month DESC", conn)


def save_finalized_allocation_items(month_str, amt_map):
    with get_conn() as conn:
        conn.execute("DELETE FROM finalized_allocation_items WHERE month = ?", (month_str,))
        for bucket, amt in amt_map.items():
            conn.execute(
                """
                INSERT INTO finalized_allocation_items (month, bucket, amt)
                VALUES (?, ?, ?)
                """,
                (month_str, bucket, round(float(amt), 2)),
            )


def get_bucket_colors(labels):
    colors = []
    idx = 0
    for lbl in labels:
        if lbl == "Unallocated":
            colors.append("#B6B6B6")
        else:
            colors.append(ALLOC_PALETTE[idx % len(ALLOC_PALETTE)])
            idx += 1
    return colors


def build_clean_pie(labels, values, colors, title):
    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.45,
            marker=dict(colors=colors),
            textposition="inside",
            insidetextorientation="horizontal",
            texttemplate="%{percent:.1%}",
            hovertemplate="<b>%{label}</b><br>S$%{value:,.2f} (%{percent:.1%})<extra></extra>",
            automargin=True,
        )
    )
    fig.update_layout(
        title=dict(text=title, y=0.96, x=0.5, xanchor="center"),
        height=420,
        margin=dict(l=40, r=40, t=70, b=80),
        uniformtext_minsize=11,
        uniformtext_mode="hide",
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.12,
            xanchor="center",
            x=0.5,
        ),
    )
    return fig


def is_personal_expense_row(row):
    cat = str(row.get("category", "")).strip().lower()
    tags = [t.strip().lower() for t in str(row.get("tags", "") or "").split(",")]
    return "personal" in cat or "personal" in tags


# --- APP INIT ---
st.set_page_config(page_title="Personal Finance & Expense Tracker", layout="wide")
st.markdown(
    """
    <style>
        [data-testid='stHeaderActionElements'] { display: none; }
        [data-testid='stMetricValue'] { font-size: 1.5rem; }
        [data-testid='stMetricValue'] > div { overflow: visible; text-overflow: clip; }
        [data-testid='stNumberInput'] [data-testid='InputInstructions'],
        [data-testid='stNumberInput'] small {
            display: none !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

init_db()

categories_map = load_categories()
category_names = list(categories_map.keys())
raw_df = load_expenses()
finances_df = load_monthly_finances()
alloc_types = load_allocation_types()
all_finalized_df = load_all_finalized_items()

# --- SIDEBAR: LOG EXPENSE, MANAGE CATEGORIES, BACKUP ---
with st.sidebar:
    st.header("Log Expense")
    if not category_names:
        st.info("Add at least one expense type below before logging an expense.")
    else:
        with st.form("expense_form", clear_on_submit=True, enter_to_submit=False):
            exp_date = st.date_input("Date", value=date.today(), max_value=date.today())
            category = st.selectbox("Category", category_names)
            item = st.text_input("Item / Description (optional)", placeholder="e.g., Dance class, Lunch")
            amount = st.number_input("Amount (S$)", min_value=0.01, step=0.50, format="%.2f")
            tags = st.text_input("Tags (optional)", placeholder="e.g., recurring, solo, personal")
            submitted = st.form_submit_button("Add Expense", width="stretch")

            if submitted:
                if exp_date > date.today():
                    st.error("Expense date cannot be in the future.")
                else:
                    add_expense(exp_date, category, item, amount, tags)
                    label = item.strip() if item.strip() else category
                    st.success(f"Added S${amount:.2f} for {label}")
                    st.rerun()

    st.divider()

    # Dynamic Category Manager (Add / Edit / Delete)
    with st.expander("Manage Expense Types", expanded=(len(category_names) == 0)):
        c_tab1, c_tab2, c_tab3 = st.tabs(["Add", "Edit", "Delete"])

        with c_tab1:
            with st.form("add_category_form", clear_on_submit=True, enter_to_submit=False):
                new_cat_name = st.text_input("New Expense Type", placeholder="e.g., Food, Entertainment")
                new_cat_desc = st.text_area("Description", placeholder="What belongs here?", height=70)
                if st.form_submit_button("Add Type", width="stretch"):
                    if not new_cat_name.strip():
                        st.error("Please enter a category name.")
                    else:
                        upsert_category(new_cat_name, new_cat_desc)
                        st.success(f"Added '{new_cat_name.strip()}'")
                        st.rerun()

        with c_tab2:
            if not category_names:
                st.caption("No expense types to edit yet.")
            else:
                edit_target = st.selectbox("Select type to edit", category_names, key="edit_cat_select")
                with st.form("edit_category_form", enter_to_submit=False):
                    edited_name = st.text_input("Name", value=edit_target)
                    edited_desc = st.text_area(
                        "Description", value=categories_map.get(edit_target, ""), height=70
                    )
                    if st.form_submit_button("Save Changes", width="stretch"):
                        if not edited_name.strip():
                            st.error("Name cannot be empty.")
                        else:
                            ok, msg = update_category(edit_target, edited_name, edited_desc)
                            if ok:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)

        with c_tab3:
            if not category_names:
                st.caption("No expense types to delete.")
            else:
                st.caption("Keeps past expense history intact.")
                del_cat = st.selectbox("Select type to delete", category_names, key="del_cat_select")
                if st.button("Delete Selected Type", width="stretch"):
                    delete_category(del_cat)
                    st.success(f"Deleted '{del_cat}'")
                    st.rerun()

        if category_names:
            st.divider()
            st.caption("Current types:")
            for c_name, c_desc in categories_map.items():
                st.markdown(f"- **{c_name}**: {c_desc if c_desc else '_(no description)_'}")

    st.divider()
    st.subheader("40-Year Backup")
    
    st.download_button(
        label="Export Full Backup (.db)",
        data=export_db_bytes(),
        file_name=f"finance_full_backup_{date.today()}.db",
        mime="application/octet-stream",
        width="stretch",
        help="Downloads all data across all 4 tabs (expenses, income/CPF, categories, planned & finalized allocations).",
    )

    with st.expander("Restore from Full Backup (.db)"):
        st.caption("Upload a previously exported `.db` file to recreate your entire dashboard. This replaces current data.")
        uploaded_db = st.file_uploader(
            "Select backup file",
            type=["db"],
            key="restore_db_uploader",
        )
        if uploaded_db is not None:
            if st.button("Restore Database Now", type="primary", width="stretch"):
                ok, msg = restore_db_from_bytes(uploaded_db.getvalue())
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    if not raw_df.empty:
        st.download_button(
            label="Export Expenses (CSV)",
            data=raw_df.to_csv(index=False).encode("utf-8"),
            file_name=f"expenses_backup_{date.today()}.csv",
            mime="text/csv",
            width="stretch",
        )
    if not finances_df.empty:
        st.download_button(
            label="Export Monthly Income (CSV)",
            data=finances_df.to_csv(index=False).encode("utf-8"),
            file_name=f"income_backup_{date.today()}.csv",
            mime="text/csv",
            width="stretch",
        )

# --- MAIN DASHBOARD TABS ---
st.title("Personal Finance Dashboard")
tab1, tab2, tab3, tab4 = st.tabs(
    [
        "1. Expense Trends",
        "2. Monthly Income & Cashflow",
        "3. Planned vs. Actual & Finalize",
        "4. Overall Balance Summary",
    ]
)

# ==============================================================================
# TAB 1: EXPENSE TRENDS
# ==============================================================================
with tab1:
    if raw_df.empty:
        st.info("No expenses logged yet. Add an expense type and log your first expense in the sidebar.")
    else:
        all_filter_cats = sorted(set(category_names + raw_df["category"].dropna().unique().tolist()))
        available_years = sorted(
            set(
                [date.today().year]
                + (
                    raw_df["date"].dt.year.dropna().astype(int).tolist()
                    if "date" in raw_df.columns
                    else []
                )
            )
        )

        col1, col2, col3 = st.columns([2, 2, 2])
        with col1:
            selected_category = st.selectbox("Spending Type", ["All"] + all_filter_cats, key="t1_spend_type")
        with col2:
            timeframe_mode = st.radio("Timeframe", ["Default", "Custom"], horizontal=True, key="t1_tf_mode")
        with col3:
            if timeframe_mode == "Default":
                sel_year = st.selectbox(
                    "Select Year",
                    options=available_years,
                    index=(
                        available_years.index(date.today().year)
                        if date.today().year in available_years
                        else len(available_years) - 1
                    ),
                    key="t1_default_year",
                )
                sel_month = None
            else:
                c_yr, c_mo = st.columns(2)
                sel_year = c_yr.selectbox(
                    "Year",
                    options=available_years,
                    index=(
                        available_years.index(date.today().year)
                        if date.today().year in available_years
                        else len(available_years) - 1
                    ),
                    key="t1_custom_year",
                )
                sel_month = c_mo.selectbox(
                    "Month",
                    options=list(range(1, 13)),
                    index=date.today().month - 1,
                    format_func=lambda m: date(2000, m, 1).strftime("%b"),
                    key="t1_custom_month",
                )

        if selected_category != "All" and categories_map.get(selected_category):
            st.caption(f"**{selected_category}:** {categories_map[selected_category]}")

        df = raw_df.copy()
        if selected_category != "All":
            df = df[df["category"] == selected_category]

        def build_breakdown(group):
            if granularity_is_daily or selected_category == "All":
                totals = group.groupby("category")["amount"].sum().sort_values(ascending=False)
            else:
                labels = group["item"].replace("", pd.NA).fillna(group["category"])
                totals = group.groupby(labels)["amount"].sum().sort_values(ascending=False)
            lines = [f"• {name}: S${val:,.2f}" for name, val in totals.items()]
            return "<br>".join(lines[:10]) + ("<br>..." if len(lines) > 10 else "")

        if timeframe_mode == "Default":
            granularity_is_daily = False
            all_periods = pd.period_range(f"{int(sel_year):04d}-01", f"{int(sel_year):04d}-12", freq="M").to_timestamp()
            x_format = "%b"
            unit_label = "Month"

            df = df[df["date"].dt.year == int(sel_year)].copy()
            if not df.empty:
                df["period"] = df["date"].dt.to_period("M").dt.to_timestamp()

            base_periods = pd.DataFrame({"period": all_periods})
            if df.empty:
                grouped = base_periods.copy()
                grouped["total"] = 0.0
                grouped["breakdown"] = "No expenses"
                grouped["count"] = 0
            else:
                agg_df = (
                    df.groupby("period")
                    .apply(
                        lambda g: pd.Series(
                            {
                                "total": g["amount"].sum(),
                                "breakdown": build_breakdown(g),
                                "count": len(g),
                            }
                        ),
                        include_groups=False,
                    )
                    .reset_index()
                )
                grouped = base_periods.merge(agg_df, on="period", how="left")
                grouped["total"] = grouped["total"].fillna(0.0)
                grouped["breakdown"] = grouped["breakdown"].fillna("No expenses")
                grouped["count"] = grouped["count"].fillna(0).astype(int)

            grouped["label"] = grouped["period"].dt.strftime(x_format)

            m1, m2, m3 = st.columns(3)
            m1.metric("Total Spent", f"S${grouped['total'].sum():,.2f}")
            m2.metric(f"Average per {unit_label}", f"S${grouped['total'].mean():,.2f}")
            m3.metric("Transactions", f"{len(df)}")

            if grouped["total"].max() == 0:
                hist_monthly = raw_df.copy()
                if selected_category != "All":
                    hist_monthly = hist_monthly[hist_monthly["category"] == selected_category]
                if not hist_monthly.empty:
                    hist_monthly_sums = hist_monthly.groupby(hist_monthly["date"].dt.to_period("M"))["amount"].sum()
                    fallback_max = max(100.0, round(float(hist_monthly_sums.quantile(0.9)) * 1.1, -1))
                else:
                    fallback_max = 100.0
                yaxis_cfg = dict(title="Amount (S$)", range=[0, fallback_max])
            else:
                yaxis_cfg = dict(title="Amount (S$)", rangemode="tozero")

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=grouped["label"],
                    y=grouped["total"],
                    mode="lines+markers",
                    line=dict(color=LINE_COLOR, width=2.5),
                    marker=dict(size=8, color=LINE_COLOR),
                    customdata=grouped[["breakdown", "count", "label"]],
                    hovertemplate=(
                        "<b>%{customdata[2]}</b><br>"
                        "Total: <b>S$%{y:,.2f}</b> (%{customdata[1]} items)<br>"
                        "<br>%{customdata[0]}<extra></extra>"
                    ),
                )
            )
            fig.update_layout(
                height=400,
                margin=dict(l=10, r=10, t=20, b=10),
                xaxis_title=None,
                xaxis=dict(type="category"),
                yaxis=yaxis_cfg,
                hovermode="closest",
                hoverlabel=dict(align="left", font_size=13),
            )
            selection = st.plotly_chart(fig, width="stretch", on_select="rerun", key="trend_chart")

            if selection and selection.get("selection", {}).get("points"):
                pt_index = selection["selection"]["points"][0]["point_index"]
                clicked_period = grouped.iloc[pt_index]["period"]
                period_df = df[df["period"] == clicked_period] if not df.empty else pd.DataFrame()

                st.subheader(f"Breakdown for {clicked_period.strftime('%b %Y')}")
                if period_df.empty:
                    st.info("No expenses recorded for this period.")
                elif selected_category == "All":
                    cat_summary = (
                        period_df.groupby("category", as_index=False)
                        .agg(amount=("amount", "sum"), entries=("amount", "count"))
                        .sort_values("amount", ascending=False)
                    )
                    cat_summary.columns = ["Spending Type", "Total (S$)", "Entries"]
                    st.dataframe(cat_summary, width="stretch", hide_index=True)
                else:
                    period_rows = period_df[["date", "category", "item", "amount", "tags"]].copy()
                    period_rows["date"] = period_rows["date"].dt.strftime("%Y-%m-%d")
                    st.dataframe(period_rows, width="stretch", hide_index=True)

        else:  # Custom: Year + Month view
            granularity_is_daily = True
            month_period = pd.Period(f"{int(sel_year):04d}-{int(sel_month):02d}", freq="M")
            all_periods = pd.date_range(
                start=month_period.start_time.normalize(),
                end=month_period.end_time.normalize(),
                freq="D",
            )
            x_format = "%d %b"
            unit_label = "Day"

            df = df[df["date"].dt.strftime("%Y-%m") == f"{int(sel_year):04d}-{int(sel_month):02d}"].copy()
            if not df.empty:
                df["period"] = df["date"].dt.floor("D")

            base_periods = pd.DataFrame({"period": all_periods})
            if df.empty:
                grouped = base_periods.copy()
                grouped["total"] = 0.0
                grouped["breakdown"] = "No expenses"
                grouped["count"] = 0
            else:
                agg_df = (
                    df.groupby("period")
                    .apply(
                        lambda g: pd.Series(
                            {
                                "total": g["amount"].sum(),
                                "breakdown": build_breakdown(g),
                                "count": len(g),
                            }
                        ),
                        include_groups=False,
                    )
                    .reset_index()
                )
                grouped = base_periods.merge(agg_df, on="period", how="left")
                grouped["total"] = grouped["total"].fillna(0.0)
                grouped["breakdown"] = grouped["breakdown"].fillna("No expenses")
                grouped["count"] = grouped["count"].fillna(0).astype(int)

            grouped["label"] = grouped["period"].dt.strftime(x_format)

            m1, m2, m3 = st.columns(3)
            m1.metric("Total Spent", f"S${grouped['total'].sum():,.2f}")
            m2.metric(f"Average per {unit_label}", f"S${grouped['total'].mean():,.2f}")
            m3.metric("Transactions", f"{len(df)}")

            tick_labels = grouped.loc[
                grouped["period"].dt.day.isin([1, 10, 20, 30]), "label"
            ].tolist()

            if grouped["total"].max() == 0:
                hist_daily = raw_df.copy()
                if selected_category != "All":
                    hist_daily = hist_daily[hist_daily["category"] == selected_category]
                if not hist_daily.empty:
                    hist_daily_sums = hist_daily.groupby(hist_daily["date"].dt.floor("D"))["amount"].sum()
                    fallback_max = max(100.0, round(float(hist_daily_sums.quantile(0.9)) * 1.1, -1))
                else:
                    fallback_max = 100.0
                yaxis_cfg = dict(title="Amount (S$)", range=[0, fallback_max])
            else:
                yaxis_cfg = dict(title="Amount (S$)", rangemode="tozero")

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=grouped["label"],
                    y=grouped["total"],
                    mode="lines+markers",
                    line=dict(color=LINE_COLOR, width=2.5),
                    marker=dict(size=8, color=LINE_COLOR),
                    customdata=grouped[["breakdown", "count", "label"]],
                    hovertemplate=(
                        "<b>%{customdata[2]}</b><br>"
                        "Total: <b>S$%{y:,.2f}</b> (%{customdata[1]} items)<br>"
                        "<br>%{customdata[0]}<extra></extra>"
                    ),
                )
            )
            fig.update_layout(
                height=400,
                margin=dict(l=10, r=10, t=20, b=10),
                xaxis_title=None,
                xaxis=dict(
                    type="category",
                    tickmode="array",
                    tickvals=tick_labels,
                    ticktext=tick_labels,
                ),
                yaxis=yaxis_cfg,
                hovermode="closest",
                hoverlabel=dict(align="left", font_size=13),
            )
            selection = st.plotly_chart(fig, width="stretch", on_select="rerun", key="trend_chart_custom")

            st.subheader(f"Spendings in {month_period.strftime('%B %Y')}")
            if df.empty:
                st.info("No expenses recorded in this month.")
            else:
                month_rows = df.sort_values("date", ascending=False)[
                    ["date", "category", "item", "amount", "tags"]
                ].copy()
                month_rows["date"] = month_rows["date"].dt.strftime("%Y-%m-%d")
                st.dataframe(month_rows, width="stretch", hide_index=True)

        with st.expander("View, Edit, or Delete Raw Entries"):
            raw_sort_mode = st.radio(
                "Sort Entries By",
                options=["Expense Date (Most Recent)", "Time of Entry (Most Recent)"],
                horizontal=True,
                key="t1_raw_sort_mode",
            )

            display_df = raw_df.copy()
            if raw_sort_mode == "Time of Entry (Most Recent)":
                display_df = display_df.sort_values("id", ascending=False)
            else:
                display_df = display_df.sort_values(["date", "id"], ascending=[False, False])

            display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
            st.dataframe(display_df, width="stretch", hide_index=True)

            st.divider()
            entry_ids = display_df["id"].tolist()
            selected_id = st.selectbox("Select Entry ID to Edit or Delete", entry_ids)
            row = raw_df[raw_df["id"] == selected_id].iloc[0]

            edit_cats = sorted(set(category_names + [row["category"]]))

            with st.form("edit_expense_form", enter_to_submit=False):
                ec1, ec2, ec3 = st.columns(3)
                e_date = ec1.date_input(
                    "Date",
                    value=min(row["date"].date(), date.today()),
                    max_value=date.today(),
                )
                e_cat = ec2.selectbox("Category", edit_cats, index=edit_cats.index(row["category"]))
                e_amount = ec3.number_input(
                    "Amount (S$)", min_value=0.01, value=float(row["amount"]), step=0.50, format="%.2f"
                )

                ec4, ec5 = st.columns(2)
                e_item = ec4.text_input("Item / Description (optional)", value=row["item"])
                e_tags = ec5.text_input("Tags", value=row["tags"] if pd.notna(row["tags"]) else "")

                btn_col1, btn_col2 = st.columns(2)
                save_btn = btn_col1.form_submit_button("Save Changes", width="stretch")
                del_btn = btn_col2.form_submit_button("Delete Entry", width="stretch")

                if save_btn:
                    if e_date > date.today():
                        st.error("Expense date cannot be in the future.")
                    else:
                        update_expense(selected_id, e_date, e_cat, e_item, e_amount, e_tags)
                        st.success(f"Updated entry #{selected_id}.")
                        st.rerun()

                if del_btn:
                    delete_expense(selected_id)
                    st.success(f"Deleted entry #{selected_id}.")
                    st.rerun()

# ==============================================================================
# TAB 2: MONTHLY INCOME, CPF & CASHFLOW (PLANNING ALLOCATION)
# ==============================================================================
with tab2:
    st.subheader("Monthly Income & Cashflow Breakdown")

    c_year, c_month = st.columns(2)
    sel_year = c_year.number_input("Year", min_value=2000, max_value=2100, value=date.today().year, step=1)
    sel_month = c_month.selectbox(
        "Month",
        list(range(1, 13)),
        index=date.today().month - 1,
        format_func=lambda m: date(2000, m, 1).strftime("%B"),
    )
    month_key = f"{sel_year:04d}-{sel_month:02d}"

    existing_row = finances_df[finances_df["month"] == month_key]
    init_gross = float(existing_row["gross_income"].iloc[0]) if not existing_row.empty else 0.0
    init_bonus = (
        float(existing_row["bonus"].iloc[0])
        if (not existing_row.empty and "bonus" in existing_row.columns and pd.notna(existing_row["bonus"].iloc[0]))
        else 0.0
    )
    init_cpf = float(existing_row["cpf_deduction"].iloc[0]) if not existing_row.empty else 0.0

    if not existing_row.empty and init_gross > 0:
        init_cpf_pct = min(100.0, max(0.0, round((init_cpf / init_gross) * 100.0, 2)))
    else:
        init_cpf_pct = 20.0 if existing_row.empty else 0.0

    with st.form("monthly_income_form", enter_to_submit=False):
        ic1, ic2, ic3 = st.columns(3)
        gross_input = ic1.number_input(
            f"Gross Income for {month_key} (S$)",
            min_value=0.0,
            value=init_gross,
            step=100.0,
            format="%.2f",
            key=f"gross_in_{month_key}",
        )
        bonus_input = ic2.number_input(
            f"Bonus for {month_key} (S$)",
            min_value=0.0,
            value=init_bonus,
            step=100.0,
            format="%.2f",
            key=f"bonus_in_{month_key}",
        )
        cpf_pct_input = ic3.number_input(
            f"CPF Deduction for {month_key} (%)",
            min_value=0.0,
            max_value=100.0,
            value=init_cpf_pct,
            step=1.0,
            format="%.2f",
            key=f"cpf_pct_in_{month_key}",
        )
        if st.form_submit_button("Save Monthly Income & CPF"):
            cpf_amount = round(gross_input * (cpf_pct_input / 100.0), 2)
            upsert_monthly_finance(month_key, gross_input, bonus_input, cpf_amount)
            bonus_note = f" + S${bonus_input:,.2f} bonus" if bonus_input > 0 else ""
            st.success(
                f"Saved for {month_key}: Gross S${gross_input:,.2f} | "
                f"CPF S${cpf_amount:,.2f} ({cpf_pct_input:.2f}% of gross){bonus_note}"
            )
            st.rerun()

    if not raw_df.empty:
        month_expenses = raw_df[raw_df["date"].dt.strftime("%Y-%m") == month_key]["amount"].sum()
    else:
        month_expenses = 0.0

    net_after_cpf = (init_gross - init_cpf) + init_bonus
    net_after_expenses = net_after_cpf - month_expenses
    total_monthly_income = init_gross + init_bonus

    k1, k2, k3, k4 = st.columns(4)
    gross_sub = f"Gross S${init_gross:,.2f} + S${init_bonus:,.2f} bonus" if init_bonus > 0 else "Gross income only"
    k1.metric("Total Monthly Income", f"S${total_monthly_income:,.2f}", delta=gross_sub, delta_color="off")
    k2.metric("Net After CPF (+ Bonus)", f"S${net_after_cpf:,.2f}", delta=f"-S${init_cpf:,.2f} CPF", delta_color="off")
    k3.metric("Monthly Expenses", f"S${month_expenses:,.2f}")
    k4.metric("Net After Expenses", f"S${net_after_expenses:,.2f}")

    wf_fig = go.Figure(
        go.Waterfall(
            name="Monthly Cashflow",
            orientation="v",
            measure=["absolute", "relative", "total", "relative", "relative", "total"],
            x=[
                "Gross Income",
                "CPF Deduction",
                "Net After CPF",
                "Bonus",
                "Total Expenses",
                "Net After Expenses",
            ],
            y=[init_gross, -init_cpf, 0, init_bonus, -month_expenses, 0],
            text=[
                f"S${init_gross:,.2f}",
                f"-S${init_cpf:,.2f}",
                f"S${init_gross - init_cpf:,.2f}",
                f"+S${init_bonus:,.2f}",
                f"-S${month_expenses:,.2f}",
                f"S${net_after_expenses:,.2f}",
            ],
            textposition="outside",
            cliponaxis=False,
            decreasing={"marker": {"color": "#EF553B"}},
            increasing={"marker": {"color": "#00C805"}},
            totals={"marker": {"color": "#636EFA"}},
        )
    )
    wf_fig.update_layout(
        title=f"Cashflow Waterfall — {month_key}",
        height=440,
        yaxis_title="Amount (S$)",
        showlegend=False,
        margin=dict(l=20, r=20, t=70, b=30),
    )
    st.plotly_chart(wf_fig, width="stretch")

    st.divider()
    st.subheader(f"Planned Allocation Blueprint — {month_key}")

    # Dynamic Allocation Type Manager (Add / Rename / Delete)
    with st.expander("Manage Allocation Types"):
        at_tab1, at_tab2, at_tab3 = st.tabs(["Add", "Rename", "Delete"])

        with at_tab1:
            with st.form("add_alloc_type_form", clear_on_submit=True, enter_to_submit=False):
                new_alloc_name = st.text_input("New Allocation Type", placeholder="e.g., Emergency Fund, Travel")
                if st.form_submit_button("Add Allocation Type"):
                    if not new_alloc_name.strip():
                        st.error("Please enter a name.")
                    else:
                        ok, msg = add_allocation_type(new_alloc_name)
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)

        with at_tab2:
            if not alloc_types:
                st.caption("No allocation types to rename.")
            else:
                ren_target = st.selectbox("Select type to rename", alloc_types, key="ren_alloc_select")
                with st.form("rename_alloc_type_form", enter_to_submit=False):
                    ren_new = st.text_input("New Name", value=ren_target)
                    if st.form_submit_button("Rename"):
                        if not ren_new.strip():
                            st.error("Name cannot be empty.")
                        else:
                            ok, msg = rename_allocation_type(ren_target, ren_new)
                            if ok:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)

        with at_tab3:
            if not alloc_types:
                st.caption("No allocation types to delete.")
            else:
                st.caption("Removes from future planning while keeping saved/finalized history intact.")
                del_target = st.selectbox("Select type to delete", alloc_types, key="del_alloc_select")
                if st.button("Delete Allocation Type"):
                    delete_allocation_type(del_target)
                    st.success(f"Deleted '{del_target}'.")
                    st.rerun()

    # Always referenced against 1-month Net Income (after CPF, excluding bonus)
    alloc_base = init_gross - init_cpf

    if alloc_base <= 0:
        st.info("Enter a positive Gross Income for this month above to plan allocations.")
    elif not alloc_types:
        st.info("Add at least one allocation type above to start planning allocations.")
    else:
        saved_items = load_monthly_allocation_items(month_key)
        st.caption(
            f"Planned allocations reference **1-Month Net Income (S${alloc_base:,.2f})** after CPF, before bonuses. "
            f"This is a planning preview—click **Save Allocation** to store your plan, then finalize actuals in Tab 3."
        )

        pct_inputs = {}
        cols_per_row = 4
        for i in range(0, len(alloc_types), cols_per_row):
            row_buckets = alloc_types[i : i + cols_per_row]
            row_cols = st.columns(len(row_buckets))
            for col, bucket in zip(row_cols, row_buckets):
                default_pct = float(saved_items.get(bucket, {}).get("pct", 0.0))
                pct_inputs[bucket] = col.number_input(
                    f"{bucket} (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=default_pct,
                    step=5.0,
                    format="%.2f",
                    key=f"alloc_pct_{month_key}_{bucket}",
                )

        total_pct = round(sum(pct_inputs.values()), 2)
        unalloc_pct = max(0.0, round(100.0 - total_pct, 2))

        amt_map = {b: round(alloc_base * (p / 100.0), 2) for b, p in pct_inputs.items()}
        unalloc_amt = max(0.0, round(alloc_base - sum(amt_map.values()), 2))

        if total_pct > 100.0:
            st.error(f"Total planned allocation is {total_pct:.2f}%. Reduce percentages by {total_pct - 100.0:.2f}% to save.")

        left_col, right_col = st.columns([1, 2])

        with left_col:
            for bucket in alloc_types:
                st.metric(bucket, f"S${amt_map[bucket]:,.2f}", f"{pct_inputs[bucket]:.2f}%", delta_color="off")
            st.metric("Unallocated (Gross)", f"S${unalloc_amt:,.2f}", f"{unalloc_pct:.2f}%", delta_color="off")

        with right_col:
            if 0 < total_pct <= 100.0:
                m_labels = [b for b in alloc_types if amt_map[b] > 0]
                m_vals = [amt_map[b] for b in m_labels]
                if unalloc_amt > 0:
                    m_labels.append("Unallocated")
                    m_vals.append(unalloc_amt)

                m_colors = get_bucket_colors(m_labels)
                m_pie = build_clean_pie(m_labels, m_vals, m_colors, f"Planned Allocation Preview — {month_key}")
                st.plotly_chart(m_pie, width="stretch")
            elif total_pct == 0:
                st.info("Enter percentages above to preview the planned allocation chart.")

        if st.button(
            "Save Allocation",
            disabled=(total_pct > 100.0),
            type="primary",
        ):
            save_monthly_allocation_items(month_key, pct_inputs, alloc_base)
            st.success(f"Saved planned allocation for {month_key} (based on Gross Income S${alloc_base:,.2f}).")
            st.rerun()

        if saved_items:
            summary_parts = [
                f"{b} S${vals['amt']:,.2f} ({vals['pct']:.2f}%)"
                for b, vals in saved_items.items()
                if vals["pct"] > 0 or vals["amt"] > 0
            ]
            if summary_parts:
                st.caption(f"Saved plan for {month_key}: " + " | ".join(summary_parts))

# ==============================================================================
# TAB 3: PLANNED ALLOCATION VS. EXPENSES & FINALIZE ALLOCATION
# ==============================================================================
with tab3:
    st.subheader("Planned Allocation vs. Actual Expenses")

    t3_year, t3_month = st.columns(2)
    t3_sel_year = t3_year.number_input(
        "Year", min_value=2000, max_value=2100, value=date.today().year, step=1, key="t3_year"
    )
    t3_sel_month = t3_month.selectbox(
        "Month",
        list(range(1, 13)),
        index=date.today().month - 1,
        format_func=lambda m: date(2000, m, 1).strftime("%B"),
        key="t3_month",
    )
    t3_month_key = f"{t3_sel_year:04d}-{t3_sel_month:02d}"

    planned_map = load_monthly_allocation_items(t3_month_key)
    finalized_saved = load_finalized_allocation_items(t3_month_key)

    t3_fin_row = finances_df[finances_df["month"] == t3_month_key]
    t3_gross = float(t3_fin_row["gross_income"].iloc[0]) if not t3_fin_row.empty else 0.0
    t3_bonus = (
        float(t3_fin_row["bonus"].iloc[0])
        if (not t3_fin_row.empty and "bonus" in t3_fin_row.columns and pd.notna(t3_fin_row["bonus"].iloc[0]))
        else 0.0
    )
    t3_cpf = float(t3_fin_row["cpf_deduction"].iloc[0]) if not t3_fin_row.empty else 0.0

    if not raw_df.empty:
        t3_exp_df = raw_df[raw_df["date"].dt.strftime("%Y-%m") == t3_month_key].copy()
    else:
        t3_exp_df = pd.DataFrame(columns=["date", "category", "item", "amount", "tags"])

    if not t3_exp_df.empty:
        personal_mask = t3_exp_df.apply(is_personal_expense_row, axis=1)
        personal_spend = float(t3_exp_df[personal_mask]["amount"].sum())
        other_spend = float(t3_exp_df[~personal_mask]["amount"].sum())
    else:
        personal_spend = 0.0
        other_spend = 0.0

    total_month_spend = personal_spend + other_spend
    net_after_cpf_cap = round((t3_gross - t3_cpf) - total_month_spend + t3_bonus, 2)

    # Match "Personal" allocation bucket case-insensitively if named Personal / Personal Allowance
    planned_personal_allowance = 0.0
    for b_name, b_data in planned_map.items():
        low = b_name.strip().lower()
        if "personal" in low:
            planned_personal_allowance += float(b_data.get("amt", 0.0))

    # Personal Allowance Diff = Planned Personal Allowance - Total Monthly Expenses
    personal_allowance_diff = round(planned_personal_allowance - total_month_spend, 2)

    # Identify Savings bucket before rendering Planned Allocation Numbers
    savings_bucket_plan = next(
        (b for b in alloc_types if "saving" in b.lower()),
        "Savings",
    )

    st.markdown(f"#### Planned Allocation Numbers — {t3_month_key}")
    if not planned_map:
        st.info(f"No planned allocation saved in Tab 2 for {t3_month_key} yet.")
        plan_cols = st.columns(1)
        plan_cols[0].metric("Planned Allocations", "S$0.00")
    else:
        bonus_note = f" (+ S${t3_bonus:,.2f} bonus)" if t3_bonus > 0 else ""
        st.caption(
            f"Planned allocations reference **1-Month Net Income (S${alloc_base:,.2f})** after CPF, before bonuses. "
            f"Bonuses will be automatically reflected into savings{bonus_note}."
        )
        display_buckets = list(planned_map.keys())
        for i in range(0, len(display_buckets), 4):
            chunk = display_buckets[i : i + 4]
            p_cols = st.columns(len(chunk))
            for col, b_name in zip(p_cols, chunk):
                p_amt = float(planned_map[b_name].get("amt", 0.0))
                p_pct = float(planned_map[b_name].get("pct", 0.0))
                display_p_amt = round(p_amt + t3_bonus, 2) if b_name == savings_bucket_plan else p_amt
                is_personal_plan = "personal" in b_name.strip().lower()
                with col:
                    st.metric(
                        f"Planned {b_name}",
                        f"S${display_p_amt:,.2f}",
                        f"{p_pct:.2f}%",
                        delta_color="normal" if is_personal_plan else "off",
                    )

    st.divider()
    st.markdown(f"#### Monthly Expenses Split — {t3_month_key}")
    st.caption(
        f"Calculation references **Planned Personal Allowance (S${planned_personal_allowance:,.2f})**."
    )
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Personal Spendings", f"S${personal_spend:,.2f}")
    e2.metric("Necessary Spendings", f"S${other_spend:,.2f}")
    e3.metric("Total Monthly Expenses", f"S${total_month_spend:,.2f}")
    e4.metric(
        "Personal Allowance Diff",
        f"{'+' if personal_allowance_diff > 0 else ''}S${personal_allowance_diff:,.2f}",
        delta="Surplus vs. Total Expenses" if personal_allowance_diff >= 0 else "Deficit vs. Total Expenses",
        delta_color="normal" if personal_allowance_diff >= 0 else "inverse",
    )

    st.divider()
    st.markdown(f"#### Confirm & Finalize Allocation — {t3_month_key}")

    # Identify Savings, Allowance, and Investment buckets (case-insensitive)
    savings_bucket = next(
        (b for b in alloc_types if "saving" in b.lower()),
        "Savings",
    )
    allowance_bucket = next(
        (b for b in alloc_types if "allowance" in b.lower() or "parent" in b.lower()),
        "Allowance",
    )
    investment_bucket = next(
        (b for b in alloc_types if "invest" in b.lower()),
        "Investment",
    )

    base_planned_savings_amt = float(planned_map.get(savings_bucket, {}).get("amt", 0.0))
    planned_savings_amt = round(base_planned_savings_amt + t3_bonus, 2)
    planned_allowance_amt = round(float(planned_map.get(allowance_bucket, {}).get("amt", 0.0)), 2)
    planned_investment_amt = round(float(planned_map.get(investment_bucket, {}).get("amt", 0.0)), 2)

    # Savings = (Planned Savings + Monthly Bonus) +/- Personal Allowance Diff
    suggested_savings_amt = round(planned_savings_amt + personal_allowance_diff, 2)

    inv_input_key = f"fin_inv_auto_{t3_month_key}"
    inv_target_key = f"fin_inv_target_{t3_month_key}"
    if inv_input_key not in st.session_state or st.session_state.get(inv_target_key) != planned_investment_amt:
        st.session_state[inv_input_key] = float(planned_investment_amt)
        st.session_state[inv_target_key] = float(planned_investment_amt)

    all_input_key = f"fin_all_auto_{t3_month_key}"
    all_target_key = f"fin_all_target_{t3_month_key}"
    if all_input_key not in st.session_state or st.session_state.get(all_target_key) != planned_allowance_amt:
        st.session_state[all_input_key] = float(planned_allowance_amt)
        st.session_state[all_target_key] = float(planned_allowance_amt)

    sav_input_key = f"fin_sav_auto_{t3_month_key}"
    sav_target_key = f"fin_sav_target_{t3_month_key}"
    if sav_input_key not in st.session_state or st.session_state.get(sav_target_key) != suggested_savings_amt:
        st.session_state[sav_input_key] = float(suggested_savings_amt)
        st.session_state[sav_target_key] = float(suggested_savings_amt)

    st.caption(
        f"**Savings** defaults to **Planned {savings_bucket}** (S\\${planned_savings_amt:,.2f}) "
        f"+ **Personal Allowance Diff** ({'+' if personal_allowance_diff >= 0 else ''}S\\${personal_allowance_diff:,.2f}) "
        f"= **S\\${suggested_savings_amt:,.2f}**. All three below remain editable before confirming; "
        f"total confirmed allocation cannot exceed **Gross Income** (S\\${t3_gross:,.2f}) - **CPF** (S\\${t3_cpf:,.2f}) "
        f"- **Expenses** (S\\${total_month_spend:,.2f}) + **Bonuses** (S\\${t3_bonus:,.2f}) = **S\\${net_after_cpf_cap:,.2f}**."
    )

    fc1, fc2, fc3 = st.columns(3)
    final_inv_amt = fc1.number_input(
        f"{investment_bucket} Final Amount (S$)",
        step=50.0,
        format="%.2f",
        key=inv_input_key,
    )
    final_all_amt = fc2.number_input(
        f"{allowance_bucket} Final Amount (S$)",
        step=50.0,
        format="%.2f",
        key=all_input_key,
    )
    final_sav_amt = fc3.number_input(
        f"{savings_bucket} Final Amount (S$)",
        step=50.0,
        format="%.2f",
        key=sav_input_key,
    )

    # Exclude Personal Allowance / Personal bucket (and main 3) from finalization inputs
    excluded_final_buckets = {
        b for b in alloc_types
        if b in {investment_bucket, allowance_bucket, savings_bucket}
        or "personal" in b.strip().lower()
    }
    extra_buckets = [b for b in alloc_types if b not in excluded_final_buckets]
    extra_vals = {}
    if extra_buckets:
        ex_cols = st.columns(min(4, len(extra_buckets)))
        for idx, b_name in enumerate(extra_buckets):
            init_ex = float(
                finalized_saved.get(b_name, planned_map.get(b_name, {}).get("amt", 0.0))
            )
            extra_vals[b_name] = ex_cols[idx % len(ex_cols)].number_input(
                f"{b_name} Final Amount (S$)",
                value=init_ex,
                step=50.0,
                format="%.2f",
                key=f"fin_ex_{t3_month_key}_{b_name}",
            )

    final_map = {
        investment_bucket: round(float(final_inv_amt), 2),
        allowance_bucket: round(float(final_all_amt), 2),
        savings_bucket: round(float(final_sav_amt), 2),
    }
    for b_name, val in extra_vals.items():
        final_map[b_name] = round(float(val), 2)

    total_final_alloc = round(sum(final_map.values()), 2)
    remaining_cap_buffer = round(net_after_cpf_cap - total_final_alloc, 2)
    exceeds_cap = total_final_alloc > net_after_cpf_cap

    buffer_status = (
        f"Over cap by: S\\${abs(remaining_cap_buffer):,.2f}"
        if exceeds_cap
        else f"Remaining: S\\${remaining_cap_buffer:,.2f}"
    )
    st.caption(
        f"**Preview Total Allocation:** **S\\${total_final_alloc:,.2f}** / "
        f"**S\\${net_after_cpf_cap:,.2f}** ({buffer_status})"
    )

    if exceeds_cap:
        excess_amt = round(total_final_alloc - net_after_cpf_cap, 2)
        st.error(
            f"Total confirmed allocation is S${total_final_alloc:,.2f}, which exceeds Gross Income - CPF - Expenses + Bonuses "
            f"(S${net_after_cpf_cap:,.2f}). Reduce amounts by S${excess_amt:,.2f} to confirm."
        )

    if st.button(
        "Confirm Final Allocation",
        type="primary",
        disabled=exceeds_cap,
        key=f"btn_confirm_fin_{t3_month_key}",
    ):
        save_finalized_allocation_items(t3_month_key, final_map)
        st.success(f"Confirmed and finalized allocation for {t3_month_key}.")
        st.rerun()

    if finalized_saved:
        fin_parts = [
            f"{b}: S${amt:,.2f}"
            for b, amt in finalized_saved.items()
            if amt != 0 and "personal" not in b.strip().lower()
        ]
        if fin_parts:
            st.caption(f"Confirmed & finalized for {t3_month_key}: " + " | ".join(fin_parts))

# ==============================================================================
# TAB 4: OVERALL BALANCE SUMMARY
# ==============================================================================
with tab4:
    st.subheader("Overall Balance Summary")

    base_gross = finances_df["gross_income"].sum() if not finances_df.empty else 0.0
    total_bonus = (
        finances_df["bonus"].fillna(0.0).sum()
        if (not finances_df.empty and "bonus" in finances_df.columns)
        else 0.0
    )
    total_gross = base_gross + total_bonus
    total_cpf = finances_df["cpf_deduction"].sum() if not finances_df.empty else 0.0
    total_net_after_cpf = total_gross - total_cpf
    total_all_expenses = raw_df["amount"].sum() if not raw_df.empty else 0.0
    overall_balance_left = total_net_after_cpf - total_all_expenses

    r1c1, r1c2, r1c3 = st.columns(3)
    r1c1.metric("Cumulative Gross Income", f"S${total_gross:,.2f}")
    r1c2.metric("Cumulative CPF", f"S${total_cpf:,.2f}")
    r1c3.metric("Cumulative Net (After CPF)", f"S${total_net_after_cpf:,.2f}")

    r2c1, r2c2, _ = st.columns(3)
    r2c1.metric("Cumulative Expenses", f"S${total_all_expenses:,.2f}")
    r2c2.metric("Overall Balance Left", f"S${overall_balance_left:,.2f}")

    v1, v2 = st.columns(2)

    st.divider()
    st.subheader("Remaining Balance Distribution")

    all_months_for_fin = sorted(
        set(
            (finances_df["month"].dropna().astype(str).tolist() if not finances_df.empty else [])
            + (raw_df["date"].dt.strftime("%Y-%m").dropna().unique().tolist() if not raw_df.empty else [])
        )
    )

    total_inv_confirmed = 0.0
    total_all_confirmed = 0.0
    total_sav_confirmed = 0.0
    total_other_confirmed = 0.0
    unallocated_by_month = {}

    for m_key in all_months_for_fin:
        fin_items = load_finalized_allocation_items(m_key)
        m_inv = 0.0
        m_all = 0.0
        m_sav = 0.0
        m_other = 0.0

        for b_name, b_amt in fin_items.items():
            low = b_name.strip().lower()
            val = float(b_amt or 0.0)
            if "personal" in low:
                continue
            elif "invest" in low:
                m_inv += val
            elif "allowance" in low or "parent" in low:
                m_all += val
            elif "saving" in low:
                m_sav += val
            else:
                m_other += val

        total_inv_confirmed += m_inv
        total_all_confirmed += m_all
        total_sav_confirmed += m_sav
        total_other_confirmed += m_other

        m_fin_row = finances_df[finances_df["month"] == m_key] if not finances_df.empty else pd.DataFrame()
        m_gross = float(m_fin_row["gross_income"].iloc[0]) if not m_fin_row.empty else 0.0
        m_bonus = (
            float(m_fin_row["bonus"].iloc[0])
            if (not m_fin_row.empty and "bonus" in m_fin_row.columns and pd.notna(m_fin_row["bonus"].iloc[0]))
            else 0.0
        )
        m_cpf = float(m_fin_row["cpf_deduction"].iloc[0]) if not m_fin_row.empty else 0.0
        m_net_after_cpf = (m_gross - m_cpf) + m_bonus

        m_exp = (
            float(raw_df[raw_df["date"].dt.strftime("%Y-%m") == m_key]["amount"].sum())
            if not raw_df.empty
            else 0.0
        )
        m_balance_left = m_net_after_cpf - m_exp
        m_allocated = m_inv + m_all + m_sav + m_other
        m_unalloc = round(m_balance_left - m_allocated, 2)

        if abs(m_unalloc) > 0.005:
            unallocated_by_month[m_key] = {
                "year": m_key[:4],
                "net_after_cpf": round(m_net_after_cpf, 2),
                "expenses": round(m_exp, 2),
                "balance_left": round(m_balance_left, 2),
                "confirmed_alloc": round(m_allocated, 2),
                "unallocated": m_unalloc,
            }

    total_inv_confirmed = round(total_inv_confirmed, 2)
    total_all_confirmed = round(total_all_confirmed, 2)
    total_sav_confirmed = round(total_sav_confirmed, 2)
    total_other_confirmed = round(total_other_confirmed, 2)
    total_allocated_confirmed = round(
        total_inv_confirmed + total_all_confirmed + total_sav_confirmed + total_other_confirmed, 2
    )
    unallocated_balance = round(max(0.0, overall_balance_left - total_allocated_confirmed), 2)

    st.caption(
        f"Breakdown of confirmed allocations across finalized months vs. **Overall Balance Left** (S\\${overall_balance_left:,.2f})."
    )

    left_dist_col, right_dist_col = st.columns([1, 2])

    with left_dist_col:
        st.metric("Investment", f"S${total_inv_confirmed:,.2f}")
        st.metric("Allowance (Parents)", f"S${total_all_confirmed:,.2f}")
        st.metric("Savings", f"S${total_sav_confirmed:,.2f}")
        st.metric("Unallocated Balance", f"S${unallocated_balance:,.2f}")

    with right_dist_col:
        dist_labels = ["Investment", "Allowance (Parents)", "Savings"]
        dist_values = [total_inv_confirmed, total_all_confirmed, total_sav_confirmed]
        dist_colors = ["#19D3F3", "#FFA15A", "#00C805"]

        if total_other_confirmed > 0:
            dist_labels.append("Other Buckets")
            dist_values.append(total_other_confirmed)
            dist_colors.append("#AB63FA")

        if unallocated_balance > 0:
            dist_labels.append("Unallocated Balance")
            dist_values.append(unallocated_balance)
            dist_colors.append("#636EFA")

        if sum(dist_values) > 0:
            dist_fig = go.Figure(
                data=[
                    go.Pie(
                        labels=dist_labels,
                        values=dist_values,
                        hole=0.45,
                        marker=dict(colors=dist_colors),
                        textinfo="label+percent",
                        textposition="outside",
                        automargin=True,
                        hovertemplate="<b>%{label}</b><br>S$%{value:,.2f} (%{percent})<extra></extra>",
                    )
                ]
            )
            dist_fig.update_layout(
                title=dict(
                    text="Remaining Balance Distribution",
                    y=0.96,
                    x=0.5,
                    xanchor="center",
                ),
                height=440,
                margin=dict(l=40, r=40, t=70, b=80),
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.18,
                    xanchor="center",
                    x=0.5,
                ),
            )
            st.plotly_chart(dist_fig, width="stretch")
        else:
            st.info("No confirmed allocations or remaining balance recorded yet.")

    if unallocated_balance > 0 and unallocated_by_month:
        with st.expander("Inspect Unallocated Balance by Month"):
            unalloc_rows = [
                {
                    "Month": m_k,
                    "Net After CPF (S$)": info["net_after_cpf"],
                    "Expenses (S$)": info["expenses"],
                    "Balance Left (S$)": info["balance_left"],
                    "Confirmed Allocations (S$)": info["confirmed_alloc"],
                    "Unallocated (S$)": info["unallocated"],
                }
                for m_k, info in sorted(unallocated_by_month.items())
            ]
            st.dataframe(pd.DataFrame(unalloc_rows), hide_index=True, width="stretch")

    st.divider()
    st.subheader("Monthly Income, CPF & Net Breakdown")

    available_years = sorted(
        set(
            [date.today().year]
            + (
                finances_df["month"].str[:4].dropna().astype(int).tolist()
                if not finances_df.empty
                else []
            )
        )
    )

    yc1, yc2 = st.columns([1, 2])
    sel_t4_year = yc1.selectbox(
        "Select Year",
        options=available_years,
        index=available_years.index(date.today().year) if date.today().year in available_years else len(available_years) - 1,
        key="t4_yearly_breakdown_year",
    )

    series_options = [
        "Gross Income",
        "CPF",
        "Net Income After CPF (+ Bonus)",
    ]
    selected_series = yc2.multiselect(
        "Display Series",
        options=series_options,
        default=series_options,
        key="t4_yearly_series_select",
    )

    months_12 = [f"{sel_t4_year:04d}-{m:02d}" for m in range(1, 13)]
    month_labels = [date(sel_t4_year, m, 1).strftime("%b") for m in range(1, 13)]

    t4_fin_year = (
        finances_df[finances_df["month"].str.startswith(f"{sel_t4_year:04d}-")].copy()
        if not finances_df.empty
        else pd.DataFrame(columns=["month", "gross_income", "bonus", "cpf_deduction"])
    )
    if not t4_fin_year.empty and "bonus" not in t4_fin_year.columns:
        t4_fin_year["bonus"] = 0.0

    fin_by_month = {}
    for _, row in t4_fin_year.iterrows():
        m_key = str(row["month"])
        g_val = float(row["gross_income"]) if pd.notna(row["gross_income"]) else 0.0
        b_val = float(row["bonus"]) if pd.notna(row["bonus"]) else 0.0
        c_val = float(row["cpf_deduction"]) if pd.notna(row["cpf_deduction"]) else 0.0
        fin_by_month[m_key] = (g_val, b_val, c_val)

    gross_series = []
    cpf_series = []
    net_after_cpf_bonus_series = []

    for m_key in months_12:
        g_val, b_val, c_val = fin_by_month.get(m_key, (0.0, 0.0, 0.0))
        gross_amt = round(g_val, 2)
        cpf_amt = round(c_val, 2)
        # CPF applies only to monthly gross income; bonuses are added after CPF deduction
        net_amt = round((g_val - c_val) + b_val, 2)

        gross_series.append(gross_amt)
        cpf_series.append(cpf_amt)
        net_after_cpf_bonus_series.append(net_amt)

    series_config = {
        "Gross Income": (gross_series, "#19D3F3"),
        "CPF": (cpf_series, "#FFA15A"),
        "Net Income After CPF (+ Bonus)": (net_after_cpf_bonus_series, "#00C805"),
    }

    if not selected_series:
        st.info("Select at least one series above to display the line graph.")
    else:
        monthly_fig = go.Figure()
        for s_name in series_options:
            if s_name in selected_series:
                y_vals, s_color = series_config[s_name]
                monthly_fig.add_trace(
                    go.Scatter(
                        name=s_name,
                        x=month_labels,
                        y=y_vals,
                        mode="lines+markers",
                        line=dict(color=s_color, width=2.5),
                        marker=dict(size=8, color=s_color),
                        hovertemplate=(
                            f"<b>{s_name}</b><br>"
                            "%{x}: <b>S$%{y:,.2f}</b><extra></extra>"
                        ),
                    )
                )

        monthly_fig.update_layout(
            title=dict(
                text=f"Monthly Gross Income, CPF & Net Income After CPF (+ Bonus) — {sel_t4_year}",
                y=0.96,
                x=0.5,
                xanchor="center",
            ),
            height=440,
            xaxis_title="Month",
            yaxis_title="Amount (S$)",
            hovermode="x unified",
            margin=dict(l=20, r=20, t=70, b=40),
            legend=dict(
                orientation="h",
                yanchor="top",
                y=-0.16,
                xanchor="center",
                x=0.5,
            ),
        )
        st.plotly_chart(monthly_fig, width="stretch")