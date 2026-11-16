-- Session-to-order matching is by user, product and nearest time. Flag matches more than 10 minutes apart.
{{ config(severity = 'warn') }}
select session_id, match_gap_seconds
from {{ ref('int_session_orders') }}
where match_gap_seconds > 600
