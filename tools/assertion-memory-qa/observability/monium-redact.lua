-- Do not return the incoming record, even after the parser drops journald fields.
local function values(str)
    local set = {}
    for v in str:gmatch("%S+") do set[v] = true end
    return set
end
local stages = values([[
    submission ready_probe queue_wait job_total result_persist query_total
    release_lease service_setup snapshot search raw_search answer answer_payload
    diagnostics usage_accounting provider_attempt provider_setup provider_cli
    provider_network_wait provider_response_read provider_decode
    provider_retry_signal trace_request_write trace_response_write navigation
    draft_generation readiness_check answer_verification other_model gap_search
    answer_neighborhood model_contract_validation query_outcome vm_http_submission
]])
local statuses = values("start ok failed timeout interrupted partial ready queued reused miss unavailable connection_signal retry_signal")
local phases = values("navigation draft_generation readiness_check answer_verification other_model")
local lane = os.getenv("MEMORY_MONIUM_LANE")

local function hex(v, n)
    return type(v) == "string" and #v == n and v:match("^[0-9a-f]+$")
end

local function uuid(v)
    if type(v) ~= "string" then return nil end
    local a,b,c,d,e = v:match("^([^%-]+)%-([^%-]+)%-([^%-]+)%-([^%-]+)%-([^%-]+)$")
    return hex(a,8) and hex(b,4) and hex(c,4) and hex(d,4) and hex(e,12)
end

local function count(v)
    return type(v) == "number" and v >= 0 and v <= 9007199254740991 and v % 1 == 0
end

function project_memory_query_timing(tag, timestamp, record)
    if (lane ~= "test" and lane ~= "prod") or record.event ~= "memory_query_timing" or
          record.schema_version ~= 1 or not hex(record.trace_id, 32) or
          not stages[record.stage] or not statuses[record.status] then
        return -1, timestamp, record
    end
    local result = {message = "memory_query_timing", event = "memory_query_timing",
        schema_version = 1, stage = record.stage, status = record.status,
        trace_id = record.trace_id, environment = lane}
    for _, k in ipairs({"run_id", "call_id"}) do
        if uuid(record[k]) then result[k] = record[k] end
    end
    for _, k in ipairs({"duration_ms", "signal_count"}) do
        if count(record[k]) then result[k] = record[k] end
    end
    if phases[record.model_phase] then result.model_phase = record.model_phase end
    return 2, timestamp, result
end
