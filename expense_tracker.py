import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
import plotly.express as px
import os
from sqlalchemy import create_engine


st.set_page_config(page_title="Personal Expense Tracker", layout="wide")
st.title("📊 Personal Expense Tracker")

# MASTER_FILE = "transactions_master.parquet"
db_url = st.secrets["DB_URL"]
engine = create_engine(db_url)
TABLE_NAME = "transactions"

# --------------------------
# Load from DB or create empty DataFrame
# --------------------------
if engine.dialect.has_table(engine.connect(), TABLE_NAME):
    master_df = pd.read_sql(f"SELECT * FROM {TABLE_NAME}", engine)
else:
    master_df = pd.DataFrame(columns=["Data", "Operazione", "Categoria", "Importo"])

# --------------------------
# Upload New Data
# --------------------------
uploaded_file = st.file_uploader("Upload your bank Excel file (.xls or .xlsx)", type=["xls", "xlsx"])

if uploaded_file:
    # --- Load and Clean New Data ---
    df_raw = pd.read_excel(uploaded_file, sheet_name="Lista Operazione", header=None)
    header_row = df_raw.index[df_raw.iloc[:, 0].astype(str).str.contains("Data", na=False)][0]
    
    df_new = pd.read_excel(uploaded_file, sheet_name="Lista Operazione", header=header_row)
    df_new = df_new.rename(columns=lambda x: x.strip())
    df_new = df_new[["Data", "Operazione", "Categoria", "Importo"]].copy()
    
    # Parse types
    df_new["Data"] = pd.to_datetime(df_new["Data"], errors="coerce", dayfirst=True, format="%d-%m-Y%")
    df_new["Importo"] = pd.to_numeric(df_new["Importo"], errors="coerce")
    df_new = df_new.dropna(subset=["Data", "Importo"]).sort_values("Data")
    
    # --- Merge with Master and Drop Duplicates ---
    combined_df = pd.concat([master_df, df_new], ignore_index=True)
    # combined_df = combined_df.drop_duplicates(
    #     subset=["Data", "Operazione", "Importo"], keep="last"
    # ).sort_values("Data").reset_index(drop=True)
    combined_df = combined_df.sort_values("Data").reset_index(drop=True)

    # --- Detect Salary-Based Months ---
    salary_df = combined_df[combined_df["Categoria"] == "Stipendi e pensioni"].copy()
    salary_df["YearMonth"] = salary_df["Data"].dt.to_period("M")
    salary_periods = salary_df.groupby("YearMonth")["Data"].min().sort_values().reset_index(drop=True)

    def assign_personal_month(date):
        past_periods = salary_periods[salary_periods <= date]
        return past_periods.max() if len(past_periods) else pd.NaT

    combined_df["PersonalMonthStart"] = combined_df["Data"].apply(assign_personal_month)
    combined_df["PeriodName"] = combined_df["PersonalMonthStart"].dt.strftime("%Y-%m%b")

    # --------------------------
    # Sidebar Filters
    # --------------------------
    st.sidebar.header("Filters")

    available_periods = (
        combined_df[["PersonalMonthStart", "PeriodName"]]
        .drop_duplicates()
        .dropna()
        .sort_values("PersonalMonthStart")
    )
    period_list = available_periods["PeriodName"].tolist()

    select_all = st.sidebar.checkbox("Select All Periods", value=True)
    selected_periods = st.sidebar.multiselect(
        "Select one or more personal months:",
        options=period_list,
        default=period_list if select_all else period_list[-1:]
    )

    df = combined_df.copy()
    if selected_periods:
        df = df[df["PeriodName"].isin(selected_periods)]
        st.write(f"Showing transactions for **{len(selected_periods)} period(s)**: {', '.join(selected_periods)}")
    else:
        st.write("No periods selected → showing **empty data**.")
        df = df.iloc[0:0]

    # --------------------------
    # Summary Stats
    # --------------------------
    total_expenses = df.loc[df["Importo"] < 0, "Importo"].sum()
    total_income = df.loc[df["Importo"] > 0, "Importo"].sum()
    total_investments = df.loc[(df["Importo"] < 0) & (df["Categoria"].str.contains("Investimenti", case=False, na=False)), "Importo"].sum()
    total_savings = df.loc[(df["Importo"] < 0) & (df["Categoria"].str.contains("Risparmi", case=False, na=False)), "Importo"].sum()

    st.subheader("💰 Summary")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Expenses", f"{total_expenses:,.2f} €")
    col2.metric("Total Income", f"{total_income:,.2f} €")
    col3.metric("Net for Selection", f"{(total_income + total_expenses):,.2f} €")
    col4.metric("Investments", f"{total_investments:,.2f} €")
    col5.metric("Savings", f"{total_savings:,.2f} €")
    # --------------------------
    # Charts
    # --------------------------
    st.subheader("📈 Charts")

    # --------------------------
    # Plotly Pie Chart for Expenses
    # --------------------------
    # Filtered dataframe for charting (exclude Investments and Savings)
    df_charts = df[
        ~df["Categoria"].str.contains("Investimenti", case=False, na=False)
        & ~df["Categoria"].str.contains("Risparmi", case=False, na=False)
    ]

    st.write("### Expenses per Category")
    category_expenses = (
        df_charts.loc[df_charts["Importo"] < 0]
        .groupby("Categoria")["Importo"]
        .sum()
        .abs()
        .sort_values(ascending=False)
    )

    if not category_expenses.empty:
        fig = px.pie(
            category_expenses.reset_index(),
            values="Importo",
            names="Categoria",
            title="Expenses per Category",
            hole=0.1
        )

        # Hide all text labels on slices and show only on hover
        fig.update_traces(
            textinfo='none',  # remove labels completely
            hovertemplate='%{label}: %{value:.2f} € (%{percent})',  # hover shows category, value, and percent
            pull=[0.02 if v/sum(category_expenses) < 0.05 else 0 for v in category_expenses]  # small slice highlight
        )

        # Move legend to the right
        fig.update_layout(
            showlegend=True,
            legend=dict(
                orientation="v",
                yanchor="top",
                y=1,
                xanchor="left",
                x=1.05
            )
        )

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.write("No expenses for this selection.")


    # --- Monthly Summary ---
    monthly_summary = df_charts.groupby("PeriodName")["Importo"].agg(
        Expenses=lambda x: x[x < 0].sum(),
        Income=lambda x: x[x > 0].sum()
    )
    monthly_summary = monthly_summary.reindex(period_list).fillna(0)
    monthly_summary["Net"] = monthly_summary["Income"] + monthly_summary["Expenses"]
    monthly_summary["CumulativeBalance"] = monthly_summary["Net"].cumsum()

    # Line Chart: Net per Period
    st.write("### Monthly Spending Trend (Net per Period)")
    st.line_chart(monthly_summary["Net"])

    # Bar Chart: Income vs Expenses (Streamlit)
    st.write("### Income vs Expenses per Personal Month")
    st.bar_chart(monthly_summary[["Expenses", "Income"]],color=["#E73039FF", "#25C025FF"])

    # --------------------------
    # Transactions Table
    # --------------------------
    st.subheader("📄 Transactions for Selected Periods")

    if df.empty:
        st.write("No transactions for this selection.")
    else:
        # Prepare table
        df_display = df[["Data", "Operazione", "Categoria", "Importo"]].copy()

        # Format date without hours
        df_display["Data"] = df_display["Data"].dt.strftime("%Y/%m/%d")

        # Style negative and positive amounts
        def color_amount(val):
            color = 'red' if val < 0 else 'green'
            return f'color: {color}; font-weight: bold'

        styled_df = df_display.style.format({
            "Importo": "{:,.2f} €"
        }).applymap(color_amount, subset=["Importo"])

        # Display styled dataframe in Streamlit
        st.dataframe(styled_df, use_container_width=True)
    
    # --------------------------
    # Save Updated Master
    # --------------------------
    if st.button("💾 Save Updated Data"):
        combined_df.to_sql(TABLE_NAME, engine, if_exists="replace", index=False)
        st.success("Master data saved successfully!")
        st.success(f"Master data now has {len(combined_df)} total movements.")
