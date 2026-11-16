-- One row per order line. thelook prices are all USD, so no currency conversion is needed.
select
    oi.id as order_item_id,
    oi.order_id,
    oi.user_id,
    oi.product_id,
    lower(oi.status) as status,
    oi.created_at,
    round(cast(oi.sale_price as numeric), 2) as sale_price,
    oi.status = 'Cancelled' as is_cancelled,
    oi.status = 'Returned' as is_returned
from {{ source('thelook', 'order_items') }} as oi
where oi.created_at < timestamp('{{ var("as_of") }}')
