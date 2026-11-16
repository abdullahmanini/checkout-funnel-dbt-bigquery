-- One row per session with the furthest funnel step reached, step timings and net GMV.
with sessions as (
    select * from {{ ref('int_session_steps') }}
),

orders as (
    select * from {{ ref('int_session_orders') }}
),

products as (
    select * from {{ ref('stg_products') }}
)

select
    s.session_id,
    s.visitor_id,
    s.user_id,
    s.is_anonymous,
    s.browser,
    s.traffic_source,
    s.session_start_at,
    date(s.session_start_at) as session_date,
    fp.category,
    fp.department,
    fp.brand,
    s.first_product_at is not null as reached_product,
    s.first_cart_at is not null as reached_cart,
    s.purchase_at is not null as purchased,
    case
        when s.purchase_at is not null then 'purchase'
        when s.first_cart_at is not null then 'cart'
        when s.first_product_at is not null then 'product'
        else 'browse'
    end as furthest_step,
    s.first_cart_at,
    s.purchase_at,
    timestamp_diff(s.first_cart_at, s.first_product_at, second) as seconds_product_to_cart,
    timestamp_diff(s.purchase_at, s.first_cart_at, minute) as minutes_cart_to_purchase,
    s.returns_to_browse_after_cart,
    s.carted_product_id,
    cp.retail_price as carted_price,
    o.order_item_id,
    o.order_id,
    o.status as order_status,
    coalesce(if(o.is_cancelled, 0, o.sale_price), 0) as gmv
from sessions as s
left join products as fp on fp.product_id = s.first_product_id
left join products as cp on cp.product_id = s.carted_product_id
left join orders as o on o.session_id = s.session_id
