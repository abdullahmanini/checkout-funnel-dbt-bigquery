-- Rolls each session's events up to one row, using window functions to find the step order,
-- the product that was added to cart, and the time between steps.
with events as (
    select * from {{ ref('stg_events') }}
),

ordered as (
    select
        *,
        lag(event_type) over w as prev_event_type,
        lead(event_type) over w as next_event_type,
        row_number() over w as step_index
    from events
    window w as (partition by session_id order by sequence_number)
)

select
    session_id,
    any_value(visitor_id) as visitor_id,
    any_value(user_id) as user_id,
    logical_or(is_anonymous) as is_anonymous,
    any_value(browser) as browser,
    any_value(traffic_source) as traffic_source,
    min(event_at) as session_start_at,
    count(*) as event_count,
    min(if(event_type = 'product', event_at, null)) as first_product_at,
    min(if(event_type = 'cart', event_at, null)) as first_cart_at,
    min(if(event_type = 'purchase', event_at, null)) as purchase_at,
    logical_or(event_type = 'cancel') as has_cancel,
    min(if(event_type = 'cart', step_index, null)) as cart_step_index,
    -- a product view whose next event is a cart add is the carted product
    array_agg(if(next_event_type = 'cart', product_id, null) ignore nulls order by sequence_number limit 1)[safe_offset(0)] as carted_product_id,
    array_agg(product_id ignore nulls order by sequence_number limit 1)[safe_offset(0)] as first_product_id,
    array_agg(distinct product_id ignore nulls) as viewed_product_ids,
    countif(event_type = 'product' and prev_event_type = 'cart') as returns_to_browse_after_cart
from ordered
group by session_id
