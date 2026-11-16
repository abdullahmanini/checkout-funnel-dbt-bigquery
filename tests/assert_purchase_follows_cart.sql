-- A purchase can't come before the cart add in the same session.
select session_id
from {{ ref('fct_checkout_funnel') }}
where purchased and (first_cart_at is null or purchase_at < first_cart_at)
