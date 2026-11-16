-- Every purchase session must match one order line, or GMV is undercounted.
select session_id
from {{ ref('fct_checkout_funnel') }}
where purchased and order_item_id is null
