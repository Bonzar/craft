-- Run from this directory: MEMORY_MONIUM_LANE=test lua monium-redact-test.lua
dofile("monium-redact.lua")
local function good()
    return {event="memory_query_timing", schema_version=1, trace_id=string.rep("a",32),
        run_id="aabbccdd-0123-4567-8899-aabbccddeeff",
        stage="readiness_check", status="ok", model_phase="readiness_check",
        duration_ms=630266, prompt="must not export", MESSAGE="must not export",
        authorization="must not export", call_id="not a UUID"}
end
local code, ts, out = project_memory_query_timing("memory.query.timing",123,good())
assert(code == 2 and ts == 123 and out.environment == "test")
assert(out.duration_ms == 630266 and out.stage == "readiness_check")
assert(out.run_id == "aabbccdd-0123-4567-8899-aabbccddeeff")
assert(not out.prompt and not out.MESSAGE and not out.authorization and not out.call_id)
for _, pair in ipairs({{"event","other"}, {"schema_version",2}, {"trace_id","bad"},
    {"stage","private data"}, {"status","raw error"}}) do
    local value=good()
    value[pair[1]]=pair[2]
    assert(project_memory_query_timing("memory.query.timing",123,value) == -1)
end
local fake=good()
fake.duration_ms="prompt"
fake.signal_count=-1
fake.model_phase="raw text"
local _,_,filtered=project_memory_query_timing("memory.query.timing",123,fake)
assert(not filtered.duration_ms and not filtered.signal_count and not filtered.model_phase)
for _,stage in ipairs({"answer_check_write","answer_persist","model_step_replay","query_response_wait",
    "ready_result_persist","relation_frontier","relation_neighborhood","sufficiency"}) do
    local sample=good()
    sample.stage=stage
    sample.exception="must not export"
    sample.request_body="must not export"
    local code,_,projected=project_memory_query_timing("memory.query.timing",123,sample)
    assert(code==2, "expected timing stage: "..stage)
    assert(projected.stage==stage and projected.message=="memory_query_timing")
    assert(not projected.exception and not projected.request_body and not projected.MESSAGE)
end
local impersonated=good()
impersonated.stage="sufficiency.payload"
assert(project_memory_query_timing("memory.query.timing",123,impersonated)==-1)
