import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text

# =====================================================================
# 1. PERMANENT SUPABASE CLOUD DATABASE CONNECTION
# =====================================================================
@st.cache_resource
def get_db_engine():
    """Establishes a secure connection to your external permanent database."""
    try:
        # Reads the flat configuration key directly to eliminate TOML errors
        db_url = st.secrets["supabase_url"]
        return create_engine(db_url)
    except Exception:
        st.error("🚨 Supabase Connection Link Missing! Please add 'supabase_url' to your Streamlit Secrets panel.")
        return None

engine = get_db_engine()

def init_db():
    """Creates tables permanently inside the external database if missing."""
    if engine is None:
        return
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS loans (
                id SERIAL PRIMARY KEY,
                borrower_name TEXT NOT NULL,
                principal_lent REAL NOT NULL,
                interest_rate_month REAL NOT NULL,
                initial_loan_date TEXT NOT NULL,
                status TEXT DEFAULT 'Active'
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                loan_id INTEGER,
                transaction_date TEXT NOT NULL,
                total_received REAL NOT NULL,
                interest_paid REAL NOT NULL,
                principal_paid REAL NOT NULL,
                billing_cycle_num INTEGER DEFAULT 1
            );
        """))

# Boot up database tables
init_db()

# =====================================================================
# 2. FINANCIAL CALCULATIONS ENGINE
# =====================================================================
def calculate_expected_cycles(start_date_str):
    """Maps out every single 30-day block window from issuance up to today."""
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    today = datetime.today().date()
    
    all_cycles = []
    current_cycle_start = start_dt + timedelta(days=30)
    month_index = 1

    while current_cycle_start <= today or (month_index == 1):
        current_cycle_end = current_cycle_start + timedelta(days=30)
        all_cycles.append({
            "index": month_index,
            "start": current_cycle_start - timedelta(days=30),
            "due_deadline": current_cycle_start
        })
        current_cycle_start = current_cycle_end
        month_index += 1
        
    return all_cycles
def get_loan_summary(loan_id, principal_lent, interest_rate_month, start_date):
    """Calculates active live metrics, balances, deadlines, and lateness."""
    if engine is None:
        return {"remaining_principal": 0, "remaining_interest": 0, "total_payoff_amount": 0, "next_due_date": start_date, "days_late": 0, "total_i_paid": 0, "total_p_paid": 0}
        
    with engine.connect() as conn:
        df_tx = pd.read_sql(text("SELECT * FROM transactions WHERE loan_id = :id"), conn, params={"id": int(loan_id)})

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    today = datetime.today().date()

    if not df_tx.empty:
        total_p_paid = df_tx["principal_paid"].sum()
        total_i_paid = df_tx["interest_paid"].sum()
        df_tx["tx_date_parsed"] = pd.to_datetime(df_tx["transaction_date"]).dt.date
        latest_tx_date = df_tx["tx_date_parsed"].max()
        next_due_date = latest_tx_date + timedelta(days=30)
    else:
        total_p_paid = 0.0
        total_i_paid = 0.0
        next_due_date = start_dt + timedelta(days=30)

    remaining_principal = max(0.0, principal_lent - total_p_paid)

    all_blocks = calculate_expected_cycles(start_date)
    months_elapsed = len(all_blocks)
    total_interest_accrued = (principal_lent * (interest_rate_month / 100.0)) * months_elapsed
    remaining_interest = max(0.0, total_interest_accrued - total_i_paid)

    total_payoff_amount = remaining_principal + remaining_interest
    days_late = (today - next_due_date).days if today > next_due_date else 0

    return {
        "remaining_principal": remaining_principal,
        "remaining_interest": remaining_interest,
        "total_payoff_amount": total_payoff_amount,
        "next_due_date": next_due_date.strftime("%Y-%m-%d"),
        "days_late": days_late,
        "total_i_paid": total_i_paid,
        "total_p_paid": total_p_paid,
    }

def get_master_dataframe():
    """Pulls open contracts and merges them with live calculation balances."""
    if engine is None:
        return pd.DataFrame()
    with engine.connect() as conn:
        df_loans = pd.read_sql(text("SELECT * FROM loans WHERE status='Active'"), conn)

    if df_loans.empty:
        return pd.DataFrame()

    computed_rows = []
    for _, row in df_loans.iterrows():
        summary = get_loan_summary(row["id"], row["principal_lent"], row["interest_rate_month"], row["initial_loan_date"])
        combined_record = {**row.to_dict(), **summary}
        computed_rows.append(combined_record)

    return pd.DataFrame(computed_rows)

# =====================================================================
# 3. STREAMLIT USER INTERFACE & LAYOUT CONTROLLERS
# =====================================================================
if __name__ == "__main__":
    st.set_page_config(page_title="Zeel Loan Bookkeeping App", layout="wide")
    st.title("🏛️ Zeel — Premium Outgoing Loan Tracker & Ledger")

    df_master = get_master_dataframe()

    # --- TOP GLOBAL BALANCE KPI CARD BOARD ---
    if not df_master.empty:
        total_lent = df_master["principal_lent"].sum()
        interest_per_month = (df_master["principal_lent"] * (df_master["interest_rate_month"] / 100.0)).sum()
        total_pipeline_payoff = df_master["total_payoff_amount"].sum()
    else:
        total_lent, interest_per_month, total_pipeline_payoff = 0.0, 0.0, 0.0

    m_col1, m_col2, m_col3 = st.columns(3)
    m_col1.metric("Total Lent Money (Principal)", f"${total_lent:,.2f}")
    m_col2.metric("Total Interest Revenue / Month", f"${interest_per_month:,.2f}")
    m_col3.metric("Total Outstanding Payoff Pipeline", f"${total_pipeline_payoff:,.2f}")

    st.markdown("---")

    tab1, tab2, tab3, tab4 = st.tabs(["📋 Master Portfolio View", "➕ Create New Loan", "💸 Record Payments", "✏️ Edit Historic Transactions"])
    # TAB 1: OPERATIONAL LEDGER & TIME BLOCK CHIPS
    with tab1:
        st.subheader("Live Operational Loan Ledger")
        if df_master.empty:
            st.info("No active open loans currently tracked.")
        else:
            def highlight_late_loans(row):
                return ["background-color: #ffcccc; color: black"] * len(row) if row["days_late"] > 0 else [""] * len(row)

            display_cols = ["id", "borrower_name", "principal_lent", "interest_rate_month", "initial_loan_date", "remaining_principal", "remaining_interest", "total_payoff_amount", "next_due_date", "days_late"]
            st.dataframe(df_master[display_cols].style.apply(highlight_late_loans, axis=1), use_container_width=True, hide_index=True)

            st.markdown("---")
            st.subheader("⏳ Borrower Monthly Timeline & Transaction Details")

            borrower_opts = {row["id"]: f"{row['borrower_name']} (ID: {row['id']})" for _, row in df_master.iterrows()}
            selected_timeline_id = st.selectbox("Filter Timeline By Borrower Profile:", options=list(borrower_opts.keys()), format_func=lambda x: borrower_opts[x])

            if selected_timeline_id:
                b_row = df_master[df_master["id"] == selected_timeline_id].iloc
                with engine.connect() as conn:
                    df_tx_ledger = pd.read_sql(text("SELECT * FROM transactions WHERE loan_id = :id"), conn, params={"id": int(selected_timeline_id)})

                st.markdown(f"### **Timeline History for {b_row['borrower_name']}**")
                all_cycles = calculate_expected_cycles(b_row["initial_loan_date"])
                all_cycles.reverse()

                if all_cycles:
                    cols = st.columns(len(all_cycles))
                    for idx, cycle in enumerate(all_cycles):
                        with cols[idx]:
                            cycle_txs = df_tx_ledger[df_tx_ledger["billing_cycle_num"] == cycle["index"]] if not df_tx_ledger.empty else pd.DataFrame()
                            cycle_total = cycle_txs["total_received"].sum() if not cycle_txs.empty else 0.0
                            cycle_interest = cycle_txs["interest_paid"].sum() if not cycle_txs.empty else 0.0
                            cycle_principal = cycle_txs["principal_paid"].sum() if not cycle_txs.empty else 0.0

                            today_dt = datetime.today().date()
                            is_overdue = (today_dt >= cycle["due_deadline"]) and cycle_txs.empty
                            days_overdue = (today_dt - cycle["due_deadline"]).days if is_overdue else 0

                            if is_overdue:
                                box_style = "border: 2px solid #ff4b4b; background-color: #ffebe6; padding: 15px; border-radius: 10px; margin-bottom: 10px; color: black;"
                                status_text = f"🚨 **LATE OVERDUE**<br><span style='color:#ff4b4b; font-weight:bold;'>Overdue by {days_overdue} Days!</span>"
                            elif cycle_txs.empty:
                                box_style = "border: 1px solid #ffaa00; background-color: #fff9e6; padding: 15px; border-radius: 10px; margin-bottom: 10px; color: black;"
                                status_text = f"⏳ **Active Cycle**<br><span style='color:#cc8800;'>Due: {cycle['due_deadline'].strftime('%b %d')}</span>"
                            else:
                                box_style = "border: 1px solid #00cc66; background-color: #e6f9f0; padding: 15px; border-radius: 10px; margin-bottom: 10px; color: black;"
                                status_text = "✅ **Paid/Processed**"

                            st.components.v1.html(f"""
                                <div style="{box_style} font-family: sans-serif; box-sizing: border-box; height: 160px;">
                                    <h4 style="margin: 0 0 5px 0; color: black; font-size: 1.1em;">📅 Month {cycle['index']}</h4>
                                    <small style="color: gray; font-size: 0.8em;">Due Deadline: {cycle['due_deadline'].strftime('%b %d, %Y')}</small>
                                    <hr style="margin: 8px 0; border: 0; border-top: 1px solid #ccc;">
                                    <div style="font-size: 0.9em; margin-bottom: 8px;">{status_text}</div>
                                    <p style="margin: 0; font-size: 0.85em; color: black; line-height: 1.4;">
                                        💰 Total Rec: <b>${cycle_total:,.2f}</b><br>
                                        📉 To Principal: <b>${cycle_principal:,.2f}</b><br>
                                        🏷️ To Interest: <b>${cycle_interest:,.2f}</b>
                                    </p>
                                </div>
                            """, height=180, scrolling=False)

    # TAB 2: ORIGINATE NEW LOAN
    with tab2:
        st.subheader("Originate New Loan Contract")
        with st.form("create_loan_form", clear_on_submit=True):
            b_name = st.text_input("Borrower Full Name")
            p_lent = st.number_input("Principal Lent Amount ($)", min_value=0.01, step=100.0)
            i_rate = st.number_input("Monthly Interest Rate Percentage (%)", min_value=0.0, step=0.1)
            init_date = st.date_input("Initial Loan Issuance Date", value=datetime.today() - timedelta(days=30))

            if st.form_submit_button("Create Loan Entry"):
                if b_name and engine:
                    with engine.begin() as conn:
                        conn.execute(text("INSERT INTO loans (borrower_name, principal_lent, interest_rate_month, initial_loan_date) VALUES (:name, :lent, :rate, :date)"),
                                     {"name": b_name, "lent": p_lent, "rate": i_rate, "date": str(init_date)})
                    st.success(f"Recorded new loan contract for {b_name}!")
                    st.rerun()

    # TAB 3: POST TRANSACTION MANUALLY TO A BLOCK CHIP
    with tab3:
        st.subheader("Post Inbound Transaction with Manual Block Target")
        if df_master.empty:
            st.warning("No active profiles available to post collections.")
        else:
            loan_options = {row["id"]: f"ID {row['id']} | {row['borrower_name']}" for _, row in df_master.iterrows()}
            selected_loan_id = st.selectbox("Select Borrower Profile", options=list(loan_options.keys()), format_func=lambda x: loan_options[x], key="tx_borrower_box")
            
            loan_row = df_master[df_master["id"] == selected_loan_id].iloc
            available_blocks = calculate_expected_cycles(loan_row["initial_loan_date"])
            block_options = {b["index"]: f"Month {b['index']} (Due: {b['due_deadline'].strftime('%b %d, %Y')})" for b in available_blocks}
            
            with st.form("manual_post_form", clear_on_submit=True):
                target_block = st.selectbox("Choose Target Billing Block for Payment:", options=list(block_options.keys()), format_func=lambda x: block_options[x])
                tx_date = st.date_input("Payment Date Received", value=datetime.today())
                total_rec = st.number_input("Total Money Received ($)", min_value=0.00, step=50.0)
                interest_p = st.number_input("Portion Allocated to Interest ($)", min_value=0.00, step=10.0)
                principal_p = st.number_input("Portion Allocated to Main Principal ($)", min_value=0.00, step=50.0)

                if st.form_submit_button("Post Transaction Row"):
                    if round(interest_p + principal_p, 2) != round(total_rec, 2):
                        st.error("Validation Error: Allocation components do not match Total Received.")
                    elif engine:
                        with engine.begin() as conn:
                            conn.execute(text("INSERT INTO transactions (loan_id, transaction_date, total_received, interest_paid, principal_paid, billing_cycle_num) VALUES (:l_id, :date, :tot, :int, :prin, :cyc)"),
                                         {"l_id": int(selected_loan_id), "date": str(tx_date), "tot": total_rec, "int": interest_p, "prin": principal_p, "cyc": int(target_block)})
                            
                            metrics = get_loan_summary(selected_loan_id, loan_row["principal_lent"], loan_row["interest_rate_month"], loan_row["initial_loan_date"])
                            if metrics["remaining_principal"] <= 0:
                                conn.execute(text("UPDATE loans SET status='Paid Off' WHERE id=:id"), {"id": int(selected_loan_id)})
                        st.success(f"Payment posted and successfully locked to Month {target_block} block!")
                        st.rerun()

    # TAB 4: AUDIT TOOL & PROFILE DESTROYER
    with tab4:
        st.subheader("Historic Modifications Ledger Audit Tool")
        if engine:
            with engine.connect() as conn:
                df_all_tx = pd.read_sql(text("SELECT transactions.id as tx_id, loans.borrower_name, transactions.transaction_date, transactions.total_received, transactions.interest_paid, transactions.principal_paid, transactions.billing_cycle_num FROM transactions JOIN loans ON transactions.loan_id = loans.id"), conn)
        else:
            df_all_tx = pd.DataFrame()

        if df_all_tx.empty:
            st.info("No transaction records logged yet.")
        else:
            tx_selection_map = {row["tx_id"]: f"Tx #{row['tx_id']} | {row['borrower_name']} | Target Block: Month {row['billing_cycle_num']} | Total: ${row['total_received']}" for _, row in df_all_tx.iterrows()}
            selected_tx_id = st.selectbox("Select Target Historical Entry to Edit", options=list(tx_selection_map.keys()), format_func=lambda x: tx_selection_map[x])
            target_tx = df_all_tx[df_all_tx["tx_id"] == selected_tx_id].iloc

            with st.form("edit_tx_form"):
