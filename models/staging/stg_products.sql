select
    id as product_id,
    lower(category) as category,
    brand,
    lower(department) as department,
    round(cast(retail_price as numeric), 2) as retail_price,
    round(cast(cost as numeric), 2) as cost
from {{ source('thelook', 'products') }}
