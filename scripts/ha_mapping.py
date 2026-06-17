from model import AccountData


def _latest_daily(account_data: AccountData):
    rows = [row for row in account_data.daily if row.date]
    return max(rows, key=lambda row: row.date) if rows else None


def _latest_monthly(account_data: AccountData):
    rows = [row for row in account_data.monthly if row.year_month]
    return max(rows, key=lambda row: row.year_month) if rows else None


def account_data_summary(account_data: AccountData) -> str:
    return (
        f"balance={'yes' if account_data.balance else 'no'}, "
        f"daily={len(account_data.daily)}, monthly={len(account_data.monthly)}, "
        f"yearly={'yes' if account_data.yearly else 'no'}"
    )


def account_data_to_update_args(account_data: AccountData) -> dict:
    user_id = account_data.account.account_no
    balance_model = account_data.balance
    yearly = account_data.yearly
    latest_month = _latest_monthly(account_data)
    latest_day = _latest_daily(account_data)

    balance = None
    enhanced_balance = None
    if balance_model is not None:
        balance = balance_model.balance_cny
        if balance is None:
            balance = balance_model.prepay_balance_cny
        if balance_model.arrears_cny is not None:
            enhanced_balance = {
                "as_of": balance_model.observed_at,
                "amount_due": balance_model.arrears_cny,
                "user_id": user_id,
            }

    tou_daily = []
    for row in account_data.daily:
        tou_daily.append({
            "date": row.date,
            "total_usage": row.total_usage_kwh,
            "valley_usage": row.valley_usage_kwh,
            "flat_usage": row.flat_usage_kwh,
            "peak_usage": row.peak_usage_kwh,
            "tip_usage": row.tip_usage_kwh,
        })
    tou_data = {
        "year": yearly.year if yearly else "",
        "yearly_usage": yearly.total_usage_kwh if yearly else None,
        "yearly_charge": yearly.total_charge_cny if yearly else None,
        "months": [
            {
                "month": row.year_month,
                "usage": row.total_usage_kwh,
                "charge": row.total_charge_cny,
                "begin_date": row.begin_date,
                "end_date": row.end_date,
            }
            for row in account_data.monthly
        ],
        "daily": tou_daily,
    } if tou_daily else None

    return {
        "user_id": user_id,
        "balance": balance,
        "last_daily_date": latest_day.date if latest_day else None,
        "last_daily_usage": latest_day.total_usage_kwh if latest_day else None,
        "yearly_charge": yearly.total_charge_cny if yearly else None,
        "yearly_usage": yearly.total_usage_kwh if yearly else None,
        "month_charge": latest_month.total_charge_cny if latest_month else None,
        "month_usage": latest_month.total_usage_kwh if latest_month else None,
        "tou_data": tou_data,
        "enhanced_balance": enhanced_balance,
    }
