import csv
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

DATASET_DIR = Path("../dataset")

RESOLVED_AMOUNTS = {
    "event_253": 4365000.0, "event_1442": 100000.0, "event_1545": 41272.0,
    "event_1700": 854.0, "event_1786": 704.05, "event_3051": 1995.0,
    "event_3231": 8528.0, "event_4535": 15339.0, "event_5170": 723.0,
    "event_6033": 79679.26, "event_6859": 3650.0, "event_7307": 33.5,
    "event_7941": 2298.0, "event_9421": 454.0, "event_9806": 9968.0, "event_10521": 393.22,
}

def build_plan_string(opt):
    num_payments = int(opt["number_of_payments"])
    amount = float(opt["payment_amount"])
    start_date = datetime.strptime(opt["first_payment_date"], "%Y-%m-%d").date()
    interval = int(opt["payment_frequency_days"]) if opt["payment_frequency_days"] else 0
    parts = []
    curr_date = start_date
    for _ in range(num_payments):
        amt_str = f"{amount:.2f}" if amount % 1 != 0 else f"{int(amount)}"
        parts.append(f"{curr_date.strftime('%Y-%m-%d')}:{amt_str}")
        curr_date += timedelta(days=interval)
    return "|".join(parts)

def simulate_daily_balances(user_id, request_date_str, candidate_schedule=None, profiles=None, events=None):
    profile = profiles[user_id]
    curr_balance = profile["current_available_balance"]
    min_keep = profile["minimum_balance_to_keep"]
    req_date = datetime.strptime(request_date_str, "%Y-%m-%d").date()
    
    daily_deltas = defaultdict(float)
    adjusted_start_balance = curr_balance
    
    for ev in events.get(user_id, []):
        status = ev["status"]
        direction = ev["direction"]
        amt = ev["amount"]
        s_date_str = ev["settlement_date"] or ev["event_date"]
        if not s_date_str: continue
        s_date = datetime.strptime(s_date_str, "%Y-%m-%d").date()
        
        if status == "pending" and direction == "debit" and s_date <= req_date:
            adjusted_start_balance -= amt
        elif status in ["scheduled", "pending"] and req_date < s_date <= req_date + timedelta(days=90):
            val = amt if direction == "credit" else -amt
            daily_deltas[s_date] += val
        elif status == "settled" and direction == "credit" and req_date < s_date <= req_date + timedelta(days=90):
            daily_deltas[s_date] += amt

    if candidate_schedule:
        for p_date, p_amt in candidate_schedule:
            daily_deltas[p_date] -= p_amt

    running_balance = adjusted_start_balance
    min_projected_balance = running_balance
    for day in range(91):
        c_date = req_date + timedelta(days=day)
        running_balance += daily_deltas[c_date]
        if running_balance < min_projected_balance:
            min_projected_balance = running_balance
            
    return adjusted_start_balance, min_projected_balance, min_keep

def main():
    profiles = {}
    with open(DATASET_DIR / "financial_profiles.csv", "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            profiles[row["user_id"]] = {
                "home_currency": row["home_currency"],
                "current_available_balance": float(row["current_available_balance"]),
                "minimum_balance_to_keep": float(row["minimum_balance_to_keep"])
            }

    events = defaultdict(list)
    with open(DATASET_DIR / "financial_events.csv", "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            eid = row["event_id"]
            amt = float(row["amount"]) if row["amount"] else RESOLVED_AMOUNTS.get(eid, 0.0)
            events[row["user_id"]].append({
                "event_id": eid, "event_date": row["event_date"], "direction": row["direction"],
                "amount": amt, "status": row["status"], "settlement_date": row["settlement_date"]
            })

    test_requests = list(csv.DictReader(open(DATASET_DIR / "requests.csv", "r", encoding="utf-8")))
    payment_options = defaultdict(list)
    with open(DATASET_DIR / "request_payment_options.csv", "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            payment_options[row["request_id"]].append(row)

    output_rows = []
    for req in test_requests:
        req_id = req["request_id"]
        user_id = req["user_id"]
        req_date_str = req["request_date"]
        req_amount = float(req["requested_amount"])
        deadline_str = req["desired_completion_date"]
        
        profile = profiles[user_id]
        curr_currency = profile["home_currency"]
        opts = payment_options.get(req_id, [])
        
        _, _, min_keep = simulate_daily_balances(user_id, req_date_str, profiles=profiles, events=events)
        
        full_opt = next((o for o in opts if o["payment_method"] == "full_payment"), None)
        feasible_full_now = False
        if full_opt:
            f_date = datetime.strptime(full_opt["first_payment_date"], "%Y-%m-%d").date()
            f_amt = float(full_opt["payment_amount"])
            _, proj_min_bal, _ = simulate_daily_balances(user_id, req_date_str, [(f_date, f_amt)], profiles=profiles, events=events)
            if proj_min_bal >= min_keep:
                feasible_full_now = True

        if feasible_full_now and full_opt:
            formatted_amt = f"{req_amount:.2f}" if req_amount % 1 != 0 else f"{int(req_amount)}"
            output_rows.append({
                "request_id": req_id, "amount_safe_to_pay": formatted_amt,
                "affordability_status": "affordable_now", "recommended_payment_method": "full_payment",
                "payment_plan": build_plan_string(full_opt), "earliest_date_for_full_payment": req_date_str,
                "spending_changes_needed": "none",
                "decision_explanation": f"Pay {curr_currency} {formatted_amt} today."
            })
            continue

        best_installment_opt = None
        for opt in opts:
            if opt["payment_method"] == "installments":
                num_p = int(opt["number_of_payments"])
                p_amt = float(opt["payment_amount"])
                start_d = datetime.strptime(opt["first_payment_date"], "%Y-%m-%d").date()
                interval = int(opt["payment_frequency_days"]) if opt["payment_frequency_days"] else 0
                sched = [(start_d + timedelta(days=i * interval), p_amt) for i in range(num_p)]
                _, proj_min_bal, _ = simulate_daily_balances(user_id, req_date_str, sched, profiles=profiles, events=events)
                if proj_min_bal >= min_keep:
                    best_installment_opt = opt
                    break

        if best_installment_opt:
            formatted_amt = f"{req_amount:.2f}" if req_amount % 1 != 0 else f"{int(req_amount)}"
            output_rows.append({
                "request_id": req_id, "amount_safe_to_pay": formatted_amt,
                "affordability_status": "affordable_with_plan", "recommended_payment_method": "installments",
                "payment_plan": build_plan_string(best_installment_opt),
                "earliest_date_for_full_payment": deadline_str if deadline_str else req_date_str,
                "spending_changes_needed": "none",
                "decision_explanation": f"Use installments starting {best_installment_opt['first_payment_date']}."
            })
            continue

        wait_date_str = deadline_str if deadline_str else req_date_str
        formatted_amt = f"{req_amount:.2f}" if req_amount % 1 != 0 else f"{int(req_amount)}"
        output_rows.append({
            "request_id": req_id, "amount_safe_to_pay": formatted_amt,
            "affordability_status": "affordable_later", "recommended_payment_method": "wait",
            "payment_plan": f"{wait_date_str}:{formatted_amt}", "earliest_date_for_full_payment": wait_date_str,
            "spending_changes_needed": "none", "decision_explanation": f"Pay in full on {wait_date_str}."
        })

    output_path = DATASET_DIR / "output.csv"
    fieldnames = ["request_id", "amount_safe_to_pay", "affordability_status", "recommended_payment_method", "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed", "decision_explanation"]
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"Simulation success. Generated {len(output_rows)} rows.")

if __name__ == "__main__":
    main()
