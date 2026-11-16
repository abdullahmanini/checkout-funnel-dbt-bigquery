-- One row per clickstream event. Drops future-dated rows and parses the product id out of the URI.
with source as (
    select * from {{ source('thelook', 'events') }}
),

deduped as (
    select *
    from source
    qualify row_number() over (partition by id order by created_at) = 1
)

select
    id as event_id,
    session_id,
    sequence_number,
    user_id,
    user_id is null as is_anonymous,
    coalesce(cast(user_id as string), session_id) as visitor_id,
    event_type,
    created_at as event_at,
    lower(browser) as browser,
    lower(traffic_source) as traffic_source,
    safe_cast(regexp_extract(uri, r'^/product/(\d+)$') as int64) as product_id
from deduped
where created_at < timestamp('{{ var("as_of") }}')
