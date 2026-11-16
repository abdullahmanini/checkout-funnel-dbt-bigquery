-- Store KPIs by month, plus the 30-day repeat rate of each month's first-time buyers (the cohort).
with funnel as (
    select * from {{ ref('fct_checkout_funnel') }}
),

orders as (
    select order_id, user_id, min(created_at) as order_at
    from {{ ref('stg_order_items') }}
    where not is_cancelled
    group by order_id, user_id
),

first_orders as (
    select user_id, min(order_at) as first_order_at
    from orders
    group by user_id
),

buyer_repeat as (
    select
        f.user_id,
        any_value(f.first_order_at) as first_order_at,
        logical_or(
            o.order_at > f.first_order_at
            and o.order_at <= timestamp_add(f.first_order_at, interval 30 day)
        ) as repeat_30d
    from first_orders as f
    join orders as o using (user_id)
    group by f.user_id
),

cohorts as (
    select
        date_trunc(date(first_order_at), month) as month,
        count(*) as new_buyers,
        countif(repeat_30d) as new_buyers_repeat_30d
    from buyer_repeat
    group by month
),

traffic as (
    select
        date_trunc(session_date, month) as month,
        count(*) as sessions,
        count(distinct visitor_id) as visitors,
        countif(reached_cart) as cart_sessions,
        countif(purchased) as purchase_sessions,
        count(distinct if(purchased and gmv > 0, order_id, null)) as orders,
        sum(gmv) as gmv
    from funnel
    group by month
)

select
    t.*,
    safe_divide(t.purchase_sessions, t.sessions) as session_conversion_rate,
    safe_divide(t.gmv, t.orders) as aov,
    c.new_buyers,
    -- a cohort needs 30 days of follow-up after its last buyer, otherwise the rate is understated
    if(
        date_add(last_day(t.month), interval 30 day) < date('{{ var("as_of") }}'),
        safe_divide(c.new_buyers_repeat_30d, c.new_buyers),
        null
    ) as new_buyer_repeat_rate_30d
from traffic as t
left join cohorts as c using (month)
