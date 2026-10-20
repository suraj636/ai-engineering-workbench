import pandas as pd
import random
import duckdb

random.seed(42)

def generate_domain_qa(domain_name, db_path, templates_by_tier, output_path):
    qa_list = []
    
    tier_counts = {
        'T1': 20,
        'T2': 25,
        'T3': 20,
        'T4': 25,
        'T5': 10
    }
    
    # Open duckdb connection
    con = duckdb.connect(db_path, read_only=True)
    
    for tier, target_count in tier_counts.items():
        templates = templates_by_tier[tier]
        generated_for_tier = 0
        
        while generated_for_tier < target_count:
            # Pick a random template
            tmpl = random.choice(templates)
            
            try:
                dept = random.choice(["Engineering", "Sales", "HR", "Marketing", "IT"])
                status = random.choice(["Active", "Repair", "Retired"])
                region = random.choice(["NA", "EMEA", "APAC", "LATAM"])
                asset_type = random.choice(["Server", "Router", "Switch", "LoadBalancer"])
                
                q = tmpl['q'].format(dept=dept, status=status, region=region, asset_type=asset_type)
                sql = tmpl['sql'].format(dept=dept, status=status, region=region, asset_type=asset_type)
                judge = tmpl['judge'].format(dept=dept, status=status, region=region, asset_type=asset_type)
                rationale = tmpl['rationale'].format(dept=dept, status=status, region=region, asset_type=asset_type)
                
                # Execute SQL to get expected answer
                result_df = con.execute(sql).df()
                # Convert result to a deterministic string representation
                expected_ans = result_df.to_json(orient='records')
                
                qa_list.append({
                    "natural_language_question": q,
                    "expected_answer": expected_ans,
                    "reference_sql": sql,
                    "reference_tables": tmpl['tables'],
                    "reference_fields": tmpl['fields'],
                    "judge_reference": judge,
                    "derivation_rationale": rationale
                })
                generated_for_tier += 1
            except Exception as e:
                pass
                
    con.close()
    
    df = pd.DataFrame(qa_list)
    assert len(df) == 100, f"Expected 100 QAs, got {len(df)}"
    df.to_csv(output_path, index=False)
    print(f"Generated 100 QA pairs for {domain_name} at {output_path}")

# --- Domain 1: Device Mgmt ---
d1_templates = {
    'T1': [
        {
            "q": "How many users are in the {dept} department?", 
            "sql": "SELECT COUNT(*) FROM users WHERE department = '{dept}';", 
            "tables": "users", "fields": "department",
            "judge": "The system should output the count of users in the {dept} department.",
            "rationale": "Filtered the users table by department={dept} and counted the total rows."
        },
        {
            "q": "What is the total number of {status} devices?", 
            "sql": "SELECT COUNT(*) FROM devices WHERE status = '{status}';", 
            "tables": "devices", "fields": "status",
            "judge": "The system should output the count of devices with status {status}.",
            "rationale": "Filtered the devices table by status={status} and counted the total rows."
        }
    ],
    'T2': [
        {
            "q": "List all {status} devices with their owner's name.", 
            "sql": "SELECT u.name, d.model FROM users u JOIN devices d ON u.user_id = d.user_id WHERE d.status = '{status}';", 
            "tables": "users,devices", "fields": "user_id,name,model,status",
            "judge": "The system should output the names of users and device models for all {status} devices.",
            "rationale": "Performed an INNER JOIN between users and devices on user_id, then filtered by status={status}."
        }
    ],
    'T3': [
        {
            "q": "List all devices and any events associated with them.", 
            "sql": "SELECT d.device_id, e.event_type FROM devices d LEFT JOIN events e ON d.device_id = e.device_id;", 
            "tables": "devices,events", "fields": "device_id,event_type",
            "judge": "The system should output all devices and their events, including devices without any events.",
            "rationale": "Performed a LEFT JOIN from devices to events on device_id to ensure devices without events are included."
        }
    ],
    'T4': [
        {
            "q": "Count the number of events per user.", 
            "sql": "SELECT u.name, COUNT(e.event_id) FROM users u JOIN devices d ON u.user_id = d.user_id JOIN events e ON d.device_id = e.device_id GROUP BY u.name;", 
            "tables": "users,devices,events", "fields": "user_id,device_id,event_id,name",
            "judge": "The system should output a list of users and the total count of events across all their devices.",
            "rationale": "Joined users, devices, and events, then grouped by user name to count total events."
        }
    ],
    'T5': [
        {
            "q": "Which departments have more than 5 users?", 
            "sql": "SELECT department, COUNT(*) FROM users GROUP BY department HAVING COUNT(*) > 5;", 
            "tables": "users", "fields": "department",
            "judge": "The system should output the departments that have a user count greater than 5.",
            "rationale": "Grouped users by department, counted rows, and used HAVING clause to filter out departments with 5 or fewer users."
        }
    ]
}

