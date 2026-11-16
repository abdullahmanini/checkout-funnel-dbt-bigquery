-- thelook has no session_id on orders. A purchase session is matched to the order line from the
-- same user, for a product viewed in that session, with the closest timestamp.
with sessions as (
    select session_id, user_id, purchase_at, viewed_product_ids
    from {{ ref('int_session_steps') }}
    where purchase_at is not null
),

items as (
    select * from {{ ref('stg_order_items') }}
)

select
    s.session_id,
    i.order_item_id,
    i.order_id,
    i.product_id,
    i.status,
    i.sale_price,
    i.is_cancelled,
    i.is_returned,
    abs(timestamp_diff(i.created_at, s.purchase_at, second)) as match_gap_seconds
from sessions as s
join items as i
    on i.user_id = s.user_id
    and i.product_id in unnest(s.viewed_product_ids)
qualify row_number() over (
    partition by s.session_id
    order by abs(timestamp_diff(i.created_at, s.purchase_at, second)), i.order_item_id
) = 1
