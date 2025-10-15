import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
import plotly.express as px
import os
from sqlalchemy import create_engine, text
import hashlib
from supabase import create_client, Client

st.set_page_config(page_title="Balance Your Way", layout="wide")

DB_URL = st.secrets["DB_URL"]
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

engine = create_engine(DB_URL)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

TABLE_NAME = "transactions"  # single table for all users

# -----------------------
# Utilities
# -----------------------
def row_hash(df):
    # return a Series of 64-bit hashes per row (stringified)
    return pd.util.hash_pandas_object(df.astype(str), index=False)

def ensure_user_table():
    # optional: create table if not exists (simple schema)
    # You might prefer to create table manually in Supabase SQL editor.
    with engine.begin() as conn:
        conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id serial PRIMARY KEY,
            "Data" date,
            Operazione text,
            Categoria text,
            Importo numeric,
            user_id uuid
        );
        """))

# -----------------------
# Authentication UI
# -----------------------
def auth_ui():
    st.title("🔐 Sign in / Sign up")
    cols = st.columns([1, 1, 1])
    with cols[0]:
        st.subheader("Login")
        login_email = st.text_input("Email (login)", key="login_email")
        login_pw = st.text_input("Password", type="password", key="login_pw")
        if st.button("Login"):
            try:
                res = supabase.auth.sign_in_with_password({"email": login_email, "password": login_pw})
                if getattr(res, "user", None) is None and isinstance(res, dict) and res.get("user"):
                    # older client returns dict
                    st.session_state.user = res["user"]
                    st.session_state.user_id = res["user"]["id"]
                else:
                    st.session_state.user = res.user
                    st.session_state.user_id = res.user.id
                st.success("Logged in")
                st.rerun()
            except Exception as e:
                st.error(f"Login failed: {e}")

        # Forgot password
        if st.button("Forgot password?"):
            if not login_email:
                st.warning("Enter your email in the Email field above then click 'Forgot password?'")
            else:
                try:
                    # supabase client API name may vary by version: try both
                    try:
                        supabase.auth.reset_password_for_email(login_email)
                    except Exception:
                        supabase.auth.api.reset_password_for_email(login_email)
                    st.success("Password reset email sent (check spam).")
                except Exception as e:
                    st.error(f"Could not send reset email: {e}")

    with cols[1]:
        st.subheader("Sign up")
        signup_email = st.text_input("Email (signup)", key="signup_email")
        signup_pw = st.text_input("Password (signup)", type="password", key="signup_pw")
        if st.button("Sign up"):
            if not signup_email or not signup_pw:
                st.warning("Provide email and password")
            else:
                try:
                    supabase.auth.sign_up({"email": signup_email, "password": signup_pw})
                    st.success("Sign-up OK. Check your email to confirm your account. You will get an email from noreply@mail.app.supabase.io")
                except Exception as e:
                    st.error(f"Signup failed: {e}")

    with cols[2]:
        st.subheader("Reset password (via email link)")
        st.markdown("""
        - Click *Forgot password?* to receive a reset email.
        - The link will redirect to your configured redirect URL.
        """)

# -----------------------
# Handle password-reset redirect token (if any)
# -----------------------
def handle_reset_redirect():
    params = st.query_params
    # Supabase password reset flow may include `access_token` or `type=signup` etc.
    # We try to support the common case where `access_token` or `access_token` present.
    if "access_token" in params:
        token = params["access_token"][0]
        st.subheader("Reset password")
        new_pw = st.text_input("Enter new password", type="password", key="reset_pw")
        if st.button("Update password"):
            try:
                # supabase-py versions differ; try a couple of ways:
                try:
                    supabase.auth.update_user({"password": new_pw}, token=token)
                except TypeError:
                    # older client signature
                    supabase.auth.update_user({"password": new_pw})
                st.success("Password updated — now log in.")
            except Exception as e:
                st.error(f"Could not update password via API: {e}")
        st.stop()

# -----------------------
# Main app (after login)
# -----------------------
def app_ui():
    # welcome title (emoji customizable)
    st.title(f"🏦 Balance Your Way - Welcome {st.session_state.user.email}!")

    ensure_user_table()

    # Attempt to load user's saved data into session state
    if "combined_df" not in st.session_state:
        try:
            # parameterized query to avoid SQL injection
            q = text(f"SELECT * FROM {TABLE_NAME} WHERE user_id = :uid ORDER BY \"Data\"")
            with engine.connect() as conn:
                df_loaded = pd.read_sql(q.bindparams(uid=st.session_state.user_id), conn)
            if not df_loaded.empty:
                # Ensure column names consistent
                if "Categoria" not in df_loaded.columns and "Categoria " in df_loaded.columns:
                    df_loaded = df_loaded.rename(columns={"Categoria ": "Categoria"})
                df_loaded["Data"] = pd.to_datetime(df_loaded["Data"], errors="coerce")
            st.session_state.combined_df = df_loaded
        except Exception as e:
            st.error(f"Could not load DB data: {e}")
            st.session_state.combined_df = pd.DataFrame(columns=["Data", "Operazione", "Categoria", "Importo", "user_id"])

    # Upload + preview area
    st.subheader("Upload new Excel (full dataset)")
    uploaded_file = st.file_uploader("Upload your bank Excel file (.xls/.xlsx)", type=["xls", "xlsx"])
    df_preview = None
    if uploaded_file is not None:
        try:
            df_raw = pd.read_excel(uploaded_file, sheet_name=None)  # try read all sheets first
            # heuristics: find a sheet with 'Data' header or fallback to first sheet
            sheet_names = list(df_raw.keys())
            # try to find the header row dynamically like before
            # we'll attempt the common "Lista Operazione" sheet, else first
            sheet_to_use = "Lista Operazione" if "Lista Operazione" in sheet_names else sheet_names[0]
            print(sheet_to_use)
            df_temp = pd.read_excel(uploaded_file, sheet_name=sheet_to_use, header=None)
            header_row = df_temp.index[df_temp.iloc[:, 0].astype(str).str.contains("Data", na=False)][0]
            df_preview = pd.read_excel(uploaded_file, sheet_name=sheet_to_use, header=header_row)
            df_preview = df_preview.rename(columns=lambda x: x.strip() if isinstance(x, str) else x)
            print(df_preview)
            # normalize column names
            # required columns: Data, Operazione, Categoria, Importo (offer fallback names)
            cols_map = {}
            for c in df_preview.columns:
                cn = str(c).strip().lower()
                if "data" in cn or "dato" in cn:
                    cols_map[c] = "Data"
                elif "descr" in cn or "operaz" in cn or "operazione" in cn or "movimento" in cn: # or "tekst" in cn:
                    cols_map[c] = "Operazione"
                elif "cat" in cn: # or "kategory" in cn:
                    cols_map[c] = "Categoria"
                elif "import" in cn or "amount" in cn: # or "beløb":
                    cols_map[c] = "Importo"
            df_preview = df_preview.rename(columns=cols_map)
            df_preview = df_preview[["Data", "Operazione", "Categoria", "Importo"]].copy()
            df_preview["Data"] = pd.to_datetime(df_preview["Data"], errors="coerce", dayfirst=True)
            df_preview["Importo"] = pd.to_numeric(df_preview["Importo"], errors="coerce")
            df_preview = df_preview.dropna(subset=["Data", "Importo"]).sort_values("Data").reset_index(drop=True)
            st.success(f"Preview loaded — {len(df_preview)} rows ready.")
        except Exception as e:
            st.error(f"Could not parse uploaded file automatically: {e}")
            df_preview = None

    # Merge preview into combined view for inspection but don't persist until Save confirmed
    combined_df = st.session_state.get("combined_df", pd.DataFrame(columns=["Data","Operazione","Categoria","Importo","user_id"]))
    working_df = combined_df.copy()
    if df_preview is not None:
        # merge preview (we'll treat preview as authoritative full dataset if user chooses)
        # add user_id column to preview now (for display)
        df_preview["user_id"] = st.session_state.user_id
        # compute hash and dedupe inside preview alone
        df_preview["_hash"] = row_hash(df_preview[["Data","Operazione","Categoria","Importo","user_id"]])
        working_df["_hash"] = row_hash(working_df[["Data","Operazione","Categoria","Importo","user_id"]]) if not working_df.empty else pd.Series(dtype="int64")
        # Combine (but we will present the preview as replacement if user confirms save)
        temp = pd.concat([working_df, df_preview], ignore_index=True)
        temp = temp.drop(columns="_hash", errors="ignore").sort_values("Data").reset_index(drop=True)
        working_df = temp

    # If there is no data at all, show message
    if working_df.empty:
        st.info("No transactions loaded. Upload a full dataset to begin.")
    # Sidebar: period selection, same as before
    if not working_df.empty:
        # detect salary-based months
        salary_df = working_df[working_df["Categoria"] == "Stipendi e pensioni"].copy()
        salary_df["YearMonth"] = salary_df["Data"].dt.to_period("M")
        salary_periods = salary_df.groupby("YearMonth")["Data"].min().sort_values().reset_index(drop=True)

        def assign_personal_month(date):
            past_periods = salary_periods[salary_periods <= date]
            return past_periods.max() if len(past_periods) else pd.NaT

        working_df["PersonalMonthStart"] = working_df["Data"].apply(assign_personal_month)
        working_df["PeriodName"] = working_df["PersonalMonthStart"].dt.strftime("%Y_%m_%b")#dt.strftime("%Y_%m_%b")

        st.sidebar.header("Filters")
        available_periods = (
            working_df[["PersonalMonthStart", "PeriodName"]].drop_duplicates().dropna().sort_values("PersonalMonthStart")
        )
        period_list = available_periods["PeriodName"].tolist()
        select_all = st.sidebar.checkbox("Select All Periods", value=True)
        selected_periods = st.sidebar.multiselect(
            "Select one or more periods:",
            options=period_list,
            default=period_list if select_all else (period_list[-1:] if period_list else [])
        )

        df = working_df.copy()
        if selected_periods:
            df = df[df["PeriodName"].isin(selected_periods)]
            st.write(f"Showing transactions for **{len(selected_periods)} period(s)**: {', '.join(selected_periods)}")
        else:
            df = df.iloc[0:0]
            st.write("No periods selected → showing empty data.")

        # show summary & charts (same logic as earlier)
        total_expenses = df.loc[df["Importo"] < 0, "Importo"].sum()
        total_income = df.loc[df["Importo"] > 0, "Importo"].sum()
        total_investments = df.loc[(df["Importo"] < 0) & (df["Categoria"].str.contains("Investimenti", case=False, na=False)), "Importo"].sum()
        total_savings = df.loc[(df["Importo"] < 0) & (df["Categoria"].str.contains("Risparmi", case=False, na=False)), "Importo"].sum()

        st.subheader("💰 Summary")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total Expenses", f"{total_expenses:,.2f} €")
        c2.metric("Total Income", f"{total_income:,.2f} €")
        c3.metric("Net for Selection", f"{(total_income + total_expenses):,.2f} €")
        c4.metric("Investments", f"{total_investments:,.2f} €")
        c5.metric("Savings", f"{total_savings:,.2f} €")

        # Pie & bar charts (exclude investments/savings)
        df_charts = df[
            ~df["Categoria"].str.contains("Investimenti", case=False, na=False)
            & ~df["Categoria"].str.contains("Risparmi", case=False, na=False)
        ]
        category_expenses = (
            df_charts[df_charts["Importo"] < 0].groupby("Categoria")["Importo"].sum().abs().sort_values(ascending=False)
        )
        if not category_expenses.empty:
            fig = px.pie(category_expenses.reset_index(), values="Importo", names="Categoria", hole=0.1)
            fig.update_traces(textinfo='none', hovertemplate='%{label}: %{value:.2f} € (%{percent})')
            st.plotly_chart(fig, use_container_width=True)

        monthly_summary = df_charts.groupby("PeriodName")["Importo"].agg(
            Expenses=lambda x: x[x < 0].sum(),
            Income=lambda x: x[x > 0].sum()
        )
        monthly_summary = monthly_summary.reindex(period_list).fillna(0)
        monthly_summary["Net"] = monthly_summary["Income"] + monthly_summary["Expenses"]

        st.write("### Monthly Spending Trend (Net per Period)")
        st.line_chart(monthly_summary["Net"])

        st.write("### Income vs Expenses per Personal Month")
        st.bar_chart(monthly_summary[["Expenses","Income"]], color=["#E73039FF", "#25C025FF"])

        # transactions table
        st.subheader("📄 Transactions for Selected Periods")
        if not df.empty:
            df_display = df[["Data","Operazione","Categoria","Importo"]].copy()
            df_display["Data"] = df_display["Data"].dt.strftime("%Y/%m/%d")
            def color_amount(v): return ('color: red; font-weight:bold' if v<0 else 'color: green; font-weight:bold')
            styled = df_display.style.format({"Importo":"{:,.2f} €"}).applymap(color_amount, subset=["Importo"])
            st.dataframe(styled, use_container_width=True)

    # ----------------------
    # Save flow (replace user's data)
    # ----------------------
    st.markdown("---")
    st.subheader("Save your data!")

    if df_preview is None:
        st.info("No new file uploaded — saving will keep current DB data unchanged.")

    # Initialize state
    if "saving_mode" not in st.session_state:
        st.session_state.saving_mode = False

    # First step: trigger saving mode
    if st.button("💾 Save Updated Data"):
        if uploaded_file:
            st.session_state.saving_mode = True
        else:
            st.warning("Please upload a file before saving.")

    # Second step: confirm save
    if st.session_state.saving_mode:
        confirm_text = st.text_input("Type 'Confirm' to save the uploaded data to the database:", value="")
        
        col_confirm, col_cancel = st.columns(2)
        with col_confirm:
            if st.button("✅ Confirm Save"):
                if confirm_text == "Confirm":
                    try:
                        # Add user_id to the uploaded data before saving
                        df_preview["user_id"] = st.session_state.user_id
                        
                        # Drop technical columns not in DB
                        df_to_save = df_preview.copy()
                        df_to_save = df_to_save.drop(columns=["_hash"], errors="ignore")

                        # Fix uint64 issue by converting to safe types
                        for col in df_to_save.columns:
                            if pd.api.types.is_integer_dtype(df_to_save[col]):
                                if pd.api.types.is_unsigned_integer_dtype(df_to_save[col]):
                                    df_to_save[col] = df_to_save[col].astype(str)
                                else:
                                    df_to_save[col] = df_to_save[col].astype("Int64")
                        # Append directly to DB
                        df_to_save.to_sql(TABLE_NAME, engine, if_exists="append", index=False)

                        # Update session state
                        st.session_state.combined_df = pd.concat([combined_df, df_preview], ignore_index=True)

                        st.success(f"✅ Successfully saved {len(df_preview)} transactions to the database.")
                        st.session_state.saving_mode = False
                    except Exception as e:
                        st.error(f"Error saving data: {e}")
                else:
                    st.warning("You must type 'Confirm' to proceed with saving data.")
        with col_cancel:
            if st.button("❌ Cancel"):
                st.info("Save canceled.")
                st.session_state.saving_mode = False


    # ----------------------
    # Logout
    # ----------------------
    if st.sidebar.button("🚪 Logout"):
        try:
            supabase.auth.sign_out()
        except Exception:
            pass
        st.session_state.clear()
        st.rerun()

# -----------------------
# App entry
# -----------------------
# If the password-reset URL was used, handle it first:
handle_reset_redirect()

if "user" not in st.session_state:
    auth_ui()
else:
    app_ui()
