import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text

# =====================================================================
# 0. GLOBAL CORE CONFIGURATION (MUST BE ABSOLUTE FIRST COMMAND)
# =====================================================================
st.set_page_config(page_title="Zeel Loan Bookkeeping App", layout="wide")

# =====================================================================
# 1. PERMANENT LOCAL SQLITE DATABASE CONNECTION
# =====================================================================
@st.cache_resource
def get_db_engine():
    """Establishes a permanent local SQLite database file."""
    try:
        # Uses a local file database that stays inside your folder structure permanently
        return create_engine("sqlite:///zeel_ledger.db", pool_pre_ping=True)
    except Exception as e:
        st.error(f"🚨 Local Database initialization failed! Error: {e}")
        return None

engine = get_db_engine()

def init_db():
    """Creates tables permanently inside the local SQLite database file if missing."""
    if engine is None:
        return
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS loans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                borrower_name TEXT NOT NULL,
                principal_lent REAL NOT NULL,
                interest_rate_month REAL NOT NULL,
                initial_loan_date TEXT NOT NULL,
                status TEXT DEFAULT 'Active'
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    # TAB 2: CREATE NEW LOAN
    with tab2:
        st.subheader("Add a New Loan Agreement")
        with st.form("new_loan_form", clear_on_submit=True):
            b_name = st.text_input("Borrower Full Name")
            p_lent = st.number_input("Principal Lent Amount ($)", min_value=0.0, step=100.0)
            i_rate = st.number_input("Monthly Interest Rate (%)", min_value=0.0, step=0.1)
            start_d = st.date_input("Initial Loan Date", datetime.today())
            
            submit_loan = st.form_submit_button("Save Loan To Database")
            
            if submit_loan and b_name:
                if engine:
                    with engine.begin() as conn:
                        conn.execute(text("""
                            INSERT INTO loans (borrower_name, principal_lent, interest_rate_month, initial_loan_date, status)
                            VALUES (:name, :principal, :rate, :start_date, 'Active')
                        """), {"name": b_name, "principal": p_lent, "rate": i_rate, "start_date": start_d.strftime("%Y-%m-%d")})
                    st.success(f"🎉 Loan safely saved for {b_name}!")
                    st.rerun()

    # TAB 3: RECORD PAYMENTS
    with tab3:
        st.subheader("Record Incoming Payment Receipt")
        if df_master.empty:
            st.info("No active open loans to process payments for.")
        else:
            with st.form("payment_form", clear_on_submit=True):
                borrower_opts = {row["id"]: f"{row['borrower_name']} (ID: {row['id']})" for _, row in df_master.iterrows()}
                pay_loan_id = st.selectbox("Select Active Borrower Profile:", options=list(borrower_opts.keys()), format_func=lambda x: borrower_opts[x])
                pay_date = st.date_input("Transaction Date", datetime.today())
                total_rec = st.number_input("Total Amount Received ($)", min_value=0.0, step=10.0)
                int_paid = st.number_input("Allocated to Interest Paid ($)", min_value=0.0, step=10.0)
                prin_paid = st.number_input("Allocated to Principal Paid ($)", min_value=0.0, step=10.0)

                submit_pay = st.form_submit_button("Commit Payment Entry")
                
                if submit_pay:
                    if engine:
                        with engine.begin() as conn:
                            conn.execute(text("""
                                INSERT INTO transactions (loan_id, transaction_date, total_received, interest_paid, principal_paid)
                                VALUES (:loan_id, :tx_date, :total, :interest, :principal)
                            """), {"loan_id": pay_loan_id, "tx_date": pay_date.strftime("%Y-%m-%d"), "total": total_rec, "interest": int_paid, "principal": prin_paid})
                        st.success("💸 Payment transaction completely logged into Local Storage!")
                        st.rerun()

    # TAB 4: PLACEHOLDER FOR EDIT HISTORIC TRANSACTIONS
    with tab4:
        st.subheader("Manage Historical Ledger Entries")
        st.info("This administrative section allows correcting payment logs manually if needed.")
