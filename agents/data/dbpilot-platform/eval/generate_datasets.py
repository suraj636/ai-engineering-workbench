import pandas as pd
import numpy as np
from faker import Faker
import os
import duckdb

def save_and_load(output_dir, dataframes, db_name="db.duckdb"):
    os.makedirs(output_dir, exist_ok=True)
    db_path = f"{output_dir}/{db_name}"
    if os.path.exists(db_path):
        os.remove(db_path)
    
    con = duckdb.connect(db_path)
    
    for table_name, df in dataframes.items():
        csv_path = f"{output_dir}/{table_name}.csv"
        df.to_csv(csv_path, index=False)
        con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM read_csv_auto('{csv_path}');")
        
    con.close()
    print(f"Generated Database in {output_dir}")

def generate_device_management(output_dir="datasets/domain_1_device_mgmt", seed_val=42):
    Faker.seed(seed_val)
    rng = np.random.default_rng(seed=seed_val)
    fake = Faker()
    
    # Users
    users = pd.DataFrame([{
        "user_id": i,
        "name": fake.name(),
        "department": rng.choice(["Engineering", "Sales", "HR", "Marketing", "IT"]),
        "is_active": rng.choice([True, False], p=[0.9, 0.1])
    } for i in range(1, 101)])
    
    # Devices (with ~1% duplicates)
    devices = [{"device_id": i, "user_id": rng.choice(users["user_id"]), "model": rng.choice(["MacBook Pro", "ThinkPad T14", "Dell XPS", "iPhone 13"]), "purchase_price": round(rng.pareto(1.16) * 1000 + 500, 2), "status": rng.choice(["Active", "Repair", "Retired"])} for i in range(1, 251)]
    devices.extend(rng.choice(devices, size=3).tolist()) # inject duplicates
    devices_df = pd.DataFrame(devices)
    
    # Policies (missing assignments handled via relation logic)
    policies = pd.DataFrame([{
        "policy_id": i,
        "name": fake.bs().title(),
        "strictness": rng.choice(["High", "Medium", "Low"])
    } for i in range(1, 11)])
    
    # Events
    events = pd.DataFrame([{
        "event_id": i,
        "device_id": rng.choice(devices_df["device_id"].unique()),
        "policy_id": rng.choice(policies["policy_id"].tolist() + [np.nan], p=[0.08]*10 + [0.2]), # Missing assignments
        "event_type": rng.choice(["Login", "Update", "Crash", "SecurityAlert"]),
        "timestamp": fake.date_time_between(start_date="-1y", end_date="now")
    } for i in range(1, 801)])
    
    # Alerts
    alerts = pd.DataFrame([{
        "alert_id": i,
        "user_id": rng.choice(users["user_id"]),
        "severity": rng.choice(["Critical", "Warning", "Info"]),
        "timestamp": fake.date_time_between(start_date="-1y", end_date="now")
    } for i in range(1, 301)])
    
    save_and_load(output_dir, {"users": users, "devices": devices_df, "policies": policies, "events": events, "alerts": alerts})

def generate_it_ops(output_dir="datasets/domain_2_it_ops", seed_val=101):
    Faker.seed(seed_val)
    rng = np.random.default_rng(seed=seed_val)
    fake = Faker()
    
    # Teams
    teams = pd.DataFrame([{"team_id": i, "team_name": f"{t} Support", "shift": rng.choice(["Day", "Night", "Swing"])} for i, t in enumerate(["L1", "L2", "L3", "Network", "Database"], 1)])
    
    # Assets
    assets = pd.DataFrame([{
        "asset_id": i,
        "name": fake.domain_word(),
        "asset_type": rng.choice(["Server", "Router", "Switch", "LoadBalancer"]),
        "cost": round(rng.uniform(1000, 20000), 2)
    } for i in range(1, 151)])
    
    # Incidents (with duplicates)
    incidents = [{"incident_id": i, "asset_id": rng.choice(assets["asset_id"]), "team_id": rng.choice(teams["team_id"]), "description": fake.sentence(), "status": rng.choice(["Open", "Closed", "InProgress"])} for i in range(1, 501)]
    incidents.extend(rng.choice(incidents, size=5).tolist())
    incidents_df = pd.DataFrame(incidents)
    
    # SLAs (missing SLA records)
    slas = pd.DataFrame([{
        "sla_id": i,
        "incident_id": rng.choice(incidents_df["incident_id"].unique()),
        "resolution_time_hrs": round(rng.exponential(4), 1),
        "met_sla": rng.choice([True, False], p=[0.8, 0.2])
    } for i in range(1, 401)]) # Intentionally fewer than incidents
    
    # Change Requests
    crs = pd.DataFrame([{
        "cr_id": i,
        "asset_id": rng.choice(assets["asset_id"]),
        "status": rng.choice(["Approved", "Pending", "Rejected"])
    } for i in range(1, 201)])
    
    save_and_load(output_dir, {"teams": teams, "assets": assets, "incidents": incidents_df, "slas": slas, "change_requests": crs})