# --- Domain 2: IT Ops ---
d2_templates = {
    'T1': [
        {
            "q": "What is the total cost of all {asset_type} assets?", 
            "sql": "SELECT SUM(cost) FROM assets WHERE asset_type = '{asset_type}';", 
            "tables": "assets", "fields": "cost,asset_type",
            "judge": "The system should output the total sum cost for all {asset_type} assets.",
            "rationale": "Filtered assets by asset_type={asset_type} and computed the SUM of the cost field."
        }
    ],
    'T2': [
        {
            "q": "Show incidents for {asset_type} assets.", 
            "sql": "SELECT i.incident_id, a.name FROM incidents i JOIN assets a ON i.asset_id = a.asset_id WHERE a.asset_type = '{asset_type}';", 
            "tables": "incidents,assets", "fields": "incident_id,asset_id,name,asset_type",
            "judge": "The system should output the incident IDs and asset names for all incidents involving {asset_type} assets.",
            "rationale": "Joined incidents and assets on asset_id, filtering for asset_type={asset_type}."
        }
    ],
    'T3': [
        {
            "q": "List all incidents and their SLAs if any.", 
            "sql": "SELECT i.incident_id, s.resolution_time_hrs FROM incidents i LEFT JOIN slas s ON i.incident_id = s.incident_id;", 
            "tables": "incidents,slas", "fields": "incident_id,resolution_time_hrs",
            "judge": "The system should list all incidents along with their resolution times, including incidents without an SLA record.",
            "rationale": "Used a LEFT JOIN from incidents to slas to ensure all incidents are retained regardless of SLA presence."
        }
    ],
    'T4': [
        {
            "q": "Count incidents for each asset and team.", 
            "sql": "SELECT a.name, t.team_name, COUNT(i.incident_id) FROM assets a JOIN incidents i ON a.asset_id = i.asset_id JOIN teams t ON i.team_id = t.team_id GROUP BY a.name, t.team_name;", 
            "tables": "assets,incidents,teams", "fields": "asset_id,incident_id,team_id,name,team_name",
            "judge": "The system should output the count of incidents grouped by both asset name and team name.",
            "rationale": "Joined assets, incidents, and teams. Grouped by asset name and team name to compute the incident count."
        }
    ],
    'T5': [
        {
            "q": "Which teams have handled more than 50 incidents?", 
            "sql": "SELECT t.team_name, COUNT(i.incident_id) FROM teams t JOIN incidents i ON t.team_id = i.team_id GROUP BY t.team_name HAVING COUNT(i.incident_id) > 50;", 
            "tables": "teams,incidents", "fields": "team_id,incident_id,team_name",
            "judge": "The system should output the names of teams that have an incident count strictly greater than 50.",
            "rationale": "Grouped incidents by team, counted incidents, and used a HAVING clause to filter out teams with 50 or fewer."
        }
    ]
}

