-- thelook is one store, so each brand stands in for a merchant. Trailing 12 months to as_of.
with items as (
    select i.*, p.brand, p.category
    from {{ ref('stg_order_items') }} as i
    join {{ ref('stg_products') }} as p using (product_id)
    where i.created_at >= timestamp_sub(timestamp('{{ var("as_of") }}'), interval 365 day)
),

sales as (
    select
        brand,
        count(distinct if(not is_cancelled, order_id, null)) as orders,
        sum(if(is_cancelled, 0, sale_price)) as gmv,
        safe_divide(countif(is_returned), countif(not is_cancelled)) as return_rate
    from items
    group by brand
),

brand_first as (
    select
        brand,
        user_id,
        min(created_at) as first_at,
        array_agg(order_id order by created_at limit 1)[offset(0)] as first_order_id
    from items
    where not is_cancelled
    group by brand, user_id
),

buyer_repeat as (
    select
        b.brand,
        b.user_id,
        logical_or(
            i.created_at > b.first_at
            and i.created_at <= timestamp_add(b.first_at, interval 30 day)
            and i.order_id != b.first_order_id
        ) as repeat_30d
    from brand_first as b
    join items as i
        on i.brand = b.brand and i.user_id = b.user_id and not i.is_cancelled
    -- only buyers with a full 30 days of follow-up before as_of
    where b.first_at <= timestamp_sub(timestamp('{{ var("as_of") }}'), interval 30 day)
    group by b.brand, b.user_id
),

brand_repeat as (
    select brand, count(*) as buyers, countif(repeat_30d) as repeat_buyers_30d
    from buyer_repeat
    group by brand
),

traffic as (
    select
        brand,
        count(*) as sessions,
        safe_divide(countif(reached_cart), count(*)) as cart_rate,
        safe_divide(countif(purchased), count(*)) as conversion_rate
    from {{ ref('fct_checkout_funnel') }}
    where session_start_at >= timestamp_sub(timestamp('{{ var("as_of") }}'), interval 365 day)
    group by brand
)

select
    s.brand,
    s.orders,
    s.gmv,
    safe_divide(s.gmv, s.orders) as aov,
    s.return_rate,
    t.sessions,
    t.cart_rate,
    t.conversion_rate,
    r.buyers,
    safe_divide(r.repeat_buyers_30d, r.buyers) as repeat_rate_30d
from sales as s
left join traffic as t using (brand)
left join brand_repeat as r using (brand)
where s.brand is not null
