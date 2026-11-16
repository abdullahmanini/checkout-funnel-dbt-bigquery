-- Simulated A/B test: "guest express checkout" lets a shopper pay without signing in.
-- Assignment is a salted hash of the visitor, so it is stable and 50/50 in expectation.
-- Real outcomes come from thelook. The only invented part is the treatment effect:
-- a fixed share of treatment-arm anonymous carts (picked by hash) become guest orders at the
-- carted product's list price minus a welcome discount. would_recover is computed for both
-- arms, so the true effect is known and the estimate can be checked against it.
{% set salt = var('experiment_salt') %}

with funnel as (
    select * from {{ ref('fct_checkout_funnel') }}
),

window_sessions as (
    select
        *,
        if(mod(abs(farm_fingerprint(concat('{{ salt }}', visitor_id))), 2) = 0, 'treatment', 'control') as arm,
        is_anonymous
            and reached_cart
            and mod(abs(farm_fingerprint(concat('{{ salt }}-recover-', session_id))), 10000)
                < {{ var('guest_recovery_rate') }} * 10000 as would_recover
    from funnel
    where session_date between '{{ var("experiment_start") }}' and '{{ var("experiment_end") }}'
),

outcomes as (
    select
        *,
        arm = 'treatment' and would_recover as guest_order,
        if(would_recover, round(carted_price * (1 - {{ var('guest_discount') }}), 2), 0) as potential_guest_revenue,
        case
            when purchased then gmv
            when arm = 'treatment' and would_recover then round(carted_price * (1 - {{ var('guest_discount') }}), 2)
            else 0
        end as revenue
    from window_sessions
),

visitors as (
    select
        visitor_id,
        any_value(arm) as arm,
        logical_or(is_anonymous) as is_anonymous,
        array_agg(browser order by session_start_at limit 1)[offset(0)] as browser,
        array_agg(traffic_source order by session_start_at limit 1)[offset(0)] as traffic_source,
        count(*) as sessions,
        logical_or(reached_cart) as reached_cart,
        logical_or(purchased or guest_order) as converted,
        -- cancelled orders count toward conversion but not toward orders or revenue
        count(distinct if(purchased and gmv > 0, order_id, null)) + countif(guest_order) as orders,
        countif(guest_order) as guest_orders,
        countif(purchased or guest_order) as items,
        sum(revenue) as revenue,
        min(if(purchased, purchase_at, if(guest_order, first_cart_at, null))) as first_order_at,
        array_agg(if(purchased, order_id, null) ignore nulls order by purchase_at limit 1)[safe_offset(0)] as first_order_id,
        logical_or(would_recover) as would_recover,
        sum(potential_guest_revenue) as potential_guest_revenue
    from outcomes
    group by visitor_id
),

-- Guest buyers have no account, so a later order cannot be linked to them.
repeat_orders as (
    select v.visitor_id, count(distinct f.order_id) > 0 as repeat_30d
    from visitors as v
    join funnel as f
        on f.visitor_id = v.visitor_id
        and f.purchased
        and f.order_id != v.first_order_id
        and f.purchase_at > v.first_order_at
        and f.purchase_at <= timestamp_add(v.first_order_at, interval 30 day)
    group by v.visitor_id
)

select
    v.*,
    if(v.converted, coalesce(r.repeat_30d, false), null) as repeat_30d
from visitors as v
left join repeat_orders as r using (visitor_id)