# --- Domain 3: CRM / Sales ---
d3_templates = {
    'T1': [
        {
            "q": "What is the total amount for all {stage} opportunities?",
            "sql": "SELECT SUM(amount) FROM opportunities WHERE stage = '{stage}';",
            "tables": "opportunities", "fields": "amount,stage",
            "judge": "The system should output the sum of the amount column for all opportunities in the {stage} stage.",
            "rationale": "Filtered the opportunities table by stage={stage} and computed the total sum of the amount."
        }
    ],
    'T2': [
        {
            "q": "List all opportunities and their account names for the {stage} stage.",
            "sql": "SELECT o.opp_id, a.name FROM opportunities o JOIN accounts a ON o.account_id = a.account_id WHERE o.stage = '{stage}';",
            "tables": "opportunities,accounts", "fields": "opp_id,account_id,name,stage",
            "judge": "The system should output opportunity IDs and their associated account names for the {stage} stage.",
            "rationale": "Joined opportunities to accounts on account_id, then filtered for stage={stage}."
        }
    ],
    'T3': [
        {
            "q": "Show all accounts and their opportunities, including accounts with no opportunities.",
            "sql": "SELECT a.name, o.opp_id FROM accounts a LEFT JOIN opportunities o ON a.account_id = o.account_id;",
            "tables": "accounts,opportunities", "fields": "account_id,name,opp_id",
            "judge": "The system should output a list of all accounts and their opportunity IDs, including accounts without opportunities.",
            "rationale": "Performed a LEFT JOIN from accounts to opportunities to ensure all accounts are represented."
        }
    ],
    'T4': [
        {
            "q": "Count the number of activities logged by each representative for each account.",
            "sql": "SELECT ac.name, a.rep_name, COUNT(a.activity_id) FROM accounts ac JOIN opportunities o ON ac.account_id = o.account_id JOIN activities a ON o.opp_id = a.opp_id GROUP BY ac.name, a.rep_name;",
            "tables": "accounts,opportunities,activities", "fields": "account_id,opp_id,activity_id,name,rep_name",
            "judge": "The system should output the count of activities grouped by account name and representative name.",
            "rationale": "Joined accounts, opportunities, and activities, then grouped by account name and rep name to count activities."
        }
    ],
    'T5': [
        {
            "q": "Which representatives have logged more than 20 activities?",
            "sql": "SELECT rep_name, COUNT(activity_id) FROM activities GROUP BY rep_name HAVING COUNT(activity_id) > 20;",
            "tables": "activities", "fields": "rep_name,activity_id",
            "judge": "The system should output the names of representatives who have logged strictly more than 20 activities.",
            "rationale": "Grouped activities by rep_name, counted total activities, and filtered using HAVING count > 20."
        }
    ]
}

# --- Domain 4: E-Commerce ---
d4_templates = {
    'T1': [
        {
            "q": "How many customers are in the {region} region?", 
            "sql": "SELECT COUNT(*) FROM customers WHERE region = '{region}';", 
            "tables": "customers", "fields": "region",
            "judge": "The system should output the count of customers located in the {region} region.",
            "rationale": "Filtered customers by region={region} and counted the total rows."
        }
    ],
    'T2': [
        {
            "q": "List all orders for customers in {region}.", 
            "sql": "SELECT o.order_id, c.name FROM orders o JOIN customers c ON o.customer_id = c.customer_id WHERE c.region = '{region}';", 
            "tables": "orders,customers", "fields": "order_id,customer_id,name,region",
            "judge": "The system should output the order IDs and customer names for orders placed by customers in the {region} region.",
            "rationale": "Joined orders and customers on customer_id, filtering for region={region}."
        }
    ],
    'T3': [
        {
            "q": "Show all customers and their orders, even if they have none.", 
            "sql": "SELECT c.name, o.order_id FROM customers c LEFT JOIN orders o ON c.customer_id = o.customer_id;", 
            "tables": "customers,orders", "fields": "customer_id,name,order_id",
            "judge": "The system should output a list of all customers and their order IDs, including customers who haven't placed any orders.",
            "rationale": "Used a LEFT JOIN from customers to orders to retain customers with zero orders."
        }
    ],
    'T4': [
        {
            "q": "What is the total revenue for each product?", 
            "sql": "SELECT p.name, SUM(p.price * i.quantity) FROM products p JOIN items i ON p.product_id = i.product_id JOIN orders o ON i.order_id = o.order_id GROUP BY p.name;", 
            "tables": "products,items,orders", "fields": "product_id,item_id,order_id,price,quantity,name",
            "judge": "The system should output the name of each product along with its total computed revenue across all orders.",
            "rationale": "Joined products, items, and orders. Grouped by product name and summed the product of price and quantity."
        }
    ],
    'T5': [
        {
            "q": "Which customers have placed more than 3 orders?", 
            "sql": "SELECT c.name, COUNT(o.order_id) FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.name HAVING COUNT(o.order_id) > 3;", 
            "tables": "customers,orders", "fields": "customer_id,order_id,name",
            "judge": "The system should output the names of customers who have placed strictly more than 3 orders.",
            "rationale": "Grouped orders by customer name, counted orders, and applied a HAVING clause to filter out those with 3 or fewer."
        }
    ]
}