def generate_ecommerce(output_dir="datasets/domain_4_ecommerce", seed_val=202):
    Faker.seed(seed_val)
    rng = np.random.default_rng(seed=seed_val)
    fake = Faker()
    
    # Customers
    customers = pd.DataFrame([{
        "customer_id": i,
        "name": fake.name(),
        "region": rng.choice(["NA", "EMEA", "APAC", "LATAM"]),
        "signup_date": fake.date_between(start_date="-3y", end_date="today")
    } for i in range(1, 501)])
    
    # Products (missing prices)
    products_data = []
    for i in range(1, 201):
        price = round(rng.uniform(10, 500), 2)
        if rng.random() < 0.05:
            price = np.nan # Missing prices
        products_data.append({"product_id": i, "name": fake.ecommerce_name() if hasattr(fake, 'ecommerce_name') else fake.word().title(), "category": rng.choice(["Electronics", "Apparel", "Home", "Toys"]), "price": price})
    products = pd.DataFrame(products_data)
    
    # Orders
    orders = pd.DataFrame([{
        "order_id": i,
        "customer_id": rng.choice(customers["customer_id"]),
        "order_date": fake.date_time_between(start_date="-1y", end_date="now"),
        "total_amount": round(rng.uniform(20, 2000), 2)
    } for i in range(1, 1001)])
    
    # Items (duplicate order line items)
    items = [{"item_id": i, "order_id": rng.choice(orders["order_id"]), "product_id": rng.choice(products["product_id"]), "quantity": rng.integers(1, 5)} for i in range(1, 3001)]
    items.extend(rng.choice(items, size=30).tolist())
    items_df = pd.DataFrame(items)
    
    save_and_load(output_dir, {"customers": customers, "products": products, "orders": orders, "items": items_df})

def generate_crm(output_dir="datasets/domain_3_crm_sales", seed_val=303):
    Faker.seed(seed_val)
    rng = np.random.default_rng(seed=seed_val)
    fake = Faker()
    
    # Accounts
    accounts = pd.DataFrame([{
        "account_id": i,
        "name": fake.company(),
        "industry": rng.choice(["Technology", "Healthcare", "Finance", "Retail"]),
        "annual_revenue": round(rng.uniform(50000, 5000000), 2)
    } for i in range(1, 151)])
    
    # Contacts (duplicate entries)
    contacts = [{"contact_id": i, "account_id": rng.choice(accounts["account_id"]), "name": fake.name(), "email": fake.email()} for i in range(1, 301)]
    contacts.extend(rng.choice(contacts, size=15).tolist())
    contacts_df = pd.DataFrame(contacts)
    
    # Opportunities (NULL close dates)
    opps_data = []
    for i in range(1, 401):
        stage = rng.choice(["Prospecting", "Qualification", "Proposal", "Negotiation", "Closed Won", "Closed Lost"])
        close_date = fake.date_between(start_date="-1y", end_date="+3m")
        if stage not in ["Closed Won", "Closed Lost"] and rng.random() < 0.2:
            close_date = np.nan
        opps_data.append({
            "opp_id": i,
            "account_id": rng.choice(accounts["account_id"]),
            "amount": round(rng.uniform(1000, 100000), 2),
            "stage": stage,
            "close_date": close_date
        })
    opps = pd.DataFrame(opps_data)
    
    # Activities
    activities = pd.DataFrame([{
        "activity_id": i,
        "opp_id": rng.choice(opps["opp_id"]),
        "rep_name": fake.name(),
        "type": rng.choice(["Call", "Email", "Meeting"]),
        "date": fake.date_time_between(start_date="-6m", end_date="now")
    } for i in range(1, 1001)])
    
    save_and_load(output_dir, {"accounts": accounts, "contacts": contacts_df, "opportunities": opps, "activities": activities})

def generate_finance(output_dir="datasets/domain_5_finance_gl", seed_val=505):
    Faker.seed(seed_val)
    rng = np.random.default_rng(seed=seed_val)
    fake = Faker()
    
    # Accounts
    accounts = pd.DataFrame([{
        "account_id": i,
        "name": fake.bs().title(),
        "type": rng.choice(["Asset", "Liability", "Equity", "Revenue", "Expense"]),
        "currency": rng.choice(["USD", "EUR", "GBP", "JPY"])
    } for i in range(1, 51)])
    
    # FX Rates (NULL FX rates)
    fx_data = []
    for cur in ["EUR", "GBP", "JPY"]:
        for d in pd.date_range(start="2023-01-01", end="2023-12-31"):
            rate = round(rng.uniform(0.5, 1.5) if cur != "JPY" else rng.uniform(100, 150), 4)
            if rng.random() < 0.05:
                rate = np.nan
            fx_data.append({"date": d.date(), "currency": cur, "rate_to_usd": rate})
    # Add USD baseline
    for d in pd.date_range(start="2023-01-01", end="2023-12-31"):
        fx_data.append({"date": d.date(), "currency": "USD", "rate_to_usd": 1.0})
    fx_rates = pd.DataFrame(fx_data)
    
    # Transactions (boundary-date transactions)
    transactions = pd.DataFrame([{
        "transaction_id": i,
        "date": fake.date_between(start_date="-1y", end_date="today") if rng.random() > 0.02 else pd.to_datetime("2023-12-31 23:59:59").date(),
        "description": fake.sentence()
    } for i in range(1, 501)])
    
    # Ledger Entries
    ledgers = pd.DataFrame([{
        "entry_id": i,
        "transaction_id": rng.choice(transactions["transaction_id"]),
        "account_id": rng.choice(accounts["account_id"]),
        "amount": round(rng.normal(0, 5000), 2)
    } for i in range(1, 1501)])
    
    save_and_load(output_dir, {"accounts": accounts, "fx_rates": fx_rates, "transactions": transactions, "ledger_entries": ledgers})

if __name__ == "__main__":
    generate_device_management()
    generate_it_ops()
    generate_ecommerce()
    generate_crm()
    generate_finance()
