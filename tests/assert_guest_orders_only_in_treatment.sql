-- The simulated feature must never leak into the control arm.
select visitor_id
from {{ ref('mart_experiment_visitors') }}
where guest_orders > 0 and (arm != 'treatment' or not is_anonymous)