# --- Domain 5: Finance / GL ---
d5_templates = {
    'T1': [
        {
            "q": "What is the total sum of all ledger entries for {region}?",
            "sql": "SELECT SUM(amount) FROM ledger_entries;",
            "tables": "ledger_entries", "fields": "amount",
            "judge": "The system should output the sum of the amount column for all ledger entries.",
            "rationale": "Computed the total sum of the amount in ledger_entries."
        }
    ],
    'T2': [
        {
            "q": "List all ledger entries for {region} along with the account names.",
            "sql": "SELECT l.entry_id, a.name FROM ledger_entries l JOIN accounts a ON l.account_id = a.account_id;",
            "tables": "ledger_entries,accounts", "fields": "entry_id,account_id,name",
            "judge": "The system should output entry IDs and their associated account names.",
            "rationale": "Joined ledger_entries to accounts on account_id."
        }
    ],
    'T3': [
        {
            "q": "Show all transactions and their ledger entries, including transactions with no entries.",
            "sql": "SELECT t.transaction_id, l.entry_id FROM transactions t LEFT JOIN ledger_entries l ON t.transaction_id = l.transaction_id;",
            "tables": "transactions,ledger_entries", "fields": "transaction_id,entry_id",
            "judge": "The system should output a list of all transactions and their entry IDs, including transactions without entries.",
            "rationale": "Performed a LEFT JOIN from transactions to ledger_entries to ensure all transactions are represented."
        }
    ],
    'T4': [
        {
            "q": "Count the number of ledger entries per account per transaction date.",
            "sql": "SELECT a.name, t.date, COUNT(l.entry_id) FROM accounts a JOIN ledger_entries l ON a.account_id = l.account_id JOIN transactions t ON l.transaction_id = t.transaction_id GROUP BY a.name, t.date;",
            "tables": "accounts,ledger_entries,transactions", "fields": "account_id,entry_id,transaction_id,name,date",
            "judge": "The system should output the count of ledger entries grouped by account name and transaction date.",
            "rationale": "Joined accounts, ledger_entries, and transactions, then grouped by account name and date to count entries."
        }
    ],
    'T5': [
        {
            "q": "Which accounts have more than 10 ledger entries?",
            "sql": "SELECT a.name, COUNT(l.entry_id) FROM accounts a JOIN ledger_entries l ON a.account_id = l.account_id GROUP BY a.name HAVING COUNT(l.entry_id) > 10;",
            "tables": "accounts,ledger_entries", "fields": "account_id,entry_id,name",
            "judge": "The system should output the names of accounts that have strictly more than 10 ledger entries.",
            "rationale": "Grouped ledger_entries by account_name, counted total entries, and filtered using HAVING count > 10."
        }
    ]
}

if __name__ == "__main__":
    generate_domain_qa("Domain 1: Device Mgmt", "datasets/domain_1_device_mgmt/db.duckdb", d1_templates, "datasets/domain_1_device_mgmt/qa_pairs.csv")
    generate_domain_qa("Domain 2: IT Ops", "datasets/domain_2_it_ops/db.duckdb", d2_templates, "datasets/domain_2_it_ops/qa_pairs.csv")
    generate_domain_qa("Domain 3: CRM / Sales", "datasets/domain_3_crm_sales/db.duckdb", d3_templates, "datasets/domain_3_crm_sales/qa_pairs.csv")
    generate_domain_qa("Domain 4: E-Commerce", "datasets/domain_4_ecommerce/db.duckdb", d4_templates, "datasets/domain_4_ecommerce/qa_pairs.csv")
    generate_domain_qa("Domain 5: Finance / GL", "datasets/domain_5_finance_gl/db.duckdb", d5_templates, "datasets/domain_5_finance_gl/qa_pairs.csv")
