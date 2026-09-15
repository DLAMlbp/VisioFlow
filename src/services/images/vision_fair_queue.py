"""Atomic, opt-in admission order over the existing provider capacity keys.

Only waiting attempts live here. RPM and in-flight leases remain in the legacy
keys so rollback cannot reset provider usage. All participating workers must use
the same mode; legacy workers cannot respect this queue's admission order.
"""

WAITER_LEASE_MS = 15_000
AGE_PRIORITY_MS = 15_000
MAX_WAITERS = 256
STAGE_GRANT_SEQUENCE = (1, 1, 1, 2, 3, 4)


def operation_stage(operation: str) -> int:
    if operation in ("completion_classification", "standard_classification", "routed_filter"):
        return 1
    if operation == "beautify_planning":
        return 2
    if operation in ("content_analysis", "content_analysis_batch"):
        return 3
    return 4


# KEYS: rate, inflight, cooldown, capacity, order, expires, stages, arrived,
# cursor, sequence, groups, last_group. ARGV shares the legacy first six
# arguments, followed by stage, group, waiter lease, aging threshold and queue
# bound. The weighted grant sequence gives classification three turns while
# keeping every downstream stage live and work-conserving.
FAIR_ACQUIRE_SCRIPT = """
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local rate_limit = tonumber(ARGV[3])
local configured_limit = tonumber(ARGV[4])
local lease_ms = tonumber(ARGV[5])
local token = ARGV[6]
local stage = tonumber(ARGV[7])
local group = ARGV[8]
local waiter_lease_ms = tonumber(ARGV[9])
local age_ms = tonumber(ARGV[10])
local max_waiters = tonumber(ARGV[11])
local concurrency_limit = math.min(configured_limit,
    tonumber(redis.call('GET', KEYS[4]) or configured_limit))

local function remove_waiter(id)
  redis.call('ZREM', KEYS[5], id)
  redis.call('ZREM', KEYS[6], id)
  redis.call('HDEL', KEYS[7], id)
  redis.call('HDEL', KEYS[8], id)
  redis.call('HDEL', KEYS[11], id)
end

for _, id in ipairs(redis.call('ZRANGEBYSCORE', KEYS[6], '-inf', now_ms)) do
  remove_waiter(id)
end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms - window_ms)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now_ms)
local rate_count = redis.call('ZCARD', KEYS[1])
local concurrency_count = redis.call('ZCARD', KEYS[2])

-- A retried EVAL after a lost response must not consume capacity twice.
if redis.call('ZSCORE', KEYS[2], token) then
  return {1, 0, rate_count, concurrency_count, 0, 0, 0, 0}
end
if redis.call('ZSCORE', KEYS[1], token) then
  return {-1, 0, rate_count, concurrency_count, 0, 0, 0, 0}
end
if not redis.call('ZSCORE', KEYS[5], token) then
  if redis.call('ZCARD', KEYS[5]) >= max_waiters then
    return {-1, 0, rate_count, concurrency_count, 0, 0, 0, 0}
  end
  local sequence = redis.call('INCR', KEYS[10])
  redis.call('ZADD', KEYS[5], sequence, token)
  redis.call('HSET', KEYS[7], token, stage)
  redis.call('HSET', KEYS[8], token, now_ms)
  redis.call('HSET', KEYS[11], token, group)
end
redis.call('ZADD', KEYS[6], now_ms + waiter_lease_ms, token)
for i = 5, 12 do
  redis.call('PEXPIRE', KEYS[i], waiter_lease_ms * 4)
end

local ordered = redis.call('ZRANGE', KEYS[5], 0, -1)
local selected = nil
local selected_cursor = nil
local oldest = ordered[1]
if oldest and now_ms - tonumber(redis.call('HGET', KEYS[8], oldest)) >= age_ms then
  selected = oldest
else
  local grants = {1, 1, 1, 2, 3, 4}
  local previous = tonumber(redis.call('GET', KEYS[9]) or '0')
  local last_group = redis.call('GET', KEYS[12])
  for offset = 1, 6 do
    local cursor = (previous + offset - 1) % 6 + 1
    local wanted_stage = grants[cursor]
    local fallback = nil
    for _, id in ipairs(ordered) do
      if tonumber(redis.call('HGET', KEYS[7], id)) == wanted_stage then
        if not fallback then fallback = id end
        if redis.call('HGET', KEYS[11], id) ~= last_group then
          selected = id
          break
        end
      end
    end
    if not selected then selected = fallback end
    if selected then
      selected_cursor = cursor
      break
    end
  end
end

local cooldown_ms = math.max(0, tonumber(redis.call('GET', KEYS[3]) or '0') - now_ms)
local cooldown = cooldown_ms > 0 and 1 or 0
local rate_blocked = rate_count >= rate_limit and 1 or 0
local concurrency_blocked = concurrency_count >= concurrency_limit and 1 or 0
local fairness_blocked = selected ~= token and 1 or 0
if cooldown + rate_blocked + concurrency_blocked + fairness_blocked == 0 then
  local selected_group = redis.call('HGET', KEYS[11], token)
  remove_waiter(token)
  if selected_cursor then
    redis.call('SET', KEYS[9], selected_cursor, 'PX', waiter_lease_ms * 4)
  end
  if selected_group then
    redis.call('SET', KEYS[12], selected_group, 'PX', waiter_lease_ms * 4)
  end
  redis.call('ZADD', KEYS[1], now_ms, token)
  redis.call('PEXPIRE', KEYS[1], window_ms + 5000)
  redis.call('ZADD', KEYS[2], now_ms + lease_ms, token)
  redis.call('PEXPIRE', KEYS[2], lease_ms + 5000)
  return {1, 0, rate_count + 1, concurrency_count + 1, 0, 0, 0, 0}
end
-- Short polling promptly notices releases; heartbeat keeps queued attempts live.
return {0, cooldown_ms > 0 and math.min(cooldown_ms, 1000) or 250,
        rate_count, concurrency_count, cooldown, rate_blocked,
        concurrency_blocked, fairness_blocked}
"""


CANCEL_WAIT_SCRIPT = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
redis.call('HDEL', KEYS[3], ARGV[1])
redis.call('HDEL', KEYS[4], ARGV[1])
redis.call('HDEL', KEYS[5], ARGV[1])
-- Also cover a granted EVAL whose response was lost. Never refund RPM usage.
redis.call('ZREM', KEYS[6], ARGV[1])
return 1
"""
